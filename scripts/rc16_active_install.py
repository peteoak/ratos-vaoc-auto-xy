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
from urllib.request import urlopen

import rc16_stage_upgrade as upgrade

HOME = Path('/home/pi')
INFO_URL = 'http://127.0.0.1:7125/printer/info'
CONFIG_URL = ('http://127.0.0.1:7125/printer/objects/query?'
              'configfile&auto_xy_ratos_handoff_rc16')
RESTART_COMMAND = ('sudo', '-n', 'systemctl', 'restart', 'klipper')


class InstallationError(RuntimeError):
    pass


def restart_klipper():
    subprocess.run(RESTART_COMMAND, check=True, timeout=45,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def query_json(url):
    with urlopen(url, timeout=5) as response:
        return json.load(response)


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
    receipt = upgrade.stage(home, changes)
    try:
        if live:
            restart()
            verify()
    except BaseException as error:
        # This restores the previous gate, config, printer include and RC16
        # extras using the transaction's exact content-addressed snapshot.
        try:
            upgrade.rollback(home, receipt, live=False)
            if live:
                restart()
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
