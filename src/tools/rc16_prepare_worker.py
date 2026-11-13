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
# Support old opaque names and new readable names with Pi-local timestamps.
GENERATED_ID = r'(?:[0-9a-f]{24}|[A-Za-z0-9][A-Za-z0-9_-]{0,59}_[0-9]{8}-[0-9]{6}-[0-9]{6})'
GENERATED_FILE = re.compile(r'^' + re.escape(PREFIX) + r'(' + GENERATED_ID + r')\.gcode$')
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
    is_generated = bool(GENERATED_FILE.fullmatch(current.name))
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



def pending_path(registry, stage_name):
    """Only AutoXY-generated filenames may have a pending ownership record."""
    match = GENERATED_FILE.fullmatch(stage_name)
    if not match:
        raise Refuse('Invalid pending stage token')
    return registry_directory(registry) / (match.group(1) + '.pending.json')


def write_pending(registry, stage_name, sha256, byte_count):
    if registry is None:
        return
    if not re.fullmatch(r'[0-9a-f]{64}', sha256) or byte_count < 0:
        raise Refuse('Invalid pending stage record')
    path = pending_path(registry, stage_name)
    record = {'schema': 1, 'kind': 'autoxy_pending_stage',
              'stage_name': stage_name, 'sha256': sha256,
              'bytes': byte_count}
    payload = json.dumps(record, sort_keys=True, separators=(',', ':')).encode('utf-8')
    # Use atomic replacement for the same token, never alter a symlink.
    if path.is_symlink():
        raise Refuse('Unsafe existing pending stage record')
    temporary = path.with_name('.' + path.name + '.tmp-' + secrets.token_hex(6))
    try:
        with open(temporary, 'xb') as output:
            os.chmod(temporary, 0o600)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def _allocate_stage_name(root, filename):
    # Keep basename; remove characters unsafe for a one-word RatOS SD token.
    label = re.sub(r'[^A-Za-z0-9_-]+', '_', Path(filename).stem)
    label = label.strip('_-')[:60].rstrip('_-') or 'print'
    root = Path(root).absolute()
    for _ in range(100):
        nanos = time.time_ns()
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(nanos // 1000000000))
        stamp += '-%06d' % ((nanos % 1000000000) // 1000)
        dest = root / (PREFIX + label + '_' + stamp + '.gcode')
        partial = dest.with_name('.' + dest.name + '.partial')
        if (not dest.exists() and not dest.is_symlink()
                and not partial.exists() and not partial.is_symlink()):
            return dest
        time.sleep(.001)
    raise Refuse('Could not allocate a unique dated AutoXY filename')


def stage(root, filename, registry=None):
    source = safe_path(root, filename)
    dest = _allocate_stage_name(root, filename)
    tmp = dest.with_name('.' + dest.name + '.partial')
    h = hashlib.sha256()
    total = 0
    meta_begin = meta_end = 0
    header_seen = None
    line_count = 0
    tail = b''
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
                    tail += block
                    while b'\n' in tail:
                        line, tail = tail.split(b'\n', 1)
                        if len(line) > MAX_LINE:
                            raise Refuse('Overlong source G-code line')
                        plain = line.rstrip(b'\r')
                        if line_count == 0:
                            header_seen = 'supported' if HEADER.fullmatch(plain) else ('unsupported' if HEADER_ANY.match(plain) else 'raw')
                        elif HEADER_ANY.match(plain):
                            raise Refuse('Additional RatOS header')
                        if MARKER in plain or AUTOXY.match(plain):
                            raise Refuse('Already protected source')
                        meta_begin += bool(plain.startswith(b'; ratos_meta begin '))
                        meta_end += bool(plain.startswith(b'; ratos_meta end '))
                        line_count += 1
                    if len(tail) > MAX_LINE:
                        raise Refuse('Overlong source G-code line')
                out.flush()
                os.fsync(out.fileno())
            if tail:
                if len(tail) > MAX_LINE:
                    raise Refuse('Overlong final line')
                plain = tail.rstrip(b'\r')
                if line_count == 0:
                    header_seen = 'supported' if HEADER.fullmatch(plain) else ('unsupported' if HEADER_ANY.match(plain) else 'raw')
                if MARKER in plain or AUTOXY.match(plain):
                    raise Refuse('Already protected source')
                meta_begin += bool(plain.startswith(b'; ratos_meta begin '))
                meta_end += bool(plain.startswith(b'; ratos_meta end '))
            if header_seen == 'unsupported' or (header_seen == 'supported' and (meta_begin != 1 or meta_end != 1)):
                raise Refuse('Unsupported RatOS processed source')
            if header_seen == 'raw' and (meta_begin or meta_end):
                raise Refuse('Unexpected RatOS metadata in raw source')
            if (identity(before) != identity(os.fstat(stream.fileno()))
                    or identity(before) != identity(source.stat())
                    or total != before.st_size):
                raise Refuse('Source changed while staging')
        write_pending(registry, dest.name, h.hexdigest(), total)
        # Publish without overwriting a concurrent or existing stage.
        os.link(tmp, dest)
        return {'status': 'STAGED_NOT_AUTHORIZED', 'stage_name': dest.name,
                'already_ratos_processed': header_seen == 'supported',
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


def prepare(root, stage_name, mode, registry=None):
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
        if registry is not None:
            write_pending(registry, stage_name, after_hash.hexdigest(), bytes_out)
        return {'status': 'PREPARED_NOT_AUTHORIZED',
                'stage_name': stage_name, 'protected': protected,
                'mode': mode, 'input_sha256': before_hash.hexdigest(),
                'output_sha256': after_hash.hexdigest(),
                'output_bytes': bytes_out, 'bytes_read': bytes_read,
                'elapsed_ms': round((time.monotonic()-started)*1000, 2)}
    finally:
        tmp.unlink(missing_ok=True)



def verify(root, stage_name, mode, expected_hash):
    """Post-transform immutable-fingerprint check; never authorises a print."""
    if mode not in ('independent', 'copy', 'mirror'):
        raise Refuse('Unknown physical mode')
    if not re.fullmatch('[0-9a-f]{64}', expected_hash or ''):
        raise Refuse('Invalid expected output digest')
    path = safe_path(root, stage_name, generated=True)
    digest = hashlib.sha256()
    count = marker_count = begin = end = 0
    read_bytes = 0
    shifts = None
    with open_reader(path) as reader:
        before = os.fstat(reader.fileno())
        if not stat.S_ISREG(before.st_mode) or identity(before) != identity(path.stat()):
            raise Refuse('Verified input changed')
        for lineno, line in enumerate(lines(reader), 1):
            plain = line.rstrip(b'\r\n')
            digest.update(line)
            read_bytes += len(line)
            if lineno == 1 and not HEADER.fullmatch(plain):
                raise Refuse('RatOS header changed')
            if lineno != 1 and HEADER_ANY.match(plain):
                raise Refuse('Duplicate RatOS header')
            begin += bool(plain.startswith(b'; ratos_meta begin '))
            end += bool(plain.startswith(b'; ratos_meta end '))
            marker_count += bool(plain == MARKER)
            if START.match(plain):
                count += 1
                _, shifts, _ = parse_start(line)
            if plain.startswith(b'AUTOXY_PRINT_BEGIN') and marker_count != 1:
                raise Refuse('Unexpected AutoXY command')
        after = os.fstat(reader.fileno())
    if identity(before) != identity(after) or identity(before) != identity(path.stat()):
        raise Refuse('Prepared file modified during verification')
    if begin != 1 or end != 1:
        raise Refuse('Missing RatOS metadata')
    if digest.hexdigest() != expected_hash:
        raise Refuse('Prepared file digest changed')
    if mode == 'independent' and marker_count == 1:
        # Protected file REPLACES START_PRINT with a fixed set of commands.
        if count != 0:
            raise Refuse('Protected G-code still contains native START_PRINT')
        with open_reader(path) as reader:
            # Avoid reading the whole file; validate required command presence
            # and exact cardinality with one bounded-memory stream.
            cmds = {key: 0 for key in (
                b'AUTOXY_PRINT_BEGIN', b'AUTOXY_PRINT_PREPARE',
                b'AUTOXY_PRINT_CALIBRATE', b'AUTOXY_PRINT_HOLD',
                b'AUTOXY_PRINT_CHECK', b'AUTOXY_PRINT_FINISH')}
            for line in lines(reader):
                key = line.strip().split(b' ', 1)[0]
                if key in cmds:
                    cmds[key] += 1
            if any(v != 1 for v in cmds.values()):
                raise Refuse('Missing or duplicate protected gate command')
        protected = True
    else:
        if marker_count or count != 1:
            raise Refuse('Unprotected G-code has unsafe start')
        protected = False
    return {'status': 'VERIFIED_NOT_AUTHORIZED', 'stage_name': stage_name,
            'mode': mode, 'protected': protected, 'sha256': expected_hash,
            'fingerprint': [before.st_dev, before.st_ino, before.st_size,
                            before.st_mtime_ns, before.st_ctime_ns],
            'bytes_read': read_bytes}


def registry_directory(registry):
    folder = Path(registry).absolute()
    if folder.is_symlink():
        raise Refuse('Unsafe registry directory')
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not folder.is_dir() or folder.is_symlink():
        raise Refuse('Invalid registry directory')
    return folder


def registry_key(filename):
    match = GENERATED_FILE.fullmatch(filename)
    if not match:
        raise Refuse('Invalid generated filename token')
    return match.group(1)


def register_source(root, stage_name, original, src_sha, final_sha, registry):
    """Record source mapping only AFTER a complete independent verification."""
    if not re.fullmatch(r'[0-9a-f]{64}', src_sha or '') or not re.fullmatch(r'[0-9a-f]{64}', final_sha or ''):
        raise Refuse('Invalid registry digest')
    stage_path = safe_path(root, stage_name, generated=True)
    src_path = safe_path(root, original)
    root_path = Path(root).absolute()
    if not src_path.is_relative_to(root_path):
        raise Refuse('Source outside SD root')
    # Read-only stream verifying output before recording, no hashing in Klipper.
    digest = hashlib.sha256()
    with open_reader(stage_path) as reader:
        before = os.fstat(reader.fileno())
        for block in iter(lambda: reader.read(CHUNK), b''):
            digest.update(block)
        after = os.fstat(reader.fileno())
    if identity(before) != identity(after) or identity(before) != identity(stage_path.stat()):
        raise Refuse('Generated G-code changed during registry validation')
    if digest.hexdigest() != final_sha:
        raise Refuse('Generated G-code digest mismatch')
    relative = str(src_path.relative_to(root_path))
    record = {'stage_name': stage_name, 'source': relative,
              'source_sha256': src_sha, 'output_sha256': final_sha,
              'schema': 1}
    path = registry_directory(registry) / (registry_key(stage_name) + '.json')
    data = json.dumps(record, sort_keys=True, separators=(',', ':')).encode('utf-8')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    pending = pending_path(registry, stage_name)
    if pending.is_file() and not pending.is_symlink():
        pending.unlink()
    return {'status': 'REGISTERED_NOT_AUTHORIZED', 'stage_name': stage_name,
            'output_sha256': final_sha}


def resolve_source(root, stage_name, registry):
    """Verified reprints re-run from the ORIGINAL source, never old AutoXY G-code."""
    stage_path = safe_path(root, stage_name, generated=True)
    key = registry_key(stage_name)
    rec_file = registry_directory(registry) / (key + '.json')
    if rec_file.is_symlink() or not rec_file.is_file() or rec_file.stat().st_size > 4096:
        raise Refuse('Missing or unsafe reprint registry entry')
    record = json.loads(rec_file.read_text(encoding='utf-8'))
    if (record.get('schema') != 1 or record.get('stage_name') != stage_name
            or not isinstance(record.get('source'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', record.get('output_sha256', ''))):
        raise Refuse('Registry entry mismatched')
    src_path = safe_path(root, record['source'])
    current = hashlib.sha256()
    with open_reader(stage_path) as reader:
        before = os.fstat(reader.fileno())
        for block in iter(lambda: reader.read(CHUNK), b''):
            current.update(block)
        after = os.fstat(reader.fileno())
    if (identity(before) != identity(after) or identity(before) != identity(stage_path.stat())
            or current.hexdigest() != record['output_sha256']):
        raise Refuse('Previously generated file changed; refuse reprint')
    # Original source may have been re-sliced/updated; stage() independently
    # fingerprints its current content before the NEW protected print starts.
    return {'status': 'RESOLVED_NOT_AUTHORIZED', 'source': record['source'],
            'stage_name': stage_name}

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('stage','prepare','verify','register','resolve'))
    parser.add_argument('--root', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--mode', choices=('independent','copy','mirror'))
    parser.add_argument('--expected-sha256')
    parser.add_argument('--registry')
    parser.add_argument('--original')
    parser.add_argument('--source-sha256')
    args = parser.parse_args(argv)
    try:
        # Only this AutoXY worker is deprioritised. RatOS is never a child.
        if hasattr(os, 'nice'):
            os.nice(10)
        if args.action == 'stage':
            result = stage(args.root, args.source, registry=args.registry)
        elif args.action == 'prepare':
            result = prepare(args.root, args.source, args.mode, registry=args.registry)
        elif args.action == 'verify':
            result = verify(args.root, args.source, args.mode, args.expected_sha256)
        elif args.action == 'register':
            if not args.registry or not args.original:
                raise Refuse('Missing source registry information')
            result = register_source(args.root, args.source, args.original, args.source_sha256,
                                     args.expected_sha256, args.registry)
        else:
            if not args.registry:
                raise Refuse('Missing registry directory')
            result = resolve_source(args.root, args.source, args.registry)
        print(json.dumps(result, separators=(',', ':')))
        return 0
    except (Refuse, OSError, UnicodeError, ValueError) as error:
        print(json.dumps({'status': 'REJECTED_NOT_AUTHORIZED',
                          'error_type': type(error).__name__}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
