#!/usr/bin/env python3
"""Create and verify off-Pi archive copies of exact historical AutoXY backups.

NO deletion capability. Normal pi user; no sudo, Klipper calls, or writes to
/home/pi. Archive and private receipts are created only under a separately
mounted external filesystem supplied by the operator.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath

HOME = Path('/home/pi')
GROUPS = {
    'pre-uninstall': ('autoxy-pre-uninstall-20261102-040312',),
    'factory-backups': ('autoxy-factory-backups',),
    'rc15-quarantine': ('autoxy-quarantine-rc15-dev5-20261102',),
    'retained-config-zips': (
        'printer_data/config/config-20261023-183344.zip',
        'printer_data/config/config-20261023-182815.zip',
        'printer_data/config/config-20260916-193926.zip',
    ),
}
MAX_ITEMS = 40000
MAX_BYTES = 30 * (1024 ** 3)
BUFFER_SIZE = 4 * 1024 * 1024


class Refuse(RuntimeError):
    pass


def require(ok, reason):
    if not ok:
        raise Refuse(reason)


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(BUFFER_SIZE), b''):
            h.update(block)
    return h.hexdigest()


def relative_safe(name):
    p = PurePosixPath(name)
    require(name and not p.is_absolute() and all(x not in ('..', '.') for x in p.parts),
            'Unsafe archive path: %r' % name)
    return str(p)


def scan_group(home, group, *, hash_files):
    """Read exact roots without following symlinks or crossing filesystems."""
    home = Path(home)
    require(home.is_dir() and not home.is_symlink(), 'Untrusted home path')
    require(group in GROUPS, 'Unknown backup group')
    device = home.stat().st_dev
    entries = {}
    total = 0

    def walk(path):
        nonlocal total
        st = path.lstat()
        require(st.st_dev == device, 'Unexpected filesystem within backup: '+str(path))
        relative = relative_safe(path.relative_to(home).as_posix())
        require(relative not in entries, 'Duplicate backup path')
        require(len(entries) < MAX_ITEMS, 'Too many backup entries')
        base = {'mode': stat.S_IMODE(st.st_mode), 'uid': st.st_uid,
                'gid': st.st_gid, 'mtime_ns': st.st_mtime_ns}
        if stat.S_ISREG(st.st_mode):
            require(st.st_size >= 0, 'Invalid file size')
            total += st.st_size
            require(total <= MAX_BYTES, 'Backup exceeds bounded inventory limit')
            base.update(kind='file', size=st.st_size)
            if hash_files:
                base['sha256'] = digest(path)
                after = path.lstat()
                require((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) ==
                        (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns),
                        'Source changed while hashing: '+str(path))
        elif stat.S_ISLNK(st.st_mode):
            base.update(kind='symlink', link=os.readlink(path))
        elif stat.S_ISDIR(st.st_mode):
            base.update(kind='directory')
        else:
            raise Refuse('Special file cannot be safely archived: '+str(path))
        entries[relative] = base
        if base['kind'] == 'directory':
            with os.scandir(path) as it:
                children = sorted((e.name for e in it))
            for name in children:
                walk(path / name)

    for name in GROUPS[group]:
        root = home / name
        require(root.exists() and not root.is_symlink(), 'Missing/untrusted backup root: '+str(root))
        walk(root)
    return {'group': group, 'entries': entries, 'bytes': total,
            'files': sum(1 for x in entries.values() if x['kind'] == 'file')}


def validate_external(home, mount):
    home, mount = Path(home), Path(mount)
    require(mount.is_absolute() and mount.exists(), 'External mount path missing')
    require(mount.is_dir() and not mount.is_symlink(), 'External target is not a real directory')
    require(os.path.ismount(mount), 'Target must be the exact mounted drive root')
    require(mount.stat().st_dev != home.stat().st_dev,
            'External mount is on same filesystem as printer')
    require(mount.resolve() not in (home.resolve(), Path('/')),
            'Refuse Pi root or home directory')
    return mount


def source_manifest_equal(before, after):
    # Changed file content must always be checked, not only metadata.
    return before == after


def archive_group(home, snapshot, dest_file):
    """Create a fresh archive; source paths come from a hardcoded allowlist."""
    require(not dest_file.exists() and not dest_file.is_symlink(), 'Refuse existing output')
    with tarfile.open(dest_file, 'x:gz', compresslevel=1,
                      format=tarfile.PAX_FORMAT, dereference=False) as tar:
        for name in GROUPS[snapshot['group']]:
            tar.add(Path(home) / name, arcname=name, recursive=True)
    require(dest_file.is_file() and not dest_file.is_symlink(), 'Archive not regular file')


def verify_archive(snapshot, archive_path):
    """Independently checksum every tar member, including hardlink contents."""
    require(archive_path.is_file() and not archive_path.is_symlink(),
            'Archive missing/not a regular file')
    actual = {}
    with tarfile.open(archive_path, 'r:gz') as tar:
        members = tar.getmembers()
        require(len(members) <= MAX_ITEMS, 'Archive exceeds entry count bound')
        for member in members:
            name = relative_safe(member.name.rstrip('/'))
            require(name not in actual, 'Duplicate archive entry: '+name)
            require(name in snapshot['entries'], 'Unexpected archive entry: '+name)
            wanted = snapshot['entries'][name]
            require(member.mode == wanted['mode'] and member.uid == wanted['uid']
                    and member.gid == wanted['gid'], 'Archive metadata differs: '+name)
            # PAX timestamps retain fractions of a second; the tolerance only
            # accommodates float rounding at Unix epoch scale, not changed times.
            require(abs(member.mtime - wanted['mtime_ns'] / 1_000_000_000) < 0.001,
                    'Archive timestamp differs: '+name)
            if member.islnk():
                # A tar hardlink must refer to a file *inside* the same
                # inventoried backup set, never an external pathname.
                link_target = relative_safe(member.linkname)
                require(link_target in snapshot['entries'] and
                        snapshot['entries'][link_target]['kind'] == 'file',
                        'Untrusted archive hardlink target: '+name)
            if member.isdir():
                require(wanted['kind'] == 'directory', 'Directory mismatch')
                actual[name] = {'kind':'directory'}
            elif member.issym():
                require(wanted['kind'] == 'symlink' and member.linkname == wanted['link'],
                        'Symlink mismatch: '+name)
                actual[name] = {'kind':'symlink'}
            elif member.isfile() or member.islnk():
                require(wanted['kind'] == 'file', 'File mismatch: '+name)
                require(member.size in (0, wanted['size']) if member.islnk()
                        else member.size == wanted['size'],
                        'File size mismatch: '+name)
                inp = tar.extractfile(member)
                require(inp is not None, 'Cannot verify archived member: '+name)
                h = hashlib.sha256()
                count = 0
                with inp:
                    for chunk in iter(lambda: inp.read(BUFFER_SIZE), b''):
                        count += len(chunk)
                        require(count <= wanted['size'], 'Archive member overrun: '+name)
                        h.update(chunk)
                require(count == wanted['size'] and h.hexdigest() == wanted['sha256'],
                        'File bytes differ: '+name)
                actual[name] = {'kind':'file'}
            else:
                raise Refuse('Unexpected tar special member: '+name)
    require(set(actual) == set(snapshot['entries']), 'Archive missing entries')
    return True


def total_report(home):
    snapshots = {}
    for group in GROUPS:
        snapshots[group] = scan_group(home, group, hash_files=False)
    return snapshots


def print_report(snapshots):
    for group, snapshot in snapshots.items():
        print('%-21s %10d bytes %6d files %6d entries' %
              (group, snapshot['bytes'], snapshot['files'], len(snapshot['entries'])))
    print('Combined source bytes:',sum(s['bytes'] for s in snapshots.values()))


def backup(home, mount, *, strict_external=True, archive_stamp=None):
    home = Path(home)
    mount = validate_external(home, mount) if strict_external else Path(mount)
    sizes = total_report(home)
    need = sum(x['bytes'] for x in sizes.values()) + 128*1024*1024
    require(shutil.disk_usage(mount).free > need,
            'External drive lacks conservative free-space reserve')
    archive_stamp = archive_stamp or datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = mount / ('AutoXY_Historical_Backups_' + archive_stamp)
    target.mkdir(mode=0o700, exist_ok=False)
    receipt = {'schema':1, 'policy':'NO_DELETE_NO_PI_WRITES',
               'created_utc':archive_stamp, 'groups':{}}
    for group in GROUPS:
        print('Archiving:',group,flush=True)
        before = scan_group(home, group, hash_files=True)
        path = target / (group+'.tar.gz')
        archive_group(home, before, path)
        verify_archive(before, path)
        after = scan_group(home, group, hash_files=True)
        require(source_manifest_equal(before,after), 'Backup changed during archive: '+group)
        receipt['groups'][group] = {
            'archive':path.name,
            'archive_sha256':digest(path),
            'archive_bytes':path.stat().st_size,
            'source_bytes':before['bytes'],
            'source_files':before['files'],
            'manifest':before,
        }
        print('ARCHIVE VERIFIED:',group,flush=True)
    receipt_path = target/'receipt.json'
    with receipt_path.open('x', encoding='utf-8') as stream:
        json.dump(receipt,stream,indent=2,sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    print('ARCHIVE SET VERIFIED:',target)
    print('Private receipt:',receipt_path)
    print('Original files: UNCHANGED. NOTHING DELETED.')
    return target



# Private binary protocol for an SSH-to-PC transfer. All archive bytes go
# directly to stdout; nothing is staged or deleted on the printer.
NETWORK_MAGIC = b'AUTOXY_RC16_BACKUP_STREAM_V1\\n'.replace(b'\\n', b'\n')


def network_stream_group(home, group, output):
    """Stream one allowlisted tar.gz plus its source inventory to the PC."""
    before = scan_group(home, group, hash_files=True)
    manifest = json.dumps(before, sort_keys=True).encode('utf-8')
    require(len(manifest) <= 15*1024*1024, 'Source manifest exceeds transfer bound')
    output.write(NETWORK_MAGIC)
    output.write(str(len(manifest)).encode('ascii') + b'\n')
    output.write(manifest)
    with tarfile.open(fileobj=output, mode='w|gz', compresslevel=1,
                      format=tarfile.PAX_FORMAT, dereference=False) as tar:
        for name in GROUPS[group]:
            tar.add(Path(home)/name, arcname=name, recursive=True)
    output.flush()
    after = scan_group(home, group, hash_files=True)
    require(source_manifest_equal(before, after),
            'Backup changed while streaming group: '+group)


def verify_set(home, target, *, strict_external=True):
    home, target = Path(home), Path(target)
    require(target.is_dir() and not target.is_symlink(), 'Missing archive directory')
    if strict_external:
        validate_external(home, target.parent)
    receipt_path = target/'receipt.json'
    require(receipt_path.is_file() and not receipt_path.is_symlink() and
            receipt_path.stat().st_size < 15*1024*1024,
            'Missing or oversized private manifest')
    data = json.loads(receipt_path.read_text(encoding='utf-8'))
    require(data.get('schema') == 1 and data.get('policy') == 'NO_DELETE_NO_PI_WRITES',
            'Unrecognized receipt version')
    require(set(data.get('groups',{})) == set(GROUPS),'Incomplete archive group set')
    for group, info in data['groups'].items():
        require(isinstance(info, dict) and info.get('archive') == group+'.tar.gz',
                'Untrusted archive name')
        dest = target/(group+'.tar.gz')
        require(digest(dest) == info.get('archive_sha256'), 'Archive digest mismatch: '+group)
        snapshot = info.get('manifest')
        require(snapshot and snapshot.get('group') == group and
                set(GROUPS[group]).issubset(snapshot.get('entries',{})),
                'Incomplete source manifest: '+group)
        verify_archive(snapshot, dest)
        live = scan_group(home, group, hash_files=True)
        require(source_manifest_equal(live,snapshot),
                'Source changed since archive; re-evaluate before any deletion: '+group)
        print('REVERIFIED:',group,flush=True)
    print('SUCCESS: External archive matches current sources; no deletion performed.')
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('plan')
    arch = sub.add_parser('archive')
    arch.add_argument('--drive',required=True,help='EXACT mount point of a separate USB drive')
    remote = sub.add_parser('network-stream', help='Binary archive stream for authenticated SSH client only')
    remote.add_argument('--group', required=True, choices=sorted(GROUPS))
    check = sub.add_parser('verify')
    check.add_argument('--archive-dir',required=True,help='Exact verified archive set directory')
    args = parser.parse_args(argv)
    try:
        if args.action == 'network-stream':
            network_stream_group(HOME, args.group, sys.stdout.buffer)
            return 0
        if args.action == 'plan':
            print('READ ONLY: historical backup and retained config ZIP sizes')
            print_report(total_report(HOME))
            print('NO FILES WRITTEN OR DELETED')
        elif args.action == 'archive':
            backup(HOME,args.drive)
        else:
            verify_set(HOME,args.archive_dir)
        return 0
    except (Refuse,ValueError,OSError,EOFError,tarfile.TarError) as exc:
        print('REFUSED:',exc,file=sys.stderr)
        print('NO SOURCE FILES DELETED. Check the external drive for incomplete archives.',file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())