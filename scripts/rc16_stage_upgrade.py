#!/usr/bin/env python3
"""RC16 Dev1: guarded, default-OFF staged upgrade from an EXACT RC15 Dev6.

Does not replace or modify stock RatOS, nor change saved offsets, camera
commissioning, virtual SD, or an existing vision environment. Fails closed
if the existing installer-owned controller no longer matches RC15 source.
Two modes: --check (read-only), --stage (transactional disabled install),
and --rollback (exact installed-file verification before restore).

NOTE: Stage does not mean qualified/activated. Supervised hardware testing
is required before enabling the new coordinator.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from pathlib import Path

VERSION = '0.5.0-rc16-dev1'
HOME = Path('/home/pi')
INCLUDE = '[include auto_xy_rc16.cfg]'
RC15_INCLUDE = '[include auto_xy.cfg]'
SOURCES = {
    'auto_xy/klipper/auto_xy_gate.py':
        ('src/auto_xy_gate_rc16.py', '95bf8077dcf9e05081714fcde85a18d2f383b96d'),
    'auto_xy/klipper/auto_xy_ratos_handoff_rc16.py':
        ('src/auto_xy_ratos_handoff_rc16.py', '7cb03074b349c05a8cf709b4e33947b5c6a0bb8d'),
    'auto_xy/workers/rc16_prepare_worker.py':
        ('src/tools/rc16_prepare_worker.py', '47808233086e234f72e04ffef2b0ab9329238156'),
}
LINK = 'klipper/klippy/extras/auto_xy_ratos_handoff_rc16.py'
CONFIG = 'printer_data/config/auto_xy_rc16.cfg'
PRINTER = 'printer_data/config/printer.cfg'
OLD_GATE = 'auto_xy/klipper/auto_xy_gate.py'
MANAGED = frozenset([*SOURCES, LINK, CONFIG, PRINTER])


class Refuse(RuntimeError):
    pass


def require(condition, msg):
    if not condition:
        raise Refuse(msg)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def git_blob_sha(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()


def verify_sources(repo):
    repo = Path(repo).resolve()
    manifest = json.loads((repo / 'MANIFEST.json').read_text(encoding='utf-8'))
    require(manifest.get('version') == '0.5.0-rc15-dev6',
            'Upgrade base is not RC15 Dev6')
    old_source = repo / 'src/auto_xy_gate.py'
    old_data = old_source.read_bytes()
    require(sha256_bytes(old_data) == manifest['sha256']['src/auto_xy_gate.py'],
            'RC15 baseline gate does not match signed RC15 manifest')
    files = {}
    for rel, (path, expected_blob) in SOURCES.items():
        data = (repo / path).read_bytes()
        require(git_blob_sha(data) == expected_blob,
                'RC16 source blob changed: ' + path + '; review and update pins')
        files[rel] = data
    require(b'RELEASE_VERSION = "0.5.0-rc16-dev1"' in files[OLD_GATE],
            'RC16 controller version mismatch')
    require(b'def load_config(config)' in files[OLD_GATE] and
            b'def load_config(config)' in files['auto_xy/klipper/auto_xy_ratos_handoff_rc16.py'],
            'Missing Klipper extension entrypoint')
    return old_data, files


def add_include(text):
    require(text.count(RC15_INCLUDE) == 1,
            'Existing printer.cfg must have one RC15 include')
    require(INCLUDE not in text, 'RC16 include already installed')
    # The complete Klipper SAVE_CONFIG tail remains untouched.
    save = re.search(r'(?m)^#\*#', text)
    head, tail = ((text[:save.start()], text[save.start():]) if save
                  else (text, ''))
    prefix = head.rstrip('\r\n')
    return prefix + '\n\n# AutoXY RC16 protected handoff (OFF until qualified)\n' + INCLUDE + '\n\n' + tail


def no_symlink_parents(home, rel):
    p = home
    for seg in Path(rel).parts[:-1]:
        p = p / seg
        require(not p.is_symlink(), 'Managed path has symlink directory: ' + rel)


def config_text(home):
    # Create the new Klipper extension but keep the interception disabled.
    return ('# RC16 Dev1 STAGED AND OFF. Do not enable before supervised qualification.\n'
            '[auto_xy_ratos_handoff_rc16]\n'
            'enabled: False\n'
            'worker: ' + str(home / 'auto_xy/workers/rc16_prepare_worker.py') + '\n'
            'worker_python: /usr/bin/python3\n'
            'registry_directory: ' + str(home / 'auto_xy/rc16_registry') + '\n')


def check_installation(home, repo, live):
    old_data, files = verify_sources(repo)
    home = Path(home).absolute()
    require(home.is_dir() and not home.is_symlink(), 'Invalid Pi home')
    for rel in MANAGED:
        no_symlink_parents(home, rel)
    printer = home / PRINTER
    user_cfg = home / 'printer_data/config/auto_xy.cfg'
    require(printer.is_file() and not printer.is_symlink(),
            'Missing or symlinked printer.cfg')
    require(user_cfg.is_file() and not user_cfg.is_symlink(),
            'Existing RC15 config is absent')
    pc = printer.read_text(encoding='utf-8')
    cfg = user_cfg.read_text(encoding='utf-8')
    require(pc.count(RC15_INCLUDE) == 1 and INCLUDE not in pc,
            'Expected RC15 only, without any RC16 include')
    require(re.search(r'(?m)^\s*protected_print_enabled:\s*False\s*$', cfg),
            'Protected print is already active or setting unknown; disable and review first')
    installed = home / OLD_GATE
    require(installed.is_file() and not installed.is_symlink() and
            installed.read_bytes() == old_data,
            'Installed controller differs from RC15 Dev6; reviewed migration required')
    link = home / 'klipper/klippy/extras/auto_xy_gate.py'
    require(link.is_symlink() and link.resolve() == installed,
            'Existing gate symlink differs; refusing change')
    for rel in (LINK, CONFIG, *[x for x in SOURCES if x != OLD_GATE]):
        target = home / rel
        require(not target.exists() and not target.is_symlink(),
                'RC16 target already present: ' + rel)
    if live:
        require(home == HOME, 'Live staging requires canonical /home/pi')
        # This is the read-only RC15 Moonraker/VAOC/temperature preflight.
        import install_autoxy
        install_autoxy.live_safe(home, 'rollback')
        import ratos_extension
        snapshot = ratos_extension.snapshot(home)
        require(snapshot['registered'] == str(installed) and
                snapshot['link'] == str(installed),
                'Unexpected installed RatOS extension registration')
    changes = dict(files)
    changes[CONFIG] = config_text(home).encode('utf-8')
    changes[PRINTER] = add_include(pc).encode('utf-8')
    changes[LINK] = ('SYMLINK:' +
                     str(home / 'auto_xy/klipper/auto_xy_ratos_handoff_rc16.py')).encode('ascii')
    return changes


def write_atomic(path, data, old_mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not path.is_symlink(), 'Refusing symlink write')
    fd, tmp = tempfile.mkstemp(prefix='.rc16-dev1-', dir=str(path.parent))
    try:
        if old_mode is not None:
            os.fchmod(fd, old_mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def snapshot(home, changes):
    parent = home / 'auto_xy/rollbacks'
    require(parent.is_dir() and not parent.is_symlink(), 'Missing safe RC15 rollback directory')
    folder = Path(tempfile.mkdtemp(prefix='rc16-dev1-', dir=str(parent)))
    entries = []
    for rel, data in changes.items():
        path = home / rel
        require(rel in MANAGED and not path.is_symlink(), 'Unexpected managed source')
        exists = path.exists()
        item = {
            'path': rel, 'old_exists': exists,
            'old_sha256': sha256_bytes(path.read_bytes()) if exists else None,
            'old_mode': stat.S_IMODE(path.stat().st_mode) if exists else None,
            'new_link': data.decode('ascii')[8:] if data.startswith(b'SYMLINK:') else None,
            'new_sha256': None if data.startswith(b'SYMLINK:') else sha256_bytes(data)
        }
        if exists:
            target = folder / 'files' / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        entries.append(item)
    manifest = {'version': VERSION, 'home': str(home), 'entries': entries}
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return folder, manifest


def restore(home, folder, manifest, require_installed=True):
    require(manifest.get('home') == str(home) and manifest.get('version') == VERSION,
            'Snapshot does not belong to this RC16 installation')
    entries = manifest['entries']
    for ent in entries:
        rel = ent['path']
        require(rel in MANAGED, 'Unexpected rollback target')
        no_symlink_parents(home, rel)
        path = home / rel
        if require_installed:
            if ent['new_link']:
                require(path.is_symlink() and os.readlink(path) == ent['new_link'],
                        'Installed extension link changed; rollback refused')
            else:
                require(path.is_file() and not path.is_symlink() and
                        sha256_bytes(path.read_bytes()) == ent['new_sha256'],
                        'Managed file changed after RC16 installation: ' + rel)
        if ent['old_exists']:
            previous = (folder / 'files' / rel).read_bytes()
            require(sha256_bytes(previous) == ent['old_sha256'],
                    'Backup file changed, refusing rollback')
    for ent in reversed(entries):
        path = home / ent['path']
        if ent['old_exists']:
            write_atomic(path, (folder / 'files' / ent['path']).read_bytes(),
                         ent['old_mode'])
        elif path.exists() or path.is_symlink():
            path.unlink()


def stage(home, changes):
    folder, manifest = snapshot(home, changes)
    try:
        # printer.cfg is LAST. The included RC16 section stays disabled.
        order = [key for key in changes if key != PRINTER] + [PRINTER]
        for rel in order:
            data = changes[rel]
            target = home / rel
            if data.startswith(b'SYMLINK:'):
                require(not target.exists() and not target.is_symlink(),
                        'Symlink target appeared')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(data.decode('ascii')[8:])
            else:
                mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
                write_atomic(target, data, mode)
        for ent in manifest['entries']:
            target = home / ent['path']
            if ent['new_link']:
                require(target.is_symlink() and os.readlink(target) == ent['new_link'],
                        'Failed to install RC16 extension symlink')
            else:
                require(sha256_bytes(target.read_bytes()) == ent['new_sha256'],
                        'File changed during staged upgrade')
    except BaseException:
        restore(home, folder, manifest, require_installed=False)
        raise
    return folder


def rollback(home, folder, live):
    root = home / 'auto_xy/rollbacks'
    require(root.is_dir() and not root.is_symlink(), 'Rollback root unavailable')
    folder = folder.resolve(strict=True)
    require(folder.parent == root.resolve(), 'Invalid snapshot location')
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if live:
        import install_autoxy
        install_autoxy.live_safe(home, 'rollback')
    restore(home, folder, manifest, require_installed=True)
    return folder


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--stage', action='store_true')
    group.add_argument('--rollback', type=Path)
    ap.add_argument('--home', type=Path, default=HOME, help=argparse.SUPPRESS)
    ap.add_argument('--offline', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--confirm', default='')
    args = ap.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    home = args.home.absolute()
    try:
        if not args.offline:
            require(home == HOME, 'Live staging only supports /home/pi')
        if args.rollback:
            require(args.confirm == 'ROLLBACK_RC16_DEV1',
                    'Use explicit --confirm ROLLBACK_RC16_DEV1')
            where = rollback(home, args.rollback, live=not args.offline)
            print('RC16 ROLLBACK VERIFIED, restored RC15 files from:', where)
            print('Klipper restart needed while idle. Saved offsets untouched.')
            return 0
        changes = check_installation(home, root, live=not args.offline)
        print('RC16 DEV1 preflight PASS; installed controller is exact RC15 Dev6')
        print('Change summary:', ', '.join(changes))
        if args.check:
            print('CHECK PASSED; NO FILES CHANGED')
            return 0
        require(args.confirm == 'STAGE_RC16_DEV1_DISABLED',
                'Stage requires --confirm STAGE_RC16_DEV1_DISABLED')
        folder = stage(home, changes)
        print('RC16 DEV1 STAGED AND DISABLED; rollback snapshot:', folder)
        print('Restart Klipper only after reviewing settings. Do NOT enable protected printing.')
        return 0
    except (OSError, ValueError, KeyError, Refuse, RuntimeError) as error:
        print('RC16 REFUSED: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
