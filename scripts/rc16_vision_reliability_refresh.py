#!/usr/bin/env python3
"""Transactional RC16 vision reliability refresh for an EXISTING RC16 system.

Changes only four reviewed RC16 Python sources: camera gate, persistent vision
worker, reference scoring library and a NEW dedicated RC16 geometry detector.
The signed RC15 detector, offsets, calibration reference photographs, printer
configuration and source/selected G-code are not modified.

Run "check" first; run "install" only from interactive SSH with printer
completely idle and no held/selected file or active native VAOC.
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
              'print_stats&virtual_sdcard&gcode_macro%20_VAOC&'
              'auto_xy_ratos_handoff_rc16&save_variables')
# Old hashes are exact reviewed installed RC16 camera sources, not RC15 ones.
# A non-recognised installation is REFUSED before writes.
SOURCES = (
    ('src/auto_xy_gate_rc16.py',
     'auto_xy/klipper/auto_xy_gate.py',
     '5a569655c9edecfb41549bdf468e208abdaf1356',
     '42791d5785186aae6f31f8a0fd53f42ccd763095'),
    ('src/auto_xy_vision_worker_rc16.py',
     'auto_xy/vision/auto_xy_vision_worker.py',
     'eb11b30bafeee1f88bc44ffba10c6c73741b1a0b',
     '1556bed01c50fd87c188ff0d8b670ec3b4baaaf9'),
    ('src/auto_xy_reference_signature_rc16.py',
     'auto_xy/vision/auto_xy_reference_signature_rc16.py',
     'a6ab2cfb55483d5b41c5e408b37c00e23ce95447',
     '1350573fc9052a3ccd8639d2a109a1555964de97'),
    ('src/auto_xy_detector_rc16.py',
     'auto_xy/vision/auto_xy_detector_rc16.py', None,
     'e44207a3da584961031280f6332a597326dc5e63'),
)


class Refuse(RuntimeError):
    pass


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') +
                        b'\0' + data).hexdigest()


def status():
    with urlopen(STATUS_URL, timeout=8) as response:
        packet = json.load(response)
    if 'error' in packet:
        raise Refuse('Moonraker refused the required status query')
    return packet['result']['status']


def require_idle(data):
    stats = data.get('print_stats')
    sd = data.get('virtual_sdcard')
    vaoc = data.get('gcode_macro _VAOC')
    handoff = data.get('auto_xy_ratos_handoff_rc16')
    saved = data.get('save_variables')
    variables = saved.get('variables') if isinstance(saved, dict) else None
    if (not isinstance(stats, dict) or
            stats.get('state') not in ('standby', 'complete', 'cancelled') or
            not isinstance(sd, dict) or sd.get('is_active') is not False or
            sd.get('file_path') or sd.get('filename') or
            not isinstance(vaoc, dict) or vaoc.get('is_started') is not False or
            not isinstance(handoff, dict) or handoff.get('armed') is not True or
            handoff.get('busy') is not False or
            not isinstance(variables, dict) or
            str(variables.get('auto_xy_v2_fault', 0)).lower() in ('1', '1.0', 'true') or
            str(variables.get('auto_xy_v2_in_progress', 0)).lower() in ('1', '1.0', 'true')):
        raise Refuse('Printer not idle, held print or unresolved AutoXY fault; no changes permitted')


def checked_files(repo, home):
    repo, home = Path(repo), Path(home)
    for part in ('auto_xy', 'auto_xy/klipper', 'auto_xy/vision'):
        directory = home / part
        if directory.is_symlink() or not directory.is_dir():
            raise Refuse('Unsafe RC16 installation directory: ' + part)
    result = []
    for source_name, target_name, old_sha, new_sha in SOURCES:
        source = repo / source_name
        target = home / target_name
        if (not source.is_file() or source.is_symlink() or target.is_symlink()
                or (target.exists() and not target.is_file())):
            raise Refuse('Missing or unsafe upgrade source: ' + target_name)
        new_data = source.read_bytes()
        if blob(new_data) != new_sha:
            raise Refuse('Unreviewed source: ' + source_name)
        old_data = target.read_bytes() if target.is_file() else None
        actual = blob(old_data) if old_data is not None else None
        if actual not in (old_sha, new_sha):
            raise Refuse('Unrecognised installed RC16 file: ' + target_name)
        mode = stat.S_IMODE(target.stat().st_mode) if old_data is not None else 0o644
        result.append((target, old_data, new_data, old_sha, new_sha, mode))
    versions = {old == new_data for _, old, new_data, _, _, _ in result}
    if len(versions) != 1:
        raise Refuse('Mixed old/new RC16 sources; inspect before applying update')
    return result, versions.pop()


def atomic_replace(path, data, mode):
    fd, temp = tempfile.mkstemp(prefix='.rc16-vision-', dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # The new detector is created only when the checked preflight
        # confirmed it was absent. This is safe while printer is idle.
        if path.exists() or path.is_symlink():
            os.replace(temp, path)
        else:
            os.link(temp, path)
    finally:
        if os.path.lexists(temp):
            os.unlink(temp)


def restart():
    subprocess.run(('sudo', 'systemctl', 'restart', 'klipper'),
                   check=True, timeout=120)


def verify_ready():
    import rc16_vision_refresh
    rc16_vision_refresh.ready()


def install(repo, home, live=True, restart_service=restart,
            wait_ready=verify_ready):
    entries, already = checked_files(repo, home)
    if already:
        return 'already-installed', None
    if live:
        require_idle(status())
    snapshots = Path(home) / 'auto_xy/rollbacks'
    if snapshots.is_symlink() or not snapshots.is_dir():
        raise Refuse('Unsafe rollback snapshot directory')
    backup = Path(tempfile.mkdtemp(prefix='rc16-vision-reliability-',
                                    dir=str(snapshots)))
    saved = backup/'originals'
    saved.mkdir(mode=0o700)
    receipt = []
    for i, (path, old, new, old_sha, new_sha, mode) in enumerate(entries):
        if old is not None:
            prior = saved/('original_%d.py' % i)
            prior.write_bytes(old)
            os.chmod(prior, 0o600)
            if blob(prior.read_bytes()) != old_sha:
                raise Refuse('Could not verify original rollback snapshot')
        receipt.append({'target':str(path),'old_blob':old_sha,
                        'new_blob':new_sha,'mode':mode})
    (backup/'receipt.json').write_text(json.dumps(receipt,indent=2))
    changed = []
    try:
        if live:
            require_idle(status())
        for path, old, new, old_sha, new_sha, mode in entries:
            actual = blob(path.read_bytes()) if path.is_file() and not path.is_symlink() else None
            if actual != old_sha:
                raise Refuse('Source changed since preflight: ' + str(path))
            atomic_replace(path, new, mode)
            changed.append(path)
            if blob(path.read_bytes()) != new_sha:
                raise Refuse('Installed source did not match verified source: ' + str(path))
        if live:
            restart_service()
            wait_ready()
            require_idle(status())
    except BaseException as original_error:
        if not changed:
            raise Refuse('Update refused before any installed files were changed: %s' %
                         original_error) from original_error
        try:
            for i in reversed(range(len(entries))):
                path, old, new, old_sha, new_sha, mode = entries[i]
                if path not in changed:
                    continue
                if path.is_symlink() or blob(path.read_bytes()) != new_sha:
                    raise Refuse('Changed installed file prevents safe rollback: '+str(path))
                if old is None:
                    path.unlink()
                else:
                    atomic_replace(path, (saved/('original_%d.py' % i)).read_bytes(), mode)
                    if blob(path.read_bytes()) != old_sha:
                        raise Refuse('Rollback byte verification failed: '+str(path))
            if live:
                restart_service()
                wait_ready()
        except BaseException as recovery:
            raise Refuse('Installation failed AND rollback incomplete; DO NOT PRINT. '
                         'Snapshot %s; install error %s; recovery %s' %
                         (backup,original_error,recovery)) from recovery
        raise Refuse('Installation failed; all previous files restored. '
                     'Snapshot %s; reason %s' % (backup,original_error)) from original_error
    return 'updated', backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check','install'))
    opts = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        entries, already = checked_files(repo, HOME)
        require_idle(status())
        print('RC16 VISION RELIABILITY PREFLIGHT PASS:',
              'already installed' if already else 'four reviewed sources')
        if opts.action == 'check' or already:
            return 0
        # Authenticate both the normal restart and possible rollback restart
        # before modifying printer source. Never collect a sudo password here.
        import rc16_active_install
        rc16_active_install.authorize_restart()
        result, backup = install(repo, HOME)
        print('RC16 vision reliability:', result)
        print('Rollback snapshot:', backup)
        return 0
    except (OSError, ValueError, KeyError, Refuse,
            subprocess.SubprocessError) as exc:
        print('RC16 VISION RELIABILITY REFUSED/FAILED:', exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
