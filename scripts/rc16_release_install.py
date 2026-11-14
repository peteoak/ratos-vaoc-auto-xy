#!/usr/bin/env python3
"""RC16 single-command ACTIVE installer for fresh RatOS and reviewed RC15.

One user-visible transaction, no staging toggle and no slicer modifications.
The existing signed RC15 installer supplies verified shared files and RatOS
extension registration during a fresh install. Klipper is restarted ONLY
after every RC16 active file has been installed.

Use only after completing the supervised release-candidate code audit.
"""
import argparse
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import install_autoxy as baseline
import ratos_extension
import rc16_active_install as active
import rc16_stage_upgrade as upgrade
import rc16_active_migration as migration
import vision_env

HOME = Path('/home/pi')
LOCK = Path('/tmp/autoxy-rc16-install.lock')
RECEIPT = 'rc16-active-fresh-receipt.json'


class ReleaseInstallError(RuntimeError):
    pass


def install_kind(home):
    """Fail closed for partial/unknown installations, never silently reinstall."""
    config = home / 'printer_data/config/auto_xy.cfg'
    gate = home / 'auto_xy/klipper/auto_xy_gate.py'
    if config.exists() or config.is_symlink() or gate.exists() or gate.is_symlink():
        if not config.is_file() or not gate.is_file():
            raise ReleaseInstallError('Incomplete AutoXY detected; inspect rather than overwrite')
        return 'upgrade'
    return 'fresh'


def preflight(home, root, *, live):
    kind = install_kind(home)
    if kind == 'fresh':
        # No intermediate disabled installation is presented to the operator.
        # The BASELINE preparation and RC16 conversion happen before restart.
        upgrade.verify_sources(root)
        baseline_files = baseline.plan(home, root, 'fresh')
        offsets = baseline.live_safe(home, 'fresh') if live else None
        return kind, baseline_files, offsets
    variant, changes = upgrade.check_active_installation(home, root, live=live)
    return kind, (variant, changes), None


def rollback_fresh(home, baseline_snapshot, rc16_snapshot, *, live):
    """Restore exact original bytes and unregister only our own RatOS extension."""
    if rc16_snapshot is not None:
        upgrade.rollback(home, rc16_snapshot, live=False)
    if live:
        expected = str(home / upgrade.OLD_GATE)
        state = ratos_extension.snapshot(home)
        if state['registered'] != expected or state['link'] != expected:
            raise ReleaseInstallError('RatOS registration differs; manual recovery required')
        ratos_extension.unregister(home, expected, expected)
    # After RatOS unregisters the baseline symlink, the exact fresh-install
    # snapshot needs to restore the originally absent link. Its ordinary
    # verify_installed=True branch would incorrectly expect that now-removed
    # symlink, so validate the snapshot and restore with link verification
    # already performed by ratos_extension.unregister above.
    manifest_path = Path(baseline_snapshot) / 'manifest.json'
    receipt = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (receipt.get('mode') != 'fresh' or
            receipt.get('home') != str(home) or
            not (home / 'auto_xy/rollbacks').resolve() ==
                Path(baseline_snapshot).resolve().parent):
        raise ReleaseInstallError('RC15 fresh rollback snapshot mismatched')
    baseline.restore(home, Path(baseline_snapshot), receipt,
                     verify_installed=False)


def install_fresh(home, root, *, live,
                  restart=active.restart_klipper,
                  verify=active.check_active_runtime,
                  activate=None):
    """Single active install with two exact snapshots and automatic rollback."""
    kind, changes, offsets = preflight(home, root, live=live)
    if kind != 'fresh':
        raise ReleaseInstallError('Fresh installer cannot overwrite an existing AutoXY')
    prior = None
    current = None
    try:
        prior = baseline.install(home, changes, 'fresh', offsets)
        # Already reviewed RC15 layout + registration, no Klipper restart
        # and no off-stage of the final RC16 operating configuration.
        _, prepared = upgrade.check_active_installation(home, root, live=False)
        current = upgrade.stage(home, prepared)
        # Persist receipt inside the exact RC16 rollback snapshot.
        receipt = {'schema': 1, 'mode': 'fresh-active', 'home': str(home),
                   'rc15_snapshot': str(prior), 'rc16_snapshot': str(current),
                   'version': upgrade.VERSION}
        with (current / RECEIPT).open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        if live:
            restart()
            active.wait_for_klipper_ready()
            # Fresh commissioning is required before real nozzle correction,
            # but protection is ACTIVE; no disabled or measure-only stage.
            if activate is None:
                active.activate_persistent_gate(fresh=True)
            else:
                activate()
            verify()
        return current
    except BaseException as failure:
        if prior is not None:
            try:
                rollback_fresh(home, prior, current, live=live)
                if live:
                    restart()
                    active.wait_for_klipper_ready()
                    # A fresh installation can create persistent AutoXY
                    # enable flags before its post-restart verification fails.
                    # Roll them back after the original Klipper is restored,
                    # otherwise the next fresh preflight sees stale state.
                    active.post_gcode(
                        'SAVE_VARIABLE VARIABLE=auto_xy_v2_enabled VALUE=0')
                    active.post_gcode(
                        'SAVE_VARIABLE VARIABLE=auto_xy_v2_measure_only VALUE=0')
            except BaseException as rollback_failure:
                raise ReleaseInstallError(
                    'RC16 fresh install failed AND rollback could not complete. '
                    'Do not start a print. Snapshots: %s / %s; errors: %s / %s'
                    % (prior, current, failure, rollback_failure)) from rollback_failure
        raise ReleaseInstallError(
            'RC16 fresh install refused or failed; previous state restored: %s'
            % failure) from failure


def install_complete(home, root, *, live,
                     restart=active.restart_klipper,
                     verify=active.check_active_runtime,
                     activate=None):
    """One entry point for a first install or a recognised RC15 migration."""
    kind = install_kind(home)
    if kind == 'fresh':
        receipt = install_fresh(home, root, live=live,
                                restart=restart, verify=verify,
                                activate=activate)
        return 'fresh-active', receipt
    _, package, _ = preflight(home, root, live=live)
    variant, prepared = package
    # Reuse the existing active migration, including one restart and exact
    # saved-state rollback. Offline simulation uses the same file transaction.
    if live:
        return active.install(home, root, live=True,
                              restart=restart, verify=verify)
    receipt = upgrade.stage(home, prepared)
    return variant, receipt


def prepare_vision(home, kind, *, live):
    """Provision only a completely absent standard venv; never overwrite."""
    if not live:
        return False
    standard = Path(home) / 'auto-xy-venv'
    interpreter = (migration.installed_camera_python(home) if kind == 'upgrade'
                   else standard / 'bin/python')
    if interpreter.is_file():
        if kind == 'fresh':
            result = subprocess.run([str(interpreter), '-c', 'import cv2,numpy'],
                                    capture_output=True, text=True, timeout=20)
            if result.returncode:
                raise ReleaseInstallError('Existing camera Python cannot import OpenCV/NumPy')
        return False
    if interpreter != standard / 'bin/python' or standard.exists() or standard.is_symlink():
        raise ReleaseInstallError('Camera Python missing/incomplete; refusing overwrite: %s' % interpreter)
    vision_env.install_new_venv(standard)
    return True


def install_complete_with_vision(home, root, *, live,
                                 restart=active.restart_klipper,
                                 verify=active.check_active_runtime,
                                 activate=None):
    created = False
    try:
        created = prepare_vision(home, install_kind(home), live=live)
        return install_complete(home, root, live=live, restart=restart,
                                verify=verify, activate=activate)
    except BaseException:
        if created:
            # An incomplete RC16 transaction may still need the environment.
            # Clean only after installer restored the previous printer include.
            cfg = Path(home) / 'printer_data/config/printer.cfg'
            if cfg.is_file() and 'auto_xy_rc16.cfg' not in cfg.read_text():
                env = Path(home) / 'auto-xy-venv'
                if env.is_dir() and not env.is_symlink():
                    shutil.rmtree(env)
        raise


@contextlib.contextmanager
def one_installer_only():
    with LOCK.open('a+') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReleaseInstallError('Another AutoXY installation is running') from exc
        yield


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('check', 'install'))
    ap.add_argument('--home', type=Path, default=HOME, help=argparse.SUPPRESS)
    ap.add_argument('--offline', action='store_true', help=argparse.SUPPRESS)
    opts = ap.parse_args(argv)
    home = opts.home.absolute()
    root = Path(__file__).resolve().parents[1]
    try:
        if opts.offline:
            upgrade.require(home != HOME, 'Offline testing cannot target live printer')
        else:
            upgrade.require(home == HOME, 'Real installs only support canonical /home/pi')
        with one_installer_only():
            if opts.action == 'check':
                kind, _, _ = preflight(home, root, live=not opts.offline)
                print('RC16 full active installation readiness: PASS (%s); no file changes' % kind)
                if not opts.offline:
                    standard = home / 'auto-xy-venv'
                    python = (migration.installed_camera_python(home) if kind == 'upgrade'
                              else standard / 'bin/python')
                    if python == standard / 'bin/python' and not python.is_file():
                        print('NOTICE: Installer will provision missing isolated camera venv from binary wheels')
                return 0
            # Sudo must be authorized before ANY printer or venv files change.
            # The interactive prompt goes directly to the SSH TTY, not the log.
            if not opts.offline:
                active.authorize_restart()
            kind, receipt = install_complete_with_vision(home, root, live=not opts.offline)
            print('RC16 single-transaction active installation COMPLETE:', kind)
            print('Rollback snapshot:', receipt)
            print('Protected print processor and Mainsail recommissioning loaded.')
            print('A previously uncommissioned camera requires manual native VAOC '
                  'nozzle commissioning before protected print correction.')
            return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print('RC16 INSTALL FAILED OR REFUSED:', str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
