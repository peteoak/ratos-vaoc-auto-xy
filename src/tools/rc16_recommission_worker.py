#!/usr/bin/env python3
"""RC16 full-frame nozzle-reference worker. Never moves tools or changes offsets.

Capture three independent JPEGs per nozzle. On successful validation, publish
the new profile atomically and mark the previous references RETIRED. Retired
references and exact previous JSON remain recoverable for at least 30 days.
Current references, unknown files and incomplete sessions are never cleaned.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
from auto_xy_detector import detect

REFERENCE_ROOT = Path('/home/pi/auto_xy/nozzle_references')
RESULTS_ROOT = Path('/home/pi/auto_xy/print_gate_runs')
FRAME_COUNT = 3
MAX_JPEG_BYTES = 12_000_000
MIN_JPEG_BYTES = 1000
FRAME_NAME = re.compile(r'^t[01]_[a-f0-9]{12}_[123]\.jpg$')
CAPTURE_ID = re.compile(r'^[a-f0-9]{12}$')


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def protected_session(session, reference_root=REFERENCE_ROOT, results_root=RESULTS_ROOT):
    session = Path(session)
    reference_root = Path(reference_root)
    results_root = Path(results_root)
    require(session.is_dir() and not session.is_symlink(), 'Invalid reference session')
    root = reference_root.resolve(strict=False)
    disposable = results_root.resolve(strict=False)
    folder = session.resolve(strict=True)
    require(root != disposable and root not in disposable.parents
            and disposable not in root.parents, 'Reference and disposable roots overlap')
    require(folder.parent == root, 'Reference session must be direct child of library')
    require(not folder.is_symlink(), 'Reference session may not be symlinked')
    return folder


def safe_frame(session, filename):
    path = Path(filename)
    require(FRAME_NAME.fullmatch(path.name) is not None
            and path.parent == session
            and path.is_file() and not path.is_symlink(),
            'Unrecognised or missing nozzle reference image')
    require(MIN_JPEG_BYTES < path.stat().st_size <= MAX_JPEG_BYTES,
            'Reference image outside allowed size')
    return path


def capture(session, tool, capture_id, camera_url, timeout=6.,
            reference_root=REFERENCE_ROOT, results_root=RESULTS_ROOT):
    folder = protected_session(session, reference_root, results_root)
    require(tool in (0, 1) and CAPTURE_ID.fullmatch(capture_id or '') is not None,
            'Invalid nozzle or capture ID')
    require(2. <= timeout <= 12., 'Camera timeout outside allowed range')
    names = [folder / ('t%d_%s_%d.jpg' % (tool, capture_id, i))
             for i in range(1, 4)]
    require(all(not p.exists() and not p.is_symlink() for p in names),
            'Capture destination already exists')
    hashes = set()
    stored = []
    for path in names:
        with urlopen(Request(camera_url, headers={
                'Accept': 'image/jpeg', 'Cache-Control': 'no-cache'}), timeout=timeout) as response:
            data = response.read(MAX_JPEG_BYTES + 1)
        require(MIN_JPEG_BYTES < len(data) <= MAX_JPEG_BYTES
                and data.startswith(bytes.fromhex('ffd8'))
                and data.endswith(bytes.fromhex('ffd9')),
                'Invalid/truncated camera JPEG')
        digest = sha(data)
        require(digest not in hashes, 'Camera repeated identical frame')
        hashes.add(digest)
        with path.open('xb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        stored.append(str(path))
        if len(stored) < 3:
            time.sleep(.35)
    return {'tool': tool, 'images': stored, 'sha256': sorted(hashes)}


def references(profile):
    """Reference paths for a commission profile; reject malformed metadata."""
    found = []
    for tool in ('tool0', 'tool1'):
        entry = profile.get(tool)
        require(isinstance(entry, dict), 'Missing profile ' + tool)
        frames = entry.get('reference_images', [])
        require(isinstance(frames, list), 'Invalid reference list')
        for item in frames:
            require(isinstance(item, dict) and
                    isinstance(item.get('path'), str) and
                    isinstance(item.get('sha256'), str),
                    'Invalid reference entry')
            found.append(item)
    return found


def validate_frames(session, frame_groups, camera_url,
                    reference_root=REFERENCE_ROOT, results_root=RESULTS_ROOT):
    folder = protected_session(session, reference_root, results_root)
    require(len(frame_groups) == 2, 'Both T0 and T1 required')
    used = set()
    profile = {'format': 1, 'status': 'COMMISSIONED_FROM_IMAGES',
               'manual_vaoc_confirmed': True,
               'manual_vaoc_confirmation': 'Native VAOC manually confirmed by operator',
               'commissioned_utc': datetime.now(timezone.utc).isoformat(),
               'camera_url': camera_url, 'roi': 'auto',
               'reference_schema': 'rc16-full-frame-raw-jpeg-v1'}
    for tool in (0, 1):
        images = frame_groups[tool]
        require(len(images) == 3, 'Three frames per nozzle required')
        circles, refs = [], []
        for name in images:
            path = safe_frame(folder, name)
            data = path.read_bytes()
            digest = sha(data)
            require(digest not in used, 'Duplicate T0/T1 camera frame')
            used.add(digest)
            try:
                # Legacy commissioning identifies circles; normal print
                # measurement remains on the tuned two_stage_roi pipeline.
                circle = detect(path, roi=None, detector_mode='legacy')
            except ValueError as exc:
                raise ValueError(
                    'T%d %s ambiguous or invalid; current profile preserved: %s' %
                    (tool, path.name, str(exc).split(';', 1)[0])) from exc
            require(len(circle) == 3 and all(math.isfinite(float(v)) for v in circle),
                    'Nonfinite nozzle circle')
            circles.append(tuple(float(v) for v in circle))
            refs.append({'path': str(path), 'sha256': digest})
        cx = sum(c[0] for c in circles) / 3.
        cy = sum(c[1] for c in circles) / 3.
        spread = max(math.hypot(c[0]-cx, c[1]-cy) for c in circles)
        radii = sorted(c[2] for c in circles)
        radius = radii[1]
        require(spread <= 10. and 115. <= radius <= 185.
                and max(abs(r-radius) for r in radii) <= 10.,
                'T%d nozzle centre/rim inconsistent across frames' % tool)
        profile['tool%d' % tool] = {
            'num_images': 3, 'radius_px': radius,
            'registration_center_px': [cx, cy],
            'max_spread_px': spread, 'reference_images': refs}
    return profile


def atomic_exclusive(path, data):
    with Path(path).open('xb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def publish(session, frame_groups, camera_url, profile_path, backup_dir,
            reference_root=REFERENCE_ROOT, results_root=RESULTS_ROOT,
            now=None):
    """On success, archive old profile+images for 30-day retirement."""
    folder = protected_session(session, reference_root, results_root)
    new_profile = validate_frames(folder, frame_groups, camera_url,
                                  reference_root, results_root)
    target = Path(profile_path)
    root = Path(reference_root).resolve(strict=False)
    archive = Path(backup_dir)
    require(not target.is_symlink() and target.parent.is_dir() and
            not target.parent.is_symlink(), 'Unsafe commissioning profile')
    require(archive.is_dir() and not archive.is_symlink() and
            archive.resolve(strict=True).parent == root,
            'Backup generations must be under reference library')
    before = target.read_bytes() if target.is_file() else None
    original = None
    if before is not None:
        original = json.loads(before)
        require(isinstance(original, dict), 'Unrecognised existing profile')
    # Preserve the exact old JSON before touching current profile.
    timestamp = time.time() if now is None else float(now)
    require(math.isfinite(timestamp), 'Invalid replacement timestamp')
    token = uuid.uuid4().hex
    backup = archive / ('camera_commission_%s.json' % token) if before else None
    marker = archive / ('retired_%s.json' % token) if before else None
    if backup:
        atomic_exclusive(backup, before)
    data = (json.dumps(new_profile, sort_keys=True, indent=2) + '\n').encode()
    fd, temporary = tempfile.mkstemp(prefix='.rc16-profile-', dir=str(target.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        require((target.read_bytes() if target.exists() else None) == before,
                'Profile changed during preparation; no replacement attempted')
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if backup:
        # Retire only old references. Their actual images remain in the
        # reference library and are never under print-gate results.
        record = {'schema': 1, 'retired_at_unix': timestamp,
                  'prior_profile_sha256': sha(before),
                  'backup_profile': str(backup),
                  'images': references(original)}
        atomic_exclusive(marker, (json.dumps(record, sort_keys=True) + '\n').encode())
    return {'status': 'COMMITTED_FULL_FRAME_REFERENCES', 'profile': str(target),
            'backup': str(backup) if backup else None,
            'retired_manifest': str(marker) if marker else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('capture', 'publish'))
    parser.add_argument('--session', required=True)
    parser.add_argument('--camera-url', required=True)
    parser.add_argument('--tool', type=int)
    parser.add_argument('--capture-id')
    parser.add_argument('--timeout', type=float, default=6.)
    parser.add_argument('--t0', nargs=3)
    parser.add_argument('--t1', nargs=3)
    parser.add_argument('--profile')
    parser.add_argument('--backup-dir')
    parser.add_argument('--reference-root', default=str(REFERENCE_ROOT))
    parser.add_argument('--results-root', default=str(RESULTS_ROOT))
    args = parser.parse_args(argv)
    if args.action == 'capture':
        response = capture(args.session, args.tool, args.capture_id, args.camera_url,
                           args.timeout, args.reference_root, args.results_root)
    else:
        require(args.t0 and args.t1 and args.profile and args.backup_dir,
                'Both full image sets and a profile/backup path required')
        response = publish(args.session, [args.t0, args.t1], args.camera_url,
                           args.profile, args.backup_dir, args.reference_root,
                           args.results_root)
    print(json.dumps(response, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('RC16 RECOMMISSION FAILED:', str(exc), file=sys.stderr)
        sys.exit(2)
