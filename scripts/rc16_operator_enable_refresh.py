#!/usr/bin/env python3
"""RC16 operator-owned enable preference; transactional existing-printer refresh.

Updates the reviewed RC16 gate, handoff and AUTO_XY_RETRY_DISMISS macro.
Never uses the RC15/fresh installer on an existing RC16 installation.
No install is permitted with an active, selected or recoverable print.
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
# target, repository source, acceptable existing blobs, new reviewed blob.
FILES = (
    ('auto_xy/klipper/auto_xy_gate.py',
     'src/auto_xy_gate_rc16.py',
     ('6df74b61f3f651b05b9d36a5fed12d4ff2c427f0',),
     '44788fa44c749e96350f1e1af1feee51408546b6'),
    ('auto_xy/klipper/auto_xy_ratos_handoff_rc16.py',
     'src/auto_xy_ratos_handoff_rc16.py',
     ('a9e4d1e0451fa704015ec4b97966a778820b38a7',
      '3471e280972ab95df74680c5baaeaec299f46ba0'),
     'a8b48aee334f095dd5f1a1a7ac6031eb64618701'),
    ('printer_data/config/auto_xy_macros.cfg',
     'config/auto_xy_macros.cfg',
     ('20975370938d3ceb8f5a360c8b4736c2d1d324f9',),
     'a188bb341e27b6c87cf1038ccfeff6b8a103e385'),
)
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
    require('error' not in result, 'Moonraker status query returned an error')
    return result['result']['status']


def require_idle(data):
    stats = data.get('print_stats')
    sd = data.get('virtual_sdcard')
    vaoc = data.get('gcode_macro _VAOC')
    handoff = data.get('auto_xy_ratos_handoff_rc16')
    saved = data.get('save_variables')
    vars_ = saved.get('variables') if isinstance(saved, dict) else None

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
        and isinstance(vars_, dict)
        and str(vars_.get('auto_xy_v2_fault', 0)).lower()
        not in ('1', '1.0', 'true')
        and str(vars_.get('auto_xy_v2_in_progress', 0)).lower()
        not in ('1', '1.0', 'true')
        and str(vars_.get('auto_xy_v2_retry_available', 0)).lower()
        not in ('1', '1.0', 'true'),
        'Printer must be idle with no selected/paused/recoverable file, '
        'native VAOC or AutoXY fault/retry before code replacement'
    )


def checked_files(repo, home):
    repo, home = Path(repo), Path(home)
    results = []
    for target_name, source_name, older, new_hash in FILES:
        source, target = repo/source_name, home/target_name
        require(source.is_file() and not source.is_symlink()
                and blob(source.read_bytes()) == new_hash,
                'Unreviewed update source: ' + source_name)
        require(target.is_file() and not target.is_symlink()
                and not target.parent.is_symlink(),
                'Missing/unsafe installed file: ' + target_name)
        old = target.read_bytes()
        observed = blob(old)
        require(observed == new_hash or observed in older,
                'Unrecognised installed AutoXY source: ' + target_name)
        results.append((target, old, source.read_bytes(), observed,
                        new_hash, stat.S_IMODE(target.stat().st_mode)))

    # Only one exact existing release cohort, not independently patched files.
    all_current = all(row[3] == row[4] for row in results)
    all_old = (
        results[0][3] == FILES[0][2][0]
        and results[1][3] in FILES[1][2]
        and results[2][3] == FILES[2][2][0]
    )
    require(all_current or all_old,
            'Mixed installed RC16 revisions; review first, never overwrite')
    return results, all_current


def atomic_replace(path, data, mode):
    fd, tmp = tempfile.mkstemp(prefix='.rc16-operator-', dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
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
    rows, already = checked_files(repo, home)
    if live:
        require_idle(status())
    if already:
        return 'already-installed', None
    rollbacks = Path(home)/'auto_xy/rollbacks'
    require(rollbacks.is_dir() and not rollbacks.is_symlink(),
            'Unsafe or missing rollback snapshot directory')
    archive = Path(tempfile.mkdtemp(prefix='rc16-operator-enable-',
                                    dir=str(rollbacks)))
    originals = archive/'originals'
    originals.mkdir(mode=0o700)
    receipt = []
    for index, (target, old, new, observed, new_hash, mode) in enumerate(rows):
        copy = originals/('original_%d' % index)
        copy.write_bytes(old)
        os.chmod(copy, 0o600)
        require(blob(copy.read_bytes()) == observed,
                'Rollback original verification failed: '+str(target))
        receipt.append(dict(path=str(target), old_blob=observed,
                            new_blob=new_hash, mode=mode))
    (archive/'receipt.json').write_text(json.dumps(receipt, indent=2))
    modified = []
    try:
        if live:
            require_idle(status())
        for target, old, new, observed, new_hash, mode in rows:
            require(target.is_file() and not target.is_symlink()
                    and blob(target.read_bytes()) == observed,
                    'Installed file changed after preflight: '+str(target))
            atomic_replace(target, new, mode)
            modified.append(target)
            require(blob(target.read_bytes()) == new_hash,
                    'Post-install digest mismatch: '+str(target))
        if live:
            restart_service()
            wait_ready()
            require_idle(status())
        return 'updated', archive
    except BaseException as error:
        if not modified:
            raise Refuse('Update refused before modifying files: %s' % error) from error
        try:
            for index in reversed(range(len(rows))):
                target, old, new, observed, new_hash, mode = rows[index]
                if target not in modified:
                    continue
                require(target.is_file() and not target.is_symlink()
                        and blob(target.read_bytes()) == new_hash,
                        'Unsafe rollback: modified source differs: '+str(target))
                copy = originals/('original_%d' % index)
                require(blob(copy.read_bytes()) == observed,
                        'Unsafe rollback: original snapshot differs')
                atomic_replace(target, copy.read_bytes(), mode)
                require(blob(target.read_bytes()) == observed,
                        'Rollback restore verification failed: '+str(target))
            if live:
                restart_service()
                wait_ready()
        except BaseException as rollback_error:
            raise Refuse(
                'UPDATE AND ROLLBACK FAILED; DO NOT PRINT. Snapshot %s; '
                'update: %s; rollback: %s' % (archive, error, rollback_error)
            ) from rollback_error
        raise Refuse('Update failed, previous bytes restored. Snapshot %s; '
                     'error: %s' % (archive, error)) from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'install'))
    opts = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        rows, already = checked_files(repo, HOME)
        require_idle(status())
        print('RC16 OPERATOR ENABLE PREFLIGHT PASS:',
              'already installed' if already else 'exact reviewed sources')
        if opts.action == 'check' or already:
            return 0
        import rc16_active_install
        rc16_active_install.authorize_restart()
        result, snapshot = install(repo, HOME)
        print('RC16 operator enable update:', result)
        print('Rollback snapshot:', snapshot)
        return 0
    except (OSError, ValueError, KeyError, Refuse,
            subprocess.SubprocessError) as error:
        print('RC16 OPERATOR ENABLE REFUSED:', error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
