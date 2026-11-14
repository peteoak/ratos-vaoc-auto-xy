#!/usr/bin/env python3
"""RC16 active-only upgrade orchestrator (development, not hardware qualified).

No disabled staging or manual activation step: check, backup, install, restart,
verify, and automatically restore the prior state on failure. Do NOT deploy
until RC16's physical qualification and native print interception tests pass.
"""
import argparse
import json
import os
import math
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

import rc16_stage_upgrade as upgrade

HOME = Path('/home/pi')
INFO_URL = 'http://127.0.0.1:7125/printer/info'
CONFIG_URL = ('http://127.0.0.1:7125/printer/objects/query?'
              'configfile&auto_xy_ratos_handoff_rc16&auto_xy_recommission_rc16&save_variables')
SAVED_URL = 'http://127.0.0.1:7125/printer/objects/query?save_variables'
RESTART_COMMAND = ('sudo', 'systemctl', 'restart', 'klipper')
GCODE_URL = 'http://127.0.0.1:7125/printer/gcode/script'


class InstallationError(RuntimeError):
    pass


MIGRATED_FLAGS = (
    'auto_xy_v2_enabled', 'auto_xy_v2_measure_only',
    'auto_xy_v2_fault', 'auto_xy_v2_in_progress',
    'auto_xy_v2_retry_available', 'auto_xy_v2_retry_used',
)
SAFETY_FLAGS = (
    'auto_xy_v2_fault', 'auto_xy_v2_in_progress',
    'auto_xy_v2_retry_available', 'auto_xy_v2_retry_used',
)
MIGRATION_ARCHIVE = 'pre-rc16-saved-state.json'


def authorize_restart():
    """Obtain sudo authentication from the operator BEFORE modifying files.

    No password is collected by AutoXY or written to installation logs.
    sudo uses the SSH controlling TTY and its normal credential cache.
    Refuse a noninteractive session before a rollback could depend on sudo.
    """
    try:
        with open('/dev/tty', 'rb') as tty:
            if not os.isatty(tty.fileno()):
                raise InstallationError('Interactive SSH terminal required for sudo')
            subprocess.run(('sudo', '-v'), stdin=tty, check=True, timeout=120,
                           stdout=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise InstallationError(
            'Klipper restart authorization failed. Run from interactive SSH '
            'and enter your sudo password when prompted; no files changed.'
        ) from exc


def restart_klipper():
    """Allow sudo to prompt on /dev/tty again if the credential expires."""
    try:
        subprocess.run(RESTART_COMMAND, check=True, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise InstallationError(
            'Klipper service restart failed (sudo or systemctl): %s' % exc
        ) from exc


def query_json(url):
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def wait_for_klipper_ready(timeout=120, sleep=time.sleep):
    """Wait through Moonraker's 503 gap after a Klipper service restart.

    A successful systemctl return does not mean Moonraker has reconnected.
    Require both printer/info READY and readable saved variables before any
    activation, stale-state migration or rollback state restoration.
    Retry only transient transport/incomplete-status failures; reject a
    confirmed Klipper error/shutdown immediately. Never issue motion/G-code.
    """
    deadline = time.monotonic() + timeout
    last = 'Moonraker has not reconnected to Klipper'
    while True:
        try:
            info = query_json(INFO_URL).get('result', {})
            state = str(info.get('state', '')).strip().lower()
            if state in ('error', 'shutdown'):
                raise InstallationError(
                    'Klipper entered %s during restart: %s' %
                    (state, info.get('state_message', 'no details')))
            if state == 'ready':
                status = query_json(SAVED_URL)['result']['status']
                variables = status.get('save_variables', {}).get('variables')
                if isinstance(variables, dict):
                    return
                last = 'Klipper ready, saved variables not yet readable'
            else:
                last = 'Klipper state: ' + (state or 'unavailable')
        except InstallationError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            last = '%s: %s' % (type(error).__name__, error)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sleep(min(2, remaining))
    raise InstallationError(
        'Klipper did not become Ready after restart within %ss: %s' %
        (timeout, last))


def post_gcode(script):
    """Post only fixed, reviewed housekeeping macros to Moonraker."""
    allowed = {'AUTO_XY_ON'}
    for key in MIGRATED_FLAGS:
        for value in (0, 1):
            allowed.add('SAVE_VARIABLE VARIABLE=%s VALUE=%d' % (key, value))
    if script not in allowed:
        raise InstallationError('Disallowed installer G-code')
    data = json.dumps({'script': script}).encode('utf-8')
    request = Request(GCODE_URL, data=data,
                      headers={'Content-Type': 'application/json'},
                      method='POST')
    with urlopen(request, timeout=10) as response:
        result = json.load(response)
    if 'error' in result:
        raise InstallationError('Klipper rejected AutoXY installation command')


def read_migration_state():
    """Read every managed RC15 flag, including latches; never silently discard them."""
    status = query_json(SAVED_URL)['result']['status']
    variables = status.get('save_variables', {}).get('variables', {})
    if not isinstance(variables, dict):
        raise InstallationError('Saved AutoXY variables are unavailable')
    if 'auto_xy_v2_enabled' not in variables:
        raise InstallationError('AutoXY saved enabled state missing')
    original = {}
    for key in MIGRATED_FLAGS:
        raw = str(variables.get(key, 0)).strip().lower()
        if raw not in ('0', '0.0', 'false', '1', '1.0', 'true'):
            raise InstallationError('Unexpected saved AutoXY state: ' + key)
        original[key] = int(raw in ('1', '1.0', 'true'))
    offsets = {}
    for key in ('idex_xoffset', 'idex_yoffset', 'idex_zoffset'):
        if key not in variables:
            raise InstallationError('Missing saved nozzle offset: ' + key)
        try:
            value = float(variables[key])
        except (TypeError, ValueError) as exc:
            raise InstallationError('Invalid saved nozzle offset: ' + key) from exc
        if not math.isfinite(value):
            raise InstallationError('Nonfinite saved nozzle offset: ' + key)
        offsets[key] = variables[key]
    return {'flags': original, 'offsets': offsets}


def saved_state():
    state = read_migration_state()['flags']
    for key in SAFETY_FLAGS:
        if state[key]:
            raise InstallationError('AutoXY unresolved state: ' + key)
    return bool(state['auto_xy_v2_enabled']), bool(state['auto_xy_v2_measure_only'])


def archive_migration_state(receipt, state):
    """Durably record previous software state BEFORE resetting any latch."""
    if not isinstance(state, dict) or set(state.get('flags', {})) != set(MIGRATED_FLAGS):
        raise InstallationError('Incomplete original AutoXY state; no reset permitted')
    target = Path(receipt) / MIGRATION_ARCHIVE
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump({'schema': 1, 'state': state}, out, indent=2, sort_keys=True)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def set_migration_flag(key, value):
    if key not in MIGRATED_FLAGS or value not in (0, 1):
        raise InstallationError('Invalid migration state variable')
    post_gcode('SAVE_VARIABLE VARIABLE=%s VALUE=%d' % (key, value))


def reset_stale_flags(original):
    """Clear only obsolete latches after installation, leaving offsets untouched."""
    current = read_migration_state()
    if current['offsets'] != original['offsets'] or current['flags'] != original['flags']:
        raise InstallationError(
            'Saved state changed during upgrade; refusing reset and restoring installation')
    old = original['flags']
    if any(old[key] for key in SAFETY_FLAGS):
        # Prior printer state was audited by the RC15 live preflight.
        # Recheck no file is held and no print is running after Klipper restart.
        import install_autoxy
        status, info = install_autoxy.moonraker_status()
        if info.get('state') != 'ready':
            raise InstallationError('Klipper unavailable before stale-flag reset')
        sd = status.get('virtual_sdcard', {})
        stats = status.get('print_stats', {})
        if (sd.get('is_active') is not False
                or sd.get('file_path') or sd.get('filename')
                or str(stats.get('state', '')).lower()
                   not in ('standby', 'complete', 'cancelled')):
            raise InstallationError('Possible held print; refusing stale flag reset')
    # Commit the one-shot retry/in-progress/fault latches before enabling
    # normal automatic correction. Any partial failure triggers rollback.
    for key in SAFETY_FLAGS:
        if old[key]:
            set_migration_flag(key, 0)


def restore_migration_state(original):
    """Restore exact prior boolean flags even when RC15 AUTO_XY_ON refuses faults."""
    if original is None:
        return
    for key, value in original['flags'].items():
        set_migration_flag(key, value)
    # idex_xyz offsets are never modified by this transaction.



def activate_persistent_gate(fresh=False):
    """Enable after handoff is armed; new installations may lack saved flags."""
    info = query_json(INFO_URL)['result']
    status = query_json(CONFIG_URL)['result']['status']
    if info.get('state') != 'ready':
        raise InstallationError('Cannot activate while Klipper is not ready')
    settings = status.get('configfile', {}).get('settings', {})
    if (settings.get('auto_xy_gate', {}).get('protected_print_enabled') is not True
            or settings.get('auto_xy_ratos_handoff_rc16', {}).get('enabled') is not True
            or status.get('auto_xy_ratos_handoff_rc16', {}).get('armed') is not True
            or status.get('auto_xy_ratos_handoff_rc16', {}).get('sd_api_guarded') is not True
            or status.get('auto_xy_ratos_handoff_rc16', {}).get('resume_api_guarded') is not True
            or status.get('auto_xy_recommission_rc16', {}).get('ready') is not True):

        raise InstallationError('Cannot enable before native print protection is armed')
    # Pre-existing installations must preserve and validate every saved
    # safety marker. Fresh installs have no AutoXY flags until AUTO_XY_ON
    # creates them; the shared signed baseline preflight has already checked
    # that no prior faults/held print are present.
    if fresh:
        post_gcode('AUTO_XY_ON')
    else:
        enabled, measure_only = saved_state()
        # Active operation must correct/verify, not silently stay measure-only.
        if not enabled or measure_only:
            post_gcode('AUTO_XY_ON')


def restore_saved_state(original):
    """Legacy test helper; restoration now uses the full saved-state snapshot."""
    if original is None:
        return
    if isinstance(original, dict):
        restore_migration_state(original)
        return
    desired_enabled, desired_measure = original
    set_migration_flag('auto_xy_v2_enabled', int(desired_enabled))
    set_migration_flag('auto_xy_v2_measure_only', int(desired_measure))


def check_active_runtime(timeout=90, sleep=time.sleep):
    """Wait for loaded RC16 sections and Klipper Ready without G-code/motion."""
    deadline = time.monotonic() + timeout
    last = 'Klipper not responding'
    while time.monotonic() < deadline:
        try:
            info = query_json(INFO_URL).get('result', {})
            if info.get('state') == 'ready':
                status = query_json(CONFIG_URL)['result']['status']
                sections = status['configfile']['settings']
                gate = sections.get('auto_xy_gate', {})
                handoff = sections.get('auto_xy_ratos_handoff_rc16', {})
                if gate.get('protected_print_enabled') is not True:
                    raise InstallationError('Protected AutoXY gate is not enabled')
                if handoff.get('enabled') is not True:
                    raise InstallationError('RC16 native print-file handoff is not enabled')
                if handoff.get('retention_enabled') is not True:
                    raise InstallationError('Generated G-code cleanup is not enabled')
                coordinator = status.get('auto_xy_ratos_handoff_rc16', {})
                if coordinator.get('armed') is not True:
                    raise InstallationError('RC16 print-file processor is not armed')
                if coordinator.get('sd_api_guarded') is not True:
                    raise InstallationError('RC16 direct virtual-SD API guard is not active')
                if coordinator.get('resume_api_guarded') is not True:
                    raise InstallationError('RC16 direct virtual-SD resume guard is not active')
                recommission = status.get('auto_xy_recommission_rc16', {})
                if recommission.get('ready') is not True:
                    raise InstallationError('RC16 Mainsail nozzle recommissioning is not ready')
                if not any(str(name).casefold() ==
                           'gcode_macro auto_xy_recommission_nozzles'
                           for name in sections):
                    raise InstallationError('RC16 Recommission Nozzles Mainsail macro is missing')
                if not any(str(name).casefold() ==
                           'gcode_macro auto_xy_temperature'
                           for name in sections):
                    raise InstallationError('RC16 Mainsail thermal selector is missing')
                # The config flags alone do not enable AutoXY. The controller
                # also reads its persistent SAVE_VARIABLE state on each run.
                saved = status.get('save_variables', {}).get('variables', {})
                enabled = saved.get('auto_xy_v2_enabled')
                if str(enabled).strip().lower() not in ('1', '1.0', 'true'):
                    raise InstallationError('Persistent AutoXY calibration enable is OFF')
                measure_only = saved.get('auto_xy_v2_measure_only', 0)
                if str(measure_only).strip().lower() not in ('0', '0.0', 'false'):
                    raise InstallationError('AutoXY measure-only mode remains enabled')
                for key in ('auto_xy_v2_fault', 'auto_xy_v2_in_progress'):
                    if str(saved.get(key, 0)).strip().lower() not in ('0', '0.0', 'false'):
                        raise InstallationError('AutoXY has an unresolved safety fault: ' + key)
                return
            last = 'Klipper state: ' + str(info.get('state'))
        except InstallationError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            last = type(exc).__name__
        sleep(2)
    raise InstallationError('RC16 could not be verified after restart: ' + last)


def install(home, root, *, live, restart=restart_klipper,
            verify=check_active_runtime):
    """Transactional filesystem migration; automatic service rollback if needed."""
    variant, changes = upgrade.check_active_installation(home, root, live=live)
    original_flags = read_migration_state() if live else None
    receipt = upgrade.stage(home, changes)
    try:
        if live:
            archive_migration_state(receipt, original_flags)
            restart()
            wait_for_klipper_ready()
            # Only after RC16 is loaded, verify the handoff before mutating
            # any saved flags. An idle no-held-print check still applies.
            reset_stale_flags(original_flags)
            activate_persistent_gate()
            verify()
    except BaseException as error:
        try:
            upgrade.rollback(home, receipt, live=False)
            if live:
                restart()
                wait_for_klipper_ready()
                restore_saved_state(original_flags)
        except BaseException as rollback_error:
            raise InstallationError(
                'RC16 failed and rollback/restart also failed. '
                'Do not start a print; rollback snapshot: %s; errors: %s / %s'
                % (receipt, error, rollback_error)) from rollback_error
        raise InstallationError('RC16 failed verification; prior installation '
                                'restored using snapshot %s: %s' %
                                (receipt, error)) from error
    return variant, receipt


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--check', action='store_true',
                    help='Run read-only readiness and migration planning')
    ap.add_argument('--home', type=Path, default=HOME, help=argparse.SUPPRESS)
    ap.add_argument('--offline', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    home = args.home.absolute()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.offline:
            upgrade.require(home != HOME, 'Offline testing must not target /home/pi')
        else:
            upgrade.require(home == HOME, 'Live installation supports only /home/pi')
        if args.check:
            variant, changes = upgrade.check_active_installation(
                home, root, live=not args.offline)
            print('RC16 active migration ready for', variant,
                  '; planned paths:', len(changes), '; no files changed')
            return 0
        variant, receipt = install(home, root, live=not args.offline)
        print('RC16 active installation completed:', variant)
        print('Full protected-print/retention configuration verified by Klipper.')
        print('Rollback snapshot:', receipt)
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print('RC16 active installation not completed:', error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
