#!/usr/bin/env python3
"""RC16 nozzle reference capture/publish worker; never moves or heats printer.

Capture: three fresh unmodified JPEGs from the configured camera.
Publish: independently validate every frame, keep all six original images,
backup the old commissioning JSON, then atomically replace it.
No nozzle offsets or Klipper saved variables are accessed here.
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
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vision'))
sys.path.insert(0, str(ROOT))
from auto_xy_detector import detect

FRAME_COUNT = 3
MAX_JPEG_BYTES = 12_000_000
MIN_JPEG_BYTES = 1_000
CAPTURE_ID = re.compile(r'^[a-f0-9]{12}$')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_session(session):
    session = Path(session)
    require(session.is_dir() and not session.is_symlink(), 'Unknown/symlinked recommission session')
    return session.resolve(strict=True)


def safe_frame(session, name):
    path = Path(name)
    require(path.is_file() and not path.is_symlink() and
            path.parent.resolve(strict=True) == session,
            'Recommission image outside session or missing')
    require(re.fullmatch(r't[01]_[a-f0-9]{12}_[123]\.jpg', path.name) is not None,
            'Unexpected recommission image filename')
    return path


def capture(session, tool, capture_id, camera_url, timeout=6.):
    session = safe_session(session)
    require(tool in (0, 1), 'Only independent T0/T1 may be commissioned')
    require(CAPTURE_ID.fullmatch(capture_id) is not None, 'Invalid capture ID')
    require(2. <= timeout <= 12., 'Invalid camera timeout')
    files, hashes = [], set()
    for index in range(1, FRAME_COUNT + 1):
        dest = session / ('t%d_%s_%d.jpg' % (tool, capture_id, index))
        request = Request(camera_url, headers={
            'Accept': 'image/jpeg', 'Cache-Control': 'no-cache'})
        with urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_JPEG_BYTES + 1)
        require(MIN_JPEG_BYTES < len(data) <= MAX_JPEG_BYTES and
                data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'),
                'Missing/truncated camera JPEG; cannot use image as reference')
        digest = hashlib.sha256(data).hexdigest()
        require(digest not in hashes, 'Camera returned repeated JPEG; require three independent frames')
        hashes.add(digest)
        with dest.open('xb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        files.append(str(dest))
        if index != FRAME_COUNT:
            time.sleep(.35)
    return {'tool': tool, 'images': files, 'sha256': sorted(hashes)}


def validate_frames(session, frame_groups, camera_url):
    """Apply the RC15 acceptance criteria independently for each tool."""
    session = safe_session(session)
    require(len(frame_groups) == 2, 'Both nozzle image groups required')
    used = set()
    profile = {'format': 1, 'status': 'COMMISSIONED_FROM_IMAGES',
               'manual_vaoc_confirmed': True,
               'manual_vaoc_confirmation':
                   'Operator confirmed native VAOC positioning and completed T0/T1 captures; '
                   'not independently proven to represent physical extrusion-axis alignment',
               'commissioned_utc': datetime.now(timezone.utc).isoformat(),
               'camera_url': camera_url, 'roi': 'auto',
               'reference_schema': 'rc16-full-frame-raw-jpeg-v1'}
    for tool in (0, 1):
        paths = frame_groups[tool]
        require(len(paths) == FRAME_COUNT, 'Three independent images required for T%d' % tool)
        circles, references = [], []
        for name in paths:
            path = safe_frame(session, name)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            require(digest not in used, 'Recommission captures contain duplicate frames')
            used.add(digest)
            try:
                circle = detect(path, roi=None, detector_mode='legacy')
            except ValueError as exc:
                # No nearest-circle fallback, cropped ROI or unreviewed classifier.
                raise ValueError(
                    'T%d image %s failed full-frame validation: %s' %
                    (tool, path.name, str(exc).split(';', 1)[0])) from exc
            require(len(circle) == 3 and all(math.isfinite(float(v)) for v in circle),
                    'Nonfinite/invalid T%d camera circle' % tool)
            circles.append(tuple(float(v) for v in circle))
            references.append({'path': str(path), 'sha256': digest})
        xmean = sum(c[0] for c in circles) / FRAME_COUNT
        ymean = sum(c[1] for c in circles) / FRAME_COUNT
        maxspread = max(math.hypot(c[0]-xmean, c[1]-ymean) for c in circles)
        require(maxspread <= 10., 'T%d nozzle rim moved more than 10 pixels; recommission refused' % tool)
        radii = sorted(c[2] for c in circles)
        radius = radii[1]
        require(115. <= radius <= 185. and max(abs(r-radius) for r in radii) <= 10.,
                'T%d nozzle rim radius invalid or unstable' % tool)
        profile['tool%d' % tool] = {
            'num_images': FRAME_COUNT, 'radius_px': radius,
            'registration_center_px': [xmean, ymean],
            'max_spread_px': maxspread, 'reference_images': references}
    return profile


def publish(session, frame_groups, camera_url, profile_path, backup_dir):
    """No change to existing profile until every new frame has passed."""
    new_profile = validate_frames(session, frame_groups, camera_url)
    target = Path(profile_path)
    require(not target.is_symlink(), 'Commissioning profile may not be a symlink')
    require(target.parent.is_dir() and not target.parent.is_symlink(),
            'Existing commissioning directory required')
    backups = Path(backup_dir)
    require(backups.is_dir() and not backups.is_symlink(), 'Backup directory missing')
    old_bytes = target.read_bytes() if target.exists() else None
    if old_bytes is not None:
        # Keep a timestamped, immutable copy of the exact previous profile.
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        backup = backups / ('camera_commission_%s_%s.json' %
                            (stamp, hashlib.sha256(old_bytes).hexdigest()[:12]))
        with backup.open('xb') as stream:
            stream.write(old_bytes)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        backup = None
    data = (json.dumps(new_profile, sort_keys=True, indent=2) + '\n').encode('utf-8')
    fd, temp = tempfile.mkstemp(prefix='.rc16-commission-', dir=str(target.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        require(not target.is_symlink(), 'Profile changed into symlink during recommission')
        require((target.read_bytes() if target.exists() else None) == old_bytes,
                'Existing profile changed during validation; preserving external edit')
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {'profile': str(target), 'backup': str(backup) if backup else None,
            'tool0_reference_images': FRAME_COUNT,
            'tool1_reference_images': FRAME_COUNT,
            'status': 'COMMITTED_FULL_FRAME_REFERENCES'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('capture', 'publish'))
    parser.add_argument('--session', required=True)
    parser.add_argument('--camera-url', required=True)
    parser.add_argument('--tool', type=int)
    parser.add_argument('--capture-id')
    parser.add_argument('--timeout', type=float, default=6.)
    parser.add_argument('--t0', nargs=FRAME_COUNT)
    parser.add_argument('--t1', nargs=FRAME_COUNT)
    parser.add_argument('--profile')
    parser.add_argument('--backup-dir')
    args = parser.parse_args(argv)
    if args.action == 'capture':
        result = capture(args.session, args.tool, args.capture_id,
                         args.camera_url, args.timeout)
    else:
        require(args.t0 and args.t1 and args.profile and args.backup_dir,
                'Both nozzle image sets, profile and backup dir are required')
        result = publish(args.session, [args.t0, args.t1], args.camera_url,
                         args.profile, args.backup_dir)
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('RC16 RECOMMISSION FAILED:', str(exc), file=sys.stderr)
        sys.exit(2)
