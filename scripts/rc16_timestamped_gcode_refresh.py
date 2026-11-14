#!/usr/bin/env python3
"""RC16: exact-source, idle-only transaction for readable dated G-code names.

Changes only the RC16 Python coordinator, G-code preparation worker and
generated-file retention worker. No changes to saved offsets, source G-code,
Klipper config, or the commissioned nozzle profile. Existing old-format
generated files and authenticated registry entries remain supported.

Usage on Pi: python3 scripts/rc16_timestamped_gcode_refresh.py check|install
Do not run while printing, paused, or with a virtual-SD file still selected.
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
STATUS_URL = ('http://127.0.0.1:7125/printer/objects/query?'
              'print_stats&virtual_sdcard&gcode_macro%20_VAOC&auto_xy_ratos_handoff_rc16')
SOURCES = (
    ('src/tools/rc16_prepare_worker.py',
     'auto_xy/workers/rc16_prepare_worker.py',
     '32852157d0e2cf3e2d1133bbfe2bad8720fa1f73',
     'f85b20eb8ca28ecefa14fa065f0806de0bb402ec'),
    ('src/tools/rc16_generated_retention.py',
     'auto_xy/workers/rc16_generated_retention.py',
     '300d82cd49a45fb365dd8dbb0b6ee6f1634b9a14',
     '6ea0cd231f42901700af70f3e1b6afd968717111'),
    ('src/auto_xy_ratos_handoff_rc16.py',
     'auto_xy/klipper/auto_xy_ratos_handoff_rc16.py',
     'ea3ce0355639250aeceeaa49e7d6097066ae7141',
     '3471e280972ab95df74680c5baaeaec299f46ba0'),
)


class Refuse(RuntimeError):
    pass


def git_blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() +
                        b'\0' + data).hexdigest()


def read_status():
    with urlopen(STATUS_URL, timeout=8) as response:
        packet = json.load(response)
    if 'error' in packet:
        raise Refuse('Moonraker status request returned an error')
    return packet['result']['status']


def require_idle(status):
    print_stats = status.get('print_stats')
    virtual_sd = status.get('virtual_sdcard')
    vaoc = status.get('gcode_macro _VAOC')
    handoff = status.get('auto_xy_ratos_handoff_rc16')
    if (not isinstance(print_stats, dict) or
            print_stats.get('state') not in ('standby', 'complete', 'cancelled') or
            not isinstance(virtual_sd, dict) or
            virtual_sd.get('is_active') is not False or
            virtual_sd.get('file_path') or virtual_sd.get('filename') or
            not isinstance(vaoc, dict) or vaoc.get('is_started') is not False or
            not isinstance(handoff, dict) or
            handoff.get('armed') is not True or
            handoff.get('busy') is not False):
        raise Refuse('Printer is busy, file still selected, active VAOC or unknown RC16 state')


def check_files(repo, home):
    repo, home = Path(repo).resolve(), Path(home).absolute()
    for part in ('auto_xy', 'auto_xy/workers', 'auto_xy/klipper'):
        p = home / part
        if p.is_symlink() or not p.is_dir():
            raise Refuse('Unsafe RC16 installation directory: ' + part)
    states = []
    for source_name, target_name, old, new in SOURCES:
        source, target = repo / source_name, home / target_name
        if (not source.is_file() or source.is_symlink() or
                not target.is_file() or target.is_symlink()):
            raise Refuse('Missing or unsafe filename refresh file: ' + target_name)
        incoming = source.read_bytes()
        current = target.read_bytes()
        if git_blob(incoming) != new:
            raise Refuse('Unreviewed source bytes: ' + source_name)
        version = git_blob(current)
        if version not in (old, new):
            raise Refuse('Installed RC16 file differs: ' + target_name)
        states.append((target, current, incoming, old, new,
                       stat.S_IMODE(target.stat().st_mode)))
    flags = {git_blob(current) == new for _, current, _, _, new, _ in states}
    if len(flags) != 1:
        raise Refuse('Mixed old/new filename update; inspect before continuing')
    return states, flags.pop()


def atomic_write(path, data, mode):
    fd, temp = tempfile.mkstemp(prefix='.rc16-dated-gcode-', dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.lexists(temp):
            os.unlink(temp)


def restart():
    subprocess.run(('sudo', 'systemctl', 'restart', 'klipper'),
                   check=True, timeout=120)


def verify_ready():
    # The already reviewed RC16 supervised installer validates Klipper READY
    # and the native handoff guards; do not interpret a running systemd
    # service as proof that protected printing is armed.
    import rc16_vision_refresh as refresh
    refresh.ready()


def install(repo, home, live=True, do_restart=restart, wait_ready=verify_ready):
    states, already = check_files(repo, home)
    if already:
        return 'already-updated', None
    if live:
        require_idle(read_status())
    rollback_root = Path(home) / 'auto_xy/rollbacks'
    if not rollback_root.is_dir() or rollback_root.is_symlink():
        raise Refuse('Unsafe rollback snapshot directory')
    backup = Path(tempfile.mkdtemp(prefix='rc16-dated-gcode-', dir=str(rollback_root)))
    originals = backup / 'originals'
    originals.mkdir(mode=0o700)
    receipt = []
    for i, (target, before, after, old, new, mode) in enumerate(states):
        saved = originals / ('original_%d.py' % i)
        saved.write_bytes(before)
        os.chmod(saved, 0o600)
        if git_blob(saved.read_bytes()) != old:
            raise Refuse('Rollback snapshot differs from expected original')
        receipt.append({'target': str(target), 'old_blob': old,
                        'new_blob': new, 'mode': mode})
    (backup / 'receipt.json').write_text(json.dumps(receipt, indent=2))

    changed = []
    try:
        if live:
            require_idle(read_status())
        for target, before, after, old, new, mode in states:
            if git_blob(target.read_bytes()) != old:
                raise Refuse('File changed since preflight: ' + str(target))
            atomic_write(target, after, mode)
            changed.append(target)
            if git_blob(target.read_bytes()) != new:
                raise Refuse('Refresh write verification failed: ' + str(target))
        if live:
            do_restart()
            wait_ready()
            require_idle(read_status())
    except BaseException as error:
        if not changed:
            raise Refuse('Update refused before printer files changed: %s' % error) from error
        try:
            for i in reversed(range(len(states))):
                target, before, after, old, new, mode = states[i]
                current = git_blob(target.read_bytes())
                if target in changed:
                    if current != new:
                        raise Refuse('A modified installed file prevents safe rollback: ' + str(target))
                    atomic_write(target, (originals / ('original_%d.py' % i)).read_bytes(), mode)
                    if git_blob(target.read_bytes()) != old:
                        raise Refuse('Rollback verification failed: ' + str(target))
            if live:
                do_restart()
                wait_ready()
        except BaseException as rollback_error:
            raise Refuse('Update failed AND rollback incomplete. Do not print. '
                         'Snapshot: %s; error: %s; rollback: %s' %
                         (backup, error, rollback_error)) from rollback_error
        raise Refuse('Update failed; original files restored from %s; error: %s' %
                     (backup, error)) from error
    return 'updated', backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'install'))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        states, already = check_files(root, HOME)
        require_idle(read_status())
        print('RC16 dated G-code preflight PASS; ' +
              ('already installed' if already else 'three known RC16 files'))
        if args.action == 'check' or already:
            return 0
        # Request sudo approval before any write, including rollback.
        import rc16_active_install
        rc16_active_install.authorize_restart()
        result, backup = install(root, HOME)
        print('RC16 dated G-code:', result)
        print('Rollback snapshot:', backup)
        return 0
    except (OSError, KeyError, ValueError, Refuse,
            subprocess.SubprocessError) as error:
        print('RC16 dated G-code REFUSED/FAILED: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
