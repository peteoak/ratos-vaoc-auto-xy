#!/usr/bin/env python3
"""RC16 generated-G-code retention, conservative offline-first utility.

Dry-run by default. Deletion requires explicit --execute --confirm, and
Moonraker must confirm that no print is running or paused. Only registry-owned,
expired, checksum-matching generated files are candidates. Unknown/modified
files, symlinks, incomplete records and failures are always preserved.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.request import urlopen

PREFIX = '__AutoXY_RC16_'
PAT = re.compile(r'^__AutoXY_RC16_([0-9a-f]{24})\.gcode$')
SHA = re.compile(r'^[0-9a-f]{64}$')
CHUNK = 128 * 1024
DEFAULT_DAYS = 30


class Refuse(RuntimeError):
    pass


def sha_file(path):
    h = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        for block in iter(lambda: stream.read(CHUNK), b''):
            h.update(block)
        after = os.fstat(stream.fileno())
    check = lambda s: (s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns, s.st_size)
    if check(before) != check(after) or check(before) != check(path.stat()):
        raise Refuse('File changed during digest')
    return h.hexdigest()


def moonraker_idle():
    with urlopen('http://127.0.0.1:7125/printer/objects/query?print_stats&virtual_sdcard',
                 timeout=8) as response:
        result = json.load(response)['result']['status']
    stats = result['print_stats']['state']
    v_sd = result['virtual_sdcard']
    if stats not in ('standby', 'complete', 'cancelled', 'error') or v_sd.get('is_active'):
        raise Refuse('Printer is active or paused: never delete generated G-code')
    return True


def candidates(root, registry, now=None, age_days=DEFAULT_DAYS):
    now = time.time() if now is None else now
    if age_days < 1:
        raise Refuse('Unsafe retention age')
    root, registry = Path(root).absolute(), Path(registry).absolute()
    if (not root.is_dir() or root.is_symlink()
            or not registry.is_dir() or registry.is_symlink()):
        raise Refuse('Missing or unsafe G-code/registry directory')
    result = []
    for record in sorted(registry.glob('*.json')):
        try:
            if record.is_symlink() or not record.is_file() or record.stat().st_size > 4096:
                continue
            if now - record.stat().st_mtime < age_days * 86400:
                continue
            data = json.loads(record.read_text(encoding='utf-8'))
            name = data.get('stage_name')
            if (data.get('schema') != 1 or not isinstance(name, str)
                    or not SHA.fullmatch(str(data.get('output_sha256', '')))):
                continue
            match = PAT.fullmatch(name)
            if not match or record.name != match.group(1) + '.json':
                continue
            generated = root / name
            if generated.is_symlink() or not generated.is_file():
                continue
            if sha_file(generated) != data['output_sha256']:
                continue
            # Bounded independent checks before the destructive action.
            result.append((generated, record, data['output_sha256']))
        except (OSError, ValueError, KeyError, Refuse):
            # Fail closed for unknown/corrupt files. Never guess ownership.
            continue
    return result


def clean(root, registry, execute=False, confirm='', age_days=DEFAULT_DAYS,
          idle_probe=moonraker_idle, now=None):
    if execute and confirm != 'DELETE_RC16_EXPIRED':
        raise Refuse('Explicit deletion confirmation required')
    result = candidates(root, registry, now=now, age_days=age_days)
    if not execute:
        return {'dry_run': True, 'eligible_count': len(result), 'deleted_count': 0}
    # Query the actual printer immediately before touching any files.
    idle_probe()
    deleted = 0
    for generated, record, expected in result:
        # Check every file once more after the printer idle check. The final
        # fstat/inode integrity check is performed in the worker, never Klipper.
        if generated.is_symlink() or record.is_symlink():
            continue
        try:
            if sha_file(generated) != expected:
                continue
            if record.stat().st_mtime >= (time.time() if now is None else now) - age_days * 86400:
                continue
            generated.unlink()
            record.unlink()
            deleted += 1
        except (OSError, Refuse):
            # Unknown or racing files remain protected. Report incomplete work.
            continue
    return {'dry_run': False, 'eligible_count': len(result), 'deleted_count': deleted}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', required=True)
    ap.add_argument('--registry', required=True)
    ap.add_argument('--days', type=int, default=DEFAULT_DAYS)
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--confirm', default='')
    args = ap.parse_args(argv)
    try:
        result = clean(args.root, args.registry, execute=args.execute,
                       confirm=args.confirm, age_days=args.days)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refuse, OSError, ValueError, KeyError) as exc:
        print('RC16 cleanup refused: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
