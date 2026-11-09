#!/usr/bin/env python3
"""RC16 experimental *read-only* G-code metadata worker.

Never invokes RatOS/Klipper, never transforms G-code and NEVER authorises a print.
Only basic metadata extraction and one streaming SHA256 read. The protected-print
bridge is a separate, unimplemented step; do not use this as a print gate.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

MAX_LINE = 64 * 1024
MAX_START = 8 * 1024
START = re.compile(rb'^\s*START_PRINT(?:\s|$)', re.I)
PARAM = re.compile(rb'([A-Za-z_][A-Za-z_0-9]*)=([^\s;]+)')


class InspectError(RuntimeError):
    pass


def source_path(root, relative):
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise InspectError('Invalid source root')
    relative = Path(relative)
    if (relative.is_absolute() or not relative.parts or
            '..' in relative.parts or relative.suffix.lower() != '.gcode'):
        raise InspectError('Unsafe source name')
    # Reject symlinks in source components, not just the final path.
    current = root
    for part in relative.parts:
        if part in ('.', ''):
            continue
        current = current / part
        try:
            st = current.lstat()
        except OSError as exc:
            raise InspectError('Source path unavailable') from exc
        if stat.S_ISLNK(st.st_mode):
            raise InspectError('Symlinked source path')
    if not current.is_file():
        raise InspectError('Source must be an ordinary G-code file')
    return current


def inspect(root, relative, chunk_size=128 * 1024):
    if not 1024 <= chunk_size <= 1024 * 1024:
        raise InspectError('Invalid chunk bound')
    source = source_path(root, relative)
    before = source.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
        raise InspectError('Empty or unsafe G-code source')
    started = time.monotonic()
    digest = hashlib.sha256()
    count = 0
    matches = 0
    start_params = None
    pending = b''
    with source.open('rb') as stream:
        after_open = os.fstat(stream.fileno())
        if (before.st_ino, before.st_dev) != (after_open.st_ino, after_open.st_dev):
            raise InspectError('Source changed before read')
        while True:
            block = stream.read(chunk_size)
            if not block:
                break
            count += len(block)
            digest.update(block)
            pending += block
            while True:
                pos = pending.find(b'\n')
                if pos < 0:
                    if len(pending) > MAX_LINE:
                        raise InspectError('G-code line exceeds bounded scanner length')
                    break
                line, pending = pending[:pos+1], pending[pos+1:]
                if len(line) > MAX_LINE:
                    raise InspectError('G-code line exceeds bounded scanner length')
                if START.match(line):
                    matches += 1
                    if len(line) > MAX_START:
                        raise InspectError('Oversized START_PRINT line')
                    if matches > 1:
                        raise InspectError('Multiple START_PRINT lines; refuse ambiguous metadata')
                    fields = {}
                    for key, val in PARAM.findall(line.split(b';', 1)[0]):
                        key = key.decode('ascii').upper()
                        if key in fields:
                            raise InspectError('Duplicate START_PRINT parameter')
                        fields[key] = val.decode('ascii', 'strict')
                    start_params = fields
        if pending:
            if len(pending) > MAX_LINE:
                raise InspectError('G-code line exceeds bounded scanner length')
            if START.match(pending):
                matches += 1
                if len(pending) > MAX_START or matches > 1:
                    raise InspectError('Ambiguous START_PRINT line')
                fields = {}
                for key, val in PARAM.findall(pending.split(b';', 1)[0]):
                    key = key.decode('ascii').upper()
                    if key in fields:
                        raise InspectError('Duplicate START_PRINT parameter')
                    fields[key] = val.decode('ascii', 'strict')
                start_params = fields
        after = os.fstat(stream.fileno())
    # Protect the read-only scan against ordinary source replacement/edit races.
    current = source.stat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if signature(before) != signature(after) or signature(before) != signature(current):
        raise InspectError('Source changed during scan')
    # Result is informational only. No print-path permission is implied.
    return {'status': 'INSPECTED_NOT_AUTHORIZED',
            'bytes_read': count, 'sha256': digest.hexdigest(),
            'start_print_count': matches,
            'start_print_params': start_params,
            'elapsed_ms': round((time.monotonic() - started) * 1000, 2)}


def lower_own_priority():
    # Only the AutoXY worker process is reprioritised. Never touch RatOS/Klipper.
    if hasattr(os, 'nice'):
        os.nice(10)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', required=True, help='Relative G-code path under root')
    parser.add_argument('--nice', action='store_true', help='Lower only this worker priority')
    args = parser.parse_args(argv)
    try:
        if args.nice:
            lower_own_priority()
        result = inspect(args.root, args.source)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (InspectError, UnicodeError, OSError, ValueError) as exc:
        # No private G-code filename, contents or path in diagnostic message.
        print(json.dumps({'status': 'REJECTED_NOT_AUTHORIZED',
                          'reason': exc.__class__.__name__}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
