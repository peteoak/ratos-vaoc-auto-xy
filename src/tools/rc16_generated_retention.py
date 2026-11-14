#!/usr/bin/env python3
"""RC16 automatic generated-G-code retention worker.

Automatic use: --auto (enabled by RC16 Klipper coordinator once per day).
Manual use: default dry-run, or --execute --confirm DELETE_RC16_EXPIRED.
Never run in Klipper's reactor; only this subprocess may be niced.
Delete exclusively authenticated, aged RC16 generated files/registry pairs;
never originals, unknowns, symlinks, modified files, or active/selected files.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

PAT = re.compile(r'^__AutoXY_RC16_([0-9a-f]{24}|[A-Za-z0-9][A-Za-z0-9_-]{0,59}_[0-9]{8}-[0-9]{6}-[0-9]{6})\.gcode$')
SHA = re.compile(r'^[0-9a-f]{64}$')
CHUNK = 128 * 1024
DEFAULT_DAYS = 30
MAX_SCAN = 500
MAX_DELETE = 10
LOCK_NAME = '.rc16_retention.lock'
REPORT_NAME = 'retention-last-run.json'


class Refuse(RuntimeError):
    pass


def fingerprint(s):
    return (s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns, s.st_size)


def safe_dirs(root, registry):
    root, registry = Path(root).absolute(), Path(registry).absolute()
    if (not root.is_dir() or root.is_symlink() or
            not registry.is_dir() or registry.is_symlink()):
        raise Refuse('Missing or unsafe G-code/registry directory')
    return root, registry


@contextlib.contextmanager
def ownership_lock(registry, shared=False):
    """Shared by the RC16 print coordinator: cleanup requires exclusive lock.

    Refuse a cleanup if print preparation or printing holds the shared lock.
    The lock never changes permissions on or deletes any other file.
    """
    registry = Path(registry)
    if not registry.is_dir() or registry.is_symlink():
        raise Refuse('Unsafe registry lock directory')
    name = registry / LOCK_NAME
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(str(name), flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refuse('Unsafe registry lock target')
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise Refuse('Print preparation or another cleanup owns the retention lock') from exc
        try:
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def sha_file(path):
    """Read and hash without following final symlinks; refuse concurrent writes."""
    h = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise Refuse('Unexpected non-file G-code')
        for block in iter(lambda: stream.read(CHUNK), b''):
            h.update(block)
        after = os.fstat(stream.fileno())
    if fingerprint(before) != fingerprint(after) or fingerprint(before) != fingerprint(path.stat()):
        raise Refuse('File changed during digest')
    return h.hexdigest()


def moonraker_idle():
    """Fail closed on unavailable/incomplete Moonraker or selected virtual SD file."""
    with urlopen('http://127.0.0.1:7125/printer/objects/query?print_stats&virtual_sdcard',
                 timeout=8) as response:
        result = json.load(response)['result']['status']
    stats = result['print_stats']['state']
    v_sd = result['virtual_sdcard']
    if (stats not in ('standby', 'complete', 'cancelled', 'error') or
            v_sd.get('is_active') is not False or
            v_sd.get('file_path') not in (None, '')):
        raise Refuse('Printer is printing, paused or has a selected virtual-SD file')
    return True


def candidates(root, registry, now=None, age_days=DEFAULT_DAYS,
               max_scan=MAX_SCAN, max_candidates=MAX_DELETE):
    now = time.time() if now is None else now
    if age_days < 1 or max_scan < 1 or max_candidates < 1:
        raise Refuse('Unsafe retention limits')
    root, registry = safe_dirs(root, registry)
    result = []
    scanned = 0
    for record in sorted(registry.glob('*.json')):
        scanned += 1
        if scanned > max_scan or len(result) >= max_candidates:
            break
        try:
            if (record.name == REPORT_NAME or record.is_symlink() or
                    not record.is_file() or record.stat().st_size > 4096):
                continue
            record_stat = record.stat()
            if now - record_stat.st_mtime < age_days * 86400:
                continue
            data = json.loads(record.read_text(encoding='utf-8'))
            name = data.get('stage_name')
            if (data.get('schema') != 1 or not isinstance(name, str) or
                    not SHA.fullmatch(str(data.get('output_sha256', '')))):
                continue
            match = PAT.fullmatch(name)
            if not match or record.name != match.group(1) + '.json':
                continue
            generated = root / name
            if generated.is_symlink() or not generated.is_file():
                continue
            file_stat = generated.stat()
            # A recently rewritten/recreated output is never expired.
            if now - file_stat.st_mtime < age_days * 86400:
                continue
            if sha_file(generated) != data['output_sha256']:
                continue
            result.append((generated, record, data['output_sha256'],
                           fingerprint(file_stat), fingerprint(record_stat)))
        except (OSError, ValueError, KeyError, Refuse):
            # A corrupt/unknown entry is not evidence that its file belongs to RC16.
            continue
    return result



def pending_candidates(root, registry, now=None, age_days=DEFAULT_DAYS,
                       max_candidates=MAX_DELETE):
    """Expire ONLY authenticated AutoXY pending stages, not unknown files.

    A stale incomplete stage may have no final reprint registry. A pending
    marker records its ownership and checksum from the AutoXY worker.
    A file altered after that marker was written is never silently removed.
    """
    root, registry = safe_dirs(root, registry)
    now = time.time() if now is None else now
    results = []
    for marker in sorted(registry.glob('*.pending.json')):
        if len(results) >= max_candidates:
            break
        try:
            match = re.fullmatch(r'([0-9a-f]{24}|[A-Za-z0-9][A-Za-z0-9_-]{0,59}_[0-9]{8}-[0-9]{6}-[0-9]{6})\.pending\.json', marker.name)
            if not match or marker.is_symlink() or not marker.is_file():
                continue
            st = marker.stat()
            if st.st_size > 4096 or now - st.st_mtime < age_days * 86400:
                continue
            final_record = registry / (match.group(1) + '.json')
            if final_record.exists() or final_record.is_symlink():
                continue
            info = json.loads(marker.read_text(encoding='utf-8'))
            name = '__AutoXY_RC16_' + match.group(1) + '.gcode'
            if (info.get('kind') != 'autoxy_pending_stage' or
                    info.get('schema') != 1 or info.get('stage_name') != name or
                    not SHA.fullmatch(str(info.get('sha256', ''))) or
                    type(info.get('bytes')) is not int or info['bytes'] < 0):
                continue
            file = root / name
            if file.is_symlink():
                continue
            if file.exists():
                if not file.is_file():
                    continue
                fst = file.stat()
                if fst.st_size != info['bytes'] or now - fst.st_mtime < age_days * 86400:
                    continue
                if sha_file(file) != info['sha256']:
                    continue
                snapshot = fingerprint(fst)
            else:
                snapshot = None  # leftover marker before generated publication
            results.append((file, marker, info['sha256'],
                            snapshot, fingerprint(st)))
        except (OSError, ValueError, KeyError, Refuse):
            continue
    return results


def clean(root, registry, execute=False, confirm='', age_days=DEFAULT_DAYS,
          idle_probe=moonraker_idle, now=None, auto=False,
          max_delete=MAX_DELETE):
    if execute and not auto and confirm != 'DELETE_RC16_EXPIRED':
        raise Refuse('Explicit deletion confirmation required')
    if auto and not execute:
        raise Refuse('Automatic cleanup must explicitly request execution')
    root, registry = safe_dirs(root, registry)
    if not execute:
        found = candidates(root, registry, now=now, age_days=age_days,
                           max_candidates=max_delete)
        return {'dry_run': True, 'eligible_count': len(found), 'deleted_count': 0}
    # Cleanup serializes with the coordinator. It will skip rather than block
    # a print. Check Moonraker both before scanning and before EACH deletion.
    with ownership_lock(registry):
        idle_probe()
        found = candidates(root, registry, now=now, age_days=age_days,
                           max_candidates=max_delete)
        deleted = 0
        # Authenticate and remove registered files first; allow pending
        # artifacts to use the remaining bounded deletion slots.
        pending = pending_candidates(root, registry, now=now, age_days=age_days,
                                     max_candidates=max(1, max_delete - len(found)))
        if len(found) >= max_delete:
            pending = []
        for generated, record, expected, initial_file, initial_record in found:
            try:
                idle_probe()
                if generated.is_symlink() or record.is_symlink():
                    continue
                if (fingerprint(generated.stat()) != initial_file or
                        fingerprint(record.stat()) != initial_record):
                    continue
                if sha_file(generated) != expected:
                    continue
                cutoff = (time.time() if now is None else now) - age_days * 86400
                if (generated.stat().st_mtime >= cutoff or
                        record.stat().st_mtime >= cutoff):
                    continue
                # A second probe narrows any out-of-band native-API start race.
                # A cooperating RC16 print holds a shared lock for its lifetime.
                idle_probe()
                if (fingerprint(generated.stat()) != initial_file or
                        fingerprint(record.stat()) != initial_record):
                    continue
                generated.unlink()
                record.unlink()
                deleted += 1
            except Refuse:
                # If the printer became busy, STOP the whole pass immediately.
                break
            except (OSError, ValueError):
                continue
        pending_deleted = 0
        for generated, marker, expected, initial_file, initial_marker in pending:
            try:
                idle_probe()
                if marker.is_symlink() or fingerprint(marker.stat()) != initial_marker:
                    continue
                # Never remove an owned file that later received a successful
                # final registry entry.
                final = registry / (marker.name[:-len('.pending.json')] + '.json')
                if final.exists() or final.is_symlink() or generated.is_symlink():
                    continue
                cutoff = (time.time() if now is None else now) - age_days * 86400
                if marker.stat().st_mtime >= cutoff:
                    continue
                if initial_file is not None:
                    if (not generated.is_file() or
                            fingerprint(generated.stat()) != initial_file or
                            generated.stat().st_mtime >= cutoff or
                            sha_file(generated) != expected):
                        continue
                elif generated.exists():
                    continue
                idle_probe()
                if fingerprint(marker.stat()) != initial_marker:
                    continue
                if initial_file is not None:
                    if fingerprint(generated.stat()) != initial_file:
                        continue
                    generated.unlink()
                marker.unlink()
                pending_deleted += 1
            except Refuse:
                break
            except (OSError, ValueError):
                continue
        deleted += pending_deleted
    return {'dry_run': False, 'eligible_count': len(found) + len(pending),
            'deleted_count': deleted, 'pending_deleted_count': pending_deleted}


def write_report(registry, result):
    """Record last automatic cleanup summary without storing G-code or paths."""
    registry = Path(registry)
    result = dict(result)
    result['completed_at_epoch'] = int(time.time())
    fd, temporary = tempfile.mkstemp(prefix='.rc16-retention-', dir=str(registry))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(result, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, registry / REPORT_NAME)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', required=True)
    ap.add_argument('--registry', required=True)
    ap.add_argument('--days', type=int, default=DEFAULT_DAYS)
    ap.add_argument('--max-delete', type=int, default=MAX_DELETE)
    action = ap.add_mutually_exclusive_group()
    action.add_argument('--execute', action='store_true')
    action.add_argument('--auto', action='store_true')
    ap.add_argument('--confirm', default='')
    args = ap.parse_args(argv)
    try:
        if args.auto or args.execute:
            if hasattr(os, 'nice'):
                os.nice(10)
        result = clean(args.root, args.registry, execute=args.auto or args.execute,
                       confirm=args.confirm, age_days=args.days,
                       auto=args.auto, max_delete=args.max_delete)
        if args.auto:
            write_report(args.registry, result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refuse, OSError, ValueError, KeyError) as exc:
        print('RC16 cleanup refused: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
