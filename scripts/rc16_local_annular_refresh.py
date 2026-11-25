#!/usr/bin/env python3
"""Single-file transactional RC16 vision correction for existing reviewed RC16.

Fixes false nozzle-rim rejections when an independent global coarse match
chooses residue/reflection instead of the precision Hough nozzle. This does
not modify any offsets, temperature calibration, G-code, or camera reference.
Printer must be idle, VAOC closed and no fault/held/retry pending. Requires
one Klipper service restart to load new Python, with automatic rollback.
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
SOURCE = 'src/auto_xy_detector_rc16.py'
TARGET = 'auto_xy/vision/auto_xy_detector_rc16.py'
OLD_BLOB = 'e44207a3da584961031280f6332a597326dc5e63'
NEW_BLOB = '2ab349d22b241e2962fc8a2972b2317fac76cfe1'
GATE_BLOB = 'f5649a22d754c3a4901faf59d7af73440dde1c0f'
HANDOFF_BLOB = '53b1674499aac78fdbe48563c1a89f7dd11600f4'
STATUS_URL = (
    'http://127.0.0.1:7125/printer/objects/query?'
    'print_stats&virtual_sdcard&gcode_macro%20_VAOC&'
    'auto_xy_ratos_handoff_rc16&save_variables'
)


class Refuse(RuntimeError):
    pass


def require(ok, reason):
    if not ok:
        raise Refuse(reason)


def blob(data):
    return hashlib.sha1(
        b'blob ' + str(len(data)).encode('ascii') + b'\0' + data
    ).hexdigest()


def status():
    with urlopen(STATUS_URL, timeout=8) as response:
        result = json.load(response)
    require('error' not in result, 'Moonraker status query failed')
    return result['result']['status']


def require_idle(data):
    stats = data.get('print_stats')
    sd = data.get('virtual_sdcard')
    vaoc = data.get('gcode_macro _VAOC')
    handoff = data.get('auto_xy_ratos_handoff_rc16')
    saved = data.get('save_variables')
    variables = saved.get('variables') if isinstance(saved, dict) else None
    require(
        isinstance(stats, dict)
        and stats.get('state') in ('standby', 'cancelled', 'complete')
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
        and all(str(variables.get(key, 0)).lower()
                not in ('1', '1.0', 'true')
                for key in ('auto_xy_v2_fault', 'auto_xy_v2_in_progress',
                            'auto_xy_v2_retry_available')),
        'Printer must be idle with no selected/held print, native VAOC, '
        'AutoXY fault or pending manual recovery')


def checked_files(repo, home):
    repo, home = Path(repo), Path(home)
    source, target = repo / SOURCE, home / TARGET
    require(source.is_file() and not source.is_symlink()
            and blob(source.read_bytes()) == NEW_BLOB,
            'Unreviewed RC16 detector source; no files changed')
    require(target.is_file() and not target.is_symlink()
            and not target.parent.is_symlink(),
            'Installed detector missing or symlinked')
    for name, expected in (
        ('auto_xy/klipper/auto_xy_gate.py', GATE_BLOB),
        ('auto_xy/klipper/auto_xy_ratos_handoff_rc16.py', HANDOFF_BLOB)
    ):
        component = home / name
        require(component.is_file() and not component.is_symlink()
                and blob(component.read_bytes()) == expected,
                'Installed RC16 not the reviewed gate/handoff version: ' + name)
    original = target.read_bytes()
    digest = blob(original)
    require(digest in (OLD_BLOB, NEW_BLOB),
            'Unknown detector version; preserve installation for review')
    return target, original, source.read_bytes(), digest == NEW_BLOB


def atomic_replace(path, payload, mode):
    fd, tmp = tempfile.mkstemp(prefix='.rc16-annular-', dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def restart():
    subprocess.run(('sudo', 'systemctl', 'restart', 'klipper'),
                   timeout=120, check=True)


def verify_ready():
    import rc16_vision_refresh
    rc16_vision_refresh.ready()


def install(repo, home, *, live=True, restart_service=restart,
            wait_ready=verify_ready):
    path, before, after, already = checked_files(repo, home)
    if live:
        require_idle(status())
    if already:
        return 'already-installed', None
    rollbacks = Path(home) / 'auto_xy/rollbacks'
    require(rollbacks.is_dir() and not rollbacks.is_symlink(),
            'Missing or unsafe AutoXY rollback directory')
    backup = Path(tempfile.mkdtemp(prefix='rc16-local-annular-',
                                   dir=str(rollbacks)))
    previous = backup / 'previous_detector.py'
    previous.write_bytes(before)
    os.chmod(previous, 0o600)
    require(blob(previous.read_bytes()) == OLD_BLOB,
            'Detector rollback snapshot verification failed')
    (backup / 'receipt.json').write_text(json.dumps({
        'target': str(path), 'old_git_blob': OLD_BLOB,
        'new_git_blob': NEW_BLOB, 'snapshot': str(previous)
    }, indent=2))
    mode = stat.S_IMODE(path.stat().st_mode)
    changed = False
    try:
        if live:
            require_idle(status())
        require(blob(path.read_bytes()) == OLD_BLOB,
                'Detector changed after preflight; no files modified')
        atomic_replace(path, after, mode)
        changed = True
        require(blob(path.read_bytes()) == NEW_BLOB,
                'Installed detector checksum invalid')
        if live:
            restart_service()
            wait_ready()
            require_idle(status())
        return 'updated', backup
    except BaseException as exc:
        if not changed:
            raise Refuse('Update refused before replacing detector: %s' % exc) from exc
        try:
            require(path.is_file() and not path.is_symlink()
                    and blob(path.read_bytes()) == NEW_BLOB
                    and blob(previous.read_bytes()) == OLD_BLOB,
                    'Unsafe automatic rollback: detector or snapshot changed')
            atomic_replace(path, previous.read_bytes(), mode)
            require(blob(path.read_bytes()) == OLD_BLOB,
                    'Original detector could not be restored')
            if live:
                restart_service()
                wait_ready()
        except BaseException as rollback_exc:
            raise Refuse('INSTALL AND ROLLBACK FAILED: DO NOT PRINT. '
                         'Snapshot %s; install %s; rollback %s'
                         % (backup, exc, rollback_exc)) from rollback_exc
        raise Refuse('Update failed and original detector restored. '
                     'Snapshot %s, error: %s' % (backup, exc)) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'install'))
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        _, _, _, already = checked_files(repo, HOME)
        require_idle(status())
        print('RC16 LOCAL ANNULAR PREFLIGHT PASS:',
              'already installed' if already else 'reviewed compatible installation')
        if args.action == 'check' or already:
            return 0
        import rc16_active_install
        rc16_active_install.authorize_restart()
        outcome, backup = install(repo, HOME)
        print('RC16 local annular refresh:', outcome)
        print('Rollback snapshot:', backup)
        return 0
    except (OSError, ValueError, KeyError, Refuse,
            subprocess.SubprocessError) as exc:
        print('RC16 LOCAL ANNULAR REFUSED:', exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
