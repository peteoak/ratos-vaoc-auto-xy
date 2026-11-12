#!/usr/bin/env python3
"""RC16 hot-nozzle likeness refresh: one exact source, verified rollback.

This updates ONLY the RC16 camera signature verifier. All calibration geometry,
commissioned JPEGs/profiles, saved offsets, RatOS hooks, and recovery procedures
are unchanged. Run check first; install only on an idle printer over SSH.
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
from urllib.error import HTTPError, URLError

HOME = Path('/home/pi')
SOURCE = Path('src/auto_xy_reference_signature_rc16.py')
TARGET = Path('auto_xy/vision/auto_xy_reference_signature_rc16.py')
OLD_BLOB = '021799053569ef4780c74ae6d6756ccd74f00ba0'
NEW_BLOB = 'a6ab2cfb55483d5b41c5e408b37c00e23ce95447'
STATUS_URL = 'http://127.0.0.1:7125/printer/objects/query?print_stats&virtual_sdcard&gcode_macro%20_VAOC&auto_xy_gate&auto_xy_ratos_handoff_rc16'


class Refusal(RuntimeError):
    pass


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() +
                        b'\0' + data).hexdigest()


def read_status(url):
    with urlopen(url, timeout=8) as response:
        obj = json.load(response)
    if 'error' in obj:
        raise Refusal('Moonraker returned an error')
    return obj['result']['status']


def check_idle(status):
    stats = status.get('print_stats')
    sd = status.get('virtual_sdcard')
    vaoc = status.get('gcode_macro _VAOC')
    gate = status.get('auto_xy_gate')
    handoff = status.get('auto_xy_ratos_handoff_rc16')
    if (not isinstance(stats, dict) or
            stats.get('state') not in ('standby', 'complete', 'cancelled') or
            not isinstance(sd, dict) or sd.get('is_active') is not False or
            sd.get('filename') or sd.get('file_path') or
            not isinstance(vaoc, dict) or vaoc.get('is_started') is not False or
            not isinstance(gate, dict) or gate.get('busy') is not False or
            not isinstance(handoff, dict) or handoff.get('armed') is not True or
            handoff.get('busy') is not False):
        raise Refusal('Printer busy, held/selected print, active VAOC, or unknown RC16 gate state')


def check_sources(root, home):
    source = root / SOURCE
    target = home / TARGET
    if (not source.is_file() or source.is_symlink() or
            not target.is_file() or target.is_symlink() or
            (home/'auto_xy').is_symlink() or
            (home/'auto_xy/vision').is_symlink()):
        raise Refusal('Vision refresh source/target is absent or unsafe')
    data = source.read_bytes()
    before = target.read_bytes()
    if blob(data) != NEW_BLOB:
        raise Refusal('Unreviewed vision refresh source; expected pinned Git blob')
    if blob(before) == NEW_BLOB:
        return None
    if blob(before) != OLD_BLOB:
        raise Refusal('Installed signature verifier is not the reviewed RC16 version')
    return before, data


def replace_atomic(path, data, mode):
    fd, tmp = tempfile.mkstemp(prefix='.autoxy-vision-', dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def restart():
    subprocess.run(('sudo', 'systemctl', 'restart', 'klipper'),
                   check=True, timeout=120)


def ready():
    import rc16_active_install as active
    active.wait_for_klipper_ready(timeout=120)
    active.check_active_runtime(timeout=30)


def install(root, home, live=True, do_restart=restart, wait=ready):
    changes = check_sources(root, home)
    if changes is None:
        return 'already-installed', None
    before, after = changes
    if live:
        check_idle(read_status(STATUS_URL))
    target = home / TARGET
    mode = stat.S_IMODE(target.stat().st_mode)
    snapshots = home / 'auto_xy/rollbacks'
    if not snapshots.is_dir() or snapshots.is_symlink():
        raise Refusal('Unsafe rollback snapshot root')
    backup = Path(tempfile.mkdtemp(prefix='rc16-vision-refresh-', dir=str(snapshots)))
    saved = backup / 'signature-original.py'
    saved.write_bytes(before)
    os.chmod(saved, 0o600)
    if saved.read_bytes() != before:
        raise Refusal('Snapshot failed integrity verification')
    (backup/'receipt.json').write_text(json.dumps({
        'old_blob': OLD_BLOB, 'new_blob': NEW_BLOB,
        'installed_target': str(target), 'mode': mode}, indent=2))
    try:
        if live:
            check_idle(read_status(STATUS_URL))
        if target.read_bytes() != before:
            raise Refusal('Installed vision source changed after backup')
        replace_atomic(target, after, mode)
        if blob(target.read_bytes()) != NEW_BLOB:
            raise Refusal('Vision refresh verification failed')
        if live:
            do_restart()
            wait()
            check_idle(read_status(STATUS_URL))
    except BaseException as exc:
        try:
            if blob(target.read_bytes()) != NEW_BLOB:
                raise Refusal('Vision target changed unexpectedly; manual recovery required')
            replace_atomic(target, before, mode)
            if blob(target.read_bytes()) != OLD_BLOB:
                raise Refusal('Restored vision source did not verify')
            if live:
                do_restart()
                wait()
        except BaseException as recovery:
            raise Refusal('Vision refresh failed AND recovery incomplete; stop prints. '
                          'Backup: %s; error: %s; recovery: %s' %
                          (backup, exc, recovery)) from recovery
        raise Refusal('Vision refresh failed; original verified file restored '
                      'from %s; error: %s' % (backup, exc)) from exc
    return 'updated', backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'install'))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        changes = check_sources(root, HOME)
        if changes is None:
            print('RC16 VISION REFRESH: already installed')
            return 0
        check_idle(read_status(STATUS_URL))
        print('RC16 VISION REFRESH preflight: PASS (idle, expected RC16 source)')
        if args.action == 'check':
            return 0
        # Authorize future rollback restart BEFORE touching any source file.
        import rc16_active_install as active
        active.authorize_restart()
        result, backup = install(root, HOME)
        print('RC16 VISION REFRESH:', result)
        print('Rollback snapshot:', backup)
        return 0
    except (OSError, ValueError, KeyError, Refusal, RuntimeError,
            HTTPError, URLError, subprocess.CalledProcessError) as exc:
        print('RC16 VISION REFRESH REFUSED/FAILED:', str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
