#!/usr/bin/env python3
"""RC16 active-only upgrade orchestrator (development, not hardware qualified).

No disabled staging or manual activation step: check, backup, install, restart,
verify, and automatically restore the prior state on failure. Do NOT deploy
until RC16's physical qualification and native print interception tests pass.
"""
import argparse
import json
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
RESTART_COMMAND = ('sudo', '-n', 'systemctl', 'restart', 'klipper')
GCODE_URL = 'http://127.0.0.1:7125/printer/gcode/script'


class InstallationError(RuntimeError):
    pass


def restart_klipper():
    subprocess.run(RESTART_COMMAND, check=True, timeout=45,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def query_json(url):
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def post_gcode(script):
    """Post only fixed, reviewed housekeeping macros to Moonraker."""
    allowed = ('AUTO_XY_ON',
               'SAVE_VARIABLE VARIABLE=auto_xy_v2_enabled VALUE=0',
               'SAVE_VARIABLE VARIABLE=auto_xy_v2_measure_only VALUE=0',
               'SAVE_VARIABLE VARIABLE=auto_xy_v2_measure_only VALUE=1')
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


def saved_state():
    status = query_json(SAVED_URL)['result']['status']
    variables = status.get('save_variables', {}).get('variables', {})
    if 'auto_xy_v2_enabled' not in variables:
        raise InstallationError('AutoXY saved enabled state missing')
    for key in ('auto_xy_v2_fault', 'auto_xy_v2_in_progress',
                'auto_xy_v2_retry_available'):
        if str(variables.get(key, 0)).strip().lower() not in ('0', '0.0', 'false'):
            raise InstallationError('AutoXY unresolved state: ' + key)
    enabled = str(variables['auto_xy_v2_enabled']).strip().lower()
    measure = str(variables.get('auto_xy_v2_measure_only', 0)).strip().lower()
    if enabled not in ('0', '0.0', 'false', '1', '1.0', 'true'):
        raise InstallationError('Unexpected AutoXY enable value')
    if measure not in ('0', '0.0', 'false', '1', '1.0', 'true'):
        raise InstallationError('Unexpected AutoXY measure-only value')
    return (enabled in ('1', '1.0', 'true'),
            measure in ('1', '1.0', 'true'))


def activate_persistent_gate():
    """Enable AutoXY only after the RC16 processor is loaded and armed."""
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
    enabled, measure_only = saved_state()
    # Production AutoXY must CORRECT and verify, not silently remain in
    # observation-only commissioning mode from a previous installation.
    if not enabled or measure_only:
        post_gcode('AUTO_XY_ON')


def restore_saved_state(original):
    """Restore only the saved flags the installer may have changed."""
    if original is None:
        return
    desired_enabled, desired_measure = original
    current_enabled, current_measure = saved_state()
    if desired_enabled != current_enabled:
        if desired_enabled:
            post_gcode('AUTO_XY_ON')
        else:
            post_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_enabled VALUE=0')
    if desired_measure != current_measure:
        post_gcode('SAVE_VARIABLE VARIABLE=auto_xy_v2_measure_only VALUE=%d'
                   % int(desired_measure))


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
                if 'gcode_macro auto_xy_recommission_nozzles' not in sections:
                    raise InstallationError('RC16 Recommission Nozzles Mainsail macro is missing')
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
    original_flags = saved_state() if live else None
    receipt = upgrade.stage(home, changes)
    try:
        if live:
            restart()
            activate_persistent_gate()
            verify()
    except BaseException as error:
        # This restores the previous gate, config, printer include and RC16
        # extras using the transaction's exact content-addressed snapshot.
        try:
            upgrade.rollback(home, receipt, live=False)
            if live:
                restart()
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
