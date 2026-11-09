#!/usr/bin/env python3
"""RC16 experimental low-priority FILE worker. Never invokes RatOS or Klipper.

One stream for staging + source digest; one stream for post-RatOS validation
and optional protected START_PRINT transformation. All results are informational:
only a separately qualified Klipper coordinator may start a print.
"""
import argparse
import hashlib
import json
import math
import os
import re
import secrets
import stat
import sys
import time
from pathlib import Path

PREFIX = '__AutoXY_RC16_'
MARKER = b'; AutoXY v0.5 protected pre-Beacon two-phase start'
HEADER = re.compile(rb'^; processed by RatOS\.PostProcessor 1\.0\.0 .*\bidex\s*$', re.I)
HEADER_ANY = re.compile(rb'^; processed by RatOS\.PostProcessor\b', re.I)
START = re.compile(rb'^\s*START_PRINT(?:\s|$)', re.I)
START_ARGS = re.compile(rb'^\s*START_PRINT\s+([^;\r\n]+)(?:\s*;[^\r\n]*)?\s*$', re.I)
AUTOXY = re.compile(rb'^\s*AUTOXY_PRINT_\w+\b', re.I)
REQUIRED = {'EXTRUDER_TEMP', 'EXTRUDER_OTHER_LAYER_TEMP', 'BED_TEMP',
            'TOTAL_TOOLSHIFTS', 'INITIAL_TOOL'}
CHUNK = 128 * 1024
MAX_LINE = 64 * 1024
MAX_START = 8 * 1024


class Refuse(ValueError):
    pass


def identity(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def safe_path(root, relative, generated=False):
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise Refuse('Invalid virtual-SD root')
    text = str(relative)
    relative = Path(text)
    if (not text or '\\' in text or '\x00' in text or relative.is_absolute()
            or '..' in relative.parts or relative.suffix.lower() != '.gcode'):
        raise Refuse('Unsafe G-code filename')
    current = root
    for part in relative.parts:
        current /= part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise Refuse('Symlinked G-code path')
    if not current.is_file():
        raise Refuse('Source is not an ordinary file')
    is_generated = bool(re.fullmatch(re.escape(PREFIX) + r'[0-9a-f]{24}\.gcode', current.name))
    if generated and (current.parent != root or not is_generated):
        raise Refuse('Not a generated staging filename')
    if not generated and current.name.startswith(PREFIX):
        raise Refuse('Generated filename needs verified source mapping')
    return current


def lines(reader):
    pending = b''
    for chunk in iter(lambda: reader.read(CHUNK), b''):
        pending += chunk
        while True:
            at = pending.find(b'\n')
            if at < 0:
                if len(pending) > MAX_LINE:
                    raise Refuse('Overlong G-code line')
                break
            if at + 1 > MAX_LINE:
                raise Refuse('Overlong G-code line')
            yield pending[:at+1]
            pending = pending[at+1:]
    if pending:
        if len(pending) > MAX_LINE:
            raise Refuse('Overlong G-code line')
        yield pending


def open_reader(path):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    return os.fdopen(fd, 'rb')


def parse_start(line):
    match = START_ARGS.fullmatch(line.rstrip(b'\r\n'))
    if not match or len(line) > MAX_START:
        raise Refuse('Invalid START_PRINT format')
    try:
        text = match.group(1).decode('ascii')
    except UnicodeError as exc:
        raise Refuse('Non-ASCII START_PRINT') from exc
    params = {}
    for item in text.split():
        if item.count('=') != 1:
            raise Refuse('Malformed START_PRINT parameter')
        key, val = item.split('=', 1)
        key = key.upper()
        if not re.fullmatch('[A-Z][A-Z0-9_]*', key) or key in params:
            raise Refuse('Duplicate or invalid parameter')
        params[key] = val
    if not REQUIRED.issubset(params):
        raise Refuse('Missing required start data')
    try:
        shifts = int(params['TOTAL_TOOLSHIFTS'])
        initial = int(params['INITIAL_TOOL'])
        bed = float(params['BED_TEMP'])
        temps = [float(v) for k in ('EXTRUDER_TEMP', 'EXTRUDER_OTHER_LAYER_TEMP')
                 for v in params[k].split(',')]
        if (shifts < 0 or shifts > 100000 or initial not in (0, 1)
                or not math.isfinite(bed) or not 20 <= bed <= 125
                or len(temps) != 4
                or any(not math.isfinite(x) or not 0 <= x <= 350 for x in temps)):
            raise Refuse('Invalid print parameters')
    except (ValueError, OverflowError) as exc:
        raise Refuse('Invalid start temperatures or toolshifts') from exc
    return text.encode('ascii'), shifts, params


def stage(root, filename):
    source = safe_path(root, filename)
    dest = Path(root).absolute() / (PREFIX + secrets.token_hex(12) + '.gcode')
    tmp = dest.with_name('.' + dest.name + '.partial')
    h = hashlib.sha256()
    total = 0
    started = time.monotonic()
    try:
        with open_reader(source) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
                raise Refuse('Empty/unsafe source')
            if identity(before) != identity(source.stat()):
                raise Refuse('Source changed before stage')
            with open(tmp, 'xb') as out:
                for block in iter(lambda: stream.read(CHUNK), b''):
                    h.update(block)
                    out.write(block)
                    total += len(block)
                out.flush()
                os.fsync(out.fileno())
            if (identity(before) != identity(os.fstat(stream.fileno()))
                    or identity(before) != identity(source.stat())
                    or total != before.st_size):
                raise Refuse('Source changed while staging')
        os.replace(tmp, dest)
        return {'status': 'STAGED_NOT_AUTHORIZED', 'stage_name': dest.name,
                'source_sha256': h.hexdigest(), 'bytes_read': total,
                'elapsed_ms': round((time.monotonic()-started)*1000, 2)}
    finally:
        tmp.unlink(missing_ok=True)


def replacement(args, params, nl):
    parts = [MARKER, b'AUTOXY_PRINT_BEGIN BED_TEMP=' + params['BED_TEMP'].encode('ascii'),
             b'AUTOXY_PRINT_PREPARE ' + args,
             b'AUTOXY_PRINT_CALIBRATE', b'AUTOXY_PRINT_HOLD',
             b'AUTOXY_PRINT_CHECK', b'AUTOXY_PRINT_FINISH ' + args]
    return nl.join(parts) + nl


def prepare(root, stage_name, mode):
    if mode not in ('independent', 'copy', 'mirror'):
        raise Refuse('Unknown physical IDEX mode')
    source = safe_path(root, stage_name, generated=True)
    tmp = source.with_name('.' + source.name + '.prepare-' + secrets.token_hex(6))
    started = time.monotonic()
    before_hash, after_hash = hashlib.sha256(), hashlib.sha256()
    meta_begin = meta_end = starts = 0
    bytes_read = bytes_out = 0
    protected = False
    try:
        with open_reader(source) as reader:
            before = os.fstat(reader.fileno())
            if identity(before) != identity(source.stat()):
                raise Refuse('Generated file changed before preparation')
            with open(tmp, 'xb') as writer:
                for lineno, line in enumerate(lines(reader), 1):
                    plain = line.rstrip(b'\r\n')
                    bytes_read += len(line)
                    before_hash.update(line)
                    if lineno == 1 and not HEADER.fullmatch(plain):
                        raise Refuse('Unsupported RatOS IDEX processing header')
                    if lineno != 1 and HEADER_ANY.match(plain):
                        raise Refuse('Duplicate RatOS processing header')
                    meta_begin += bool(plain.startswith(b'; ratos_meta begin '))
                    meta_end += bool(plain.startswith(b'; ratos_meta end '))
                    if MARKER in line or AUTOXY.match(plain):
                        raise Refuse('Already protected G-code')
                    out = line
                    if START.match(plain):
                        starts += 1
                        if starts != 1:
                            raise Refuse('Multiple START_PRINT commands')
                        args, shifts, params = parse_start(line)
                        if mode in ('copy', 'mirror') and shifts:
                            raise Refuse('Unexpected toolchanges in COPY/MIRROR mode')
                        if mode == 'independent' and shifts > 0:
                            nl = b'\r\n' if line.endswith(b'\r\n') else b'\n'
                            out = replacement(args, params, nl)
                            protected = True
                    writer.write(out)
                    after_hash.update(out)
                    bytes_out += len(out)
                writer.flush()
                os.fsync(writer.fileno())
            if (identity(before) != identity(os.fstat(reader.fileno()))
                    or identity(before) != identity(source.stat())):
                raise Refuse('Generated file changed during preparation')
        if meta_begin != 1 or meta_end != 1 or starts != 1:
            raise Refuse('RatOS metadata or START_PRINT missing')
        if protected:
            os.replace(tmp, source)
        return {'status': 'PREPARED_NOT_AUTHORIZED',
                'stage_name': stage_name, 'protected': protected,
                'mode': mode, 'input_sha256': before_hash.hexdigest(),
                'output_sha256': after_hash.hexdigest(),
                'output_bytes': bytes_out, 'bytes_read': bytes_read,
                'elapsed_ms': round((time.monotonic()-started)*1000, 2)}
    finally:
        tmp.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('stage','prepare'))
    parser.add_argument('--root', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--mode', choices=('independent','copy','mirror'))
    args = parser.parse_args(argv)
    try:
        # Only this AutoXY worker is deprioritised. RatOS is never a child.
        if hasattr(os, 'nice'):
            os.nice(10)
        result = stage(args.root, args.source) if args.action == 'stage' else prepare(
            args.root, args.source, args.mode)
        print(json.dumps(result, separators=(',', ':')))
        return 0
    except (Refuse, OSError, UnicodeError, ValueError) as error:
        print(json.dumps({'status': 'REJECTED_NOT_AUTHORIZED',
                          'error_type': type(error).__name__}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
