#!/usr/bin/env python3
"""Transactional RC16 cancellation-state cleanup update for existing RC16.

This is NOT a fresh/RC15 installer. It replaces one exactly recognised
coordinator only after the virtual-SD file is unloaded and the printer is
idle, with verified snapshot, rollback and Klipper readiness checks.
Normal cancellations after this one-time update require NO Klipper restart.
"""
import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

HOME = Path('/home/pi')
SOURCE = 'src/auto_xy_ratos_handoff_rc16.py'
TARGET = 'auto_xy/klipper/auto_xy_ratos_handoff_rc16.py'
OLD_BLOB = '3471e280972ab95df74680c5baaeaec299f46ba0'
NEW_BLOB = 'a9e4d1e0451fa704015ec4b97966a778820b38a7'
GATE_BLOB = '6df74b61f3f651b05b9d36a5fed12d4ff2c427f0'
STATUS_URL = (
    'http://127.0.0.1:7125/printer/objects/query?'
    'print_stats&virtual_sdcard&gcode_macro%20_VAOC&'
    'auto_xy_ratos_handoff_rc16&save_variables'
)


class Refuse(RuntimeError):
    pass


def require(ok, message):
    if not ok:
        raise Refuse(message)


def blob(data):
    return hashlib.sha1(
        b'blob ' + str(len(data)).encode('ascii') + b'\0' + data
    ).hexdigest()


def status():
    with urlopen(STATUS_URL, timeout=8) as response:
        packet = json.load(response)
    require('error' not in packet, 'Moonraker status query returned error')
    return packet['result']['status']


def require_idle(data):
    stats = data.get('print_stats')
    sd = data.get('virtual_sdcard')
    vaoc = data.get('gcode_macro _VAOC')
    handoff = data.get('auto_xy_ratos_handoff_rc16')
    saved = data.get('save_variables')
    variables = saved.get('variables') if isinstance(saved, dict) else None
    require(
        isinstance(stats, dict)
        and stats.get('state') in ('standby', 'complete', 'cancelled')
        and isinstance(sd, dict)
        and sd.get('is_active') is False
        and sd.get('file_path') in (None, '')
        and sd.get('filename') in (None, '')
        and isinstance(vaoc, dict)
        and vaoc.get('is_started') is False
        and isinstance(handoff, dict)
        and handoff.get('armed') is True
        and handoff.get('busy') is False
        and isinstance(variables, dict)
        and str(variables.get('auto_xy_v2_fault', 0)).lower()
        not in ('1', '1.0', 'true')
        and str(variables.get('auto_xy_v2_in_progress', 0)).lower()
        not in ('1', '1.0', 'true'),
        'Printer not fully idle/unloaded, VAOC active or AutoXY fault; '
        'do not replace RC16 while a print could be recovered'
    )


def checked_files(repo, home):
    repo = Path(repo)
    home = Path(home)
    parent = home / 'auto_xy/klipper'
    source = repo / SOURCE
    installed = home / TARGET
    gate = home / 'auto_xy/klipper/auto_xy_gate.py'
    require(parent.is_dir() and not parent.is_symlink(),
            'Untrusted AutoXY controller directory')
    require(source.is_file() and not source.is_symlink(),
            'Missing trusted RC16 update source')
    require(blob(source.read_bytes()) == NEW_BLOB,
            'RC16 update source differs from reviewed commit')
    require(installed.is_file() and not installed.is_symlink(),
            'Expected regular installed RC16 handoff file')
    require(gate.is_file() and not gate.is_symlink()
            and blob(gate.read_bytes()) == GATE_BLOB,
            'Installed RC16 gate is not the reviewed telemetry version')
    prior = installed.read_bytes()
    actual = blob(prior)
    require(actual in (OLD_BLOB, NEW_BLOB),
            'Installed handoff differs from exact reviewed RC16 versions')
    return installed, prior, source.read_bytes(), actual == NEW_BLOB


def atomic_replace(path, data, mode):
    fd, tmp = tempfile.mkstemp(prefix='.rc16-cancel-', dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def restart():
    subprocess.run(('sudo', 'systemctl', 'restart', 'klipper'),
                   check=True, timeout=120)


def verify_ready():
    import rc16_vision_refresh
    rc16_vision_refresh.ready()


def install(repo, home, *, live=True, restart_service=restart,
            wait_ready=verify_ready):
    path, old, new, already = checked_files(repo, home)
    if already:
        return 'already-installed', None
    if live:
        require_idle(status())
    rollbacks = Path(home) / 'auto_xy/rollbacks'
    require(rollbacks.is_dir() and not rollbacks.is_symlink(),
            'Unsafe or missing AutoXY rollback directory')
    snapshot = Path(tempfile.mkdtemp(prefix='rc16-cancel-cleanup-',
                                     dir=str(rollbacks)))
    previous = snapshot / 'previous_handoff.py'
    previous.write_bytes(old)
    os.chmod(previous, 0o600)
    require(blob(previous.read_bytes()) == OLD_BLOB,
            'Previous-file snapshot verification failed')
    receipt = {
        'target': str(path), 'old_git_blob': OLD_BLOB,
        'new_git_blob': NEW_BLOB, 'snapshot': str(previous)
    }
    (snapshot / 'receipt.json').write_text(
        json.dumps(receipt, indent=2), encoding='utf-8')
    installed = False
    mode = stat.S_IMODE(path.stat().st_mode)
    try:
        if live:
            require_idle(status())
        require(blob(path.read_bytes()) == OLD_BLOB,
                'Installed handoff changed since preflight')
        atomic_replace(path, new, mode)
        installed = True
        require(blob(path.read_bytes()) == NEW_BLOB,
                'Post-update handoff source hash mismatch')
        if live:
            restart_service()
            wait_ready()
            require_idle(status())
        return 'updated', snapshot
    except BaseException as error:
        if not installed:
            raise Refuse('Update refused before changing installed RC16: %s' %
                         error) from error
        try:
            require(path.is_file() and not path.is_symlink()
                    and blob(path.read_bytes()) == NEW_BLOB,
                    'Installed file changed since update; automatic rollback refused')
            require(blob(previous.read_bytes()) == OLD_BLOB,
                    'Snapshot changed; automatic rollback refused')
            atomic_replace(path, previous.read_bytes(), mode)
            require(blob(path.read_bytes()) == OLD_BLOB,
                    'Rollback did not restore exact original bytes')
            if live:
                restart_service()
                wait_ready()
        except BaseException as rollback_error:
            raise Refuse(
                'UPDATE AND ROLLBACK FAILED; DO NOT PRINT. Snapshot %s; '
                'errors: %s / %s' % (snapshot, error, rollback_error)
            ) from rollback_error
        raise Refuse(
            'Update failed; original handoff restored. Snapshot %s; reason %s'
            % (snapshot, error)
        ) from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'install'))
    opts = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        _, _, _, already = checked_files(root, HOME)
        require_idle(status())
        print('RC16 CANCEL CLEANUP PREFLIGHT PASS:',
              'already installed' if already else 'recognized RC16 handoff')
        if opts.action == 'check' or already:
            return 0
        import rc16_active_install
        rc16_active_install.authorize_restart()
        result, backup = install(root, HOME)
        print('RC16 cancellation cleanup:', result)
        print('Rollback snapshot:', backup)
        return 0
    except (OSError, ValueError, KeyError, Refuse,
            subprocess.SubprocessError) as error:
        print('RC16 CANCEL CLEANUP REFUSED:', error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
