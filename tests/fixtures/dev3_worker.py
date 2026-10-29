#!/usr/bin/env python3
"""RC15 dev2: persistent, fresh-HTTP-per-request vision worker with timing metadata.

The original RC14 standalone CLI remains supported.
Serve protocol is newline-delimited JSON, one reply per request. Never reuse an
image, detector result, or image acquisition across requests.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen
from auto_xy_detector import detect, register_nozzle_features

MAX_REQUEST_BYTES = 8192


def capture(a):
    started = time.monotonic()
    roi = None if a.roi.strip().lower() == 'auto' else tuple(int(k) for k in a.roi.split(','))
    if roi is not None and (len(roi) != 4 or any(v < 0 for v in roi) or roi[2] == 0 or roi[3] == 0):
        raise ValueError('Invalid ROI')
    if not 1 <= a.timeout <= 12:
        raise ValueError('Camera timeout outside allowed range')
    if bool(a.registration_anchor) != bool(a.registration_anchor_center):
        raise ValueError('Registration anchor file and centre must be supplied together')
    image = Path(a.image)
    image.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    req = Request(a.url, headers={'Accept': 'image/jpeg', 'Cache-Control': 'no-cache'})
    with urlopen(req, timeout=a.timeout) as res:
        data = res.read(12_000_001)
    fetched = time.monotonic()
    if (not 1000 < len(data) <= 12_000_000 or not data.startswith(b'\xff\xd8')
            or not data.endswith(b'\xff\xd9')):
        raise ValueError('Snapshot was missing, truncated or not a complete JPEG')
    image.write_bytes(data)
    written = time.monotonic()
    expected_center = None
    if a.expected_center is not None:
        expected_center = [float(k) for k in a.expected_center.split(',')]
    circle = detect(image, roi=roi, annotate=image.with_name('detected_' + image.name),
                    expected_center=expected_center, expected_radius=a.expected_radius)
    detected = time.monotonic()
    if not 115 <= float(circle[2]) <= 185:
        raise ValueError('Nozzle circle radius invalid')
    result = {'center_px': [float(circle[0]), float(circle[1])],
              'radius_px': float(circle[2]), 'sha256': hashlib.sha256(data).hexdigest()}
    if a.registration_anchor:
        anchor_center = [float(k) for k in a.registration_anchor_center.split(',')]
        registration = register_nozzle_features(a.registration_anchor, image,
                                                anchor_center, result['center_px'])
        result['center_px'] = registration['center_px']
        result['registration'] = registration
    done = time.monotonic()
    result['snapshot_timeline_monotonic_s'] = {
        'request_started':round(t0,6),
        'jpeg_received':round(fetched,6),
    }
    result['timing_s'] = {
        'http_fetch': round(fetched-t0, 5),
        'jpeg_write': round(written-fetched, 5),
        'detect_and_annotate': round(detected-written, 5),
        'registration': round(done-detected, 5),
        'worker_total': round(done-started, 5),
    }
    return result


def parse_args(argv=None):
    a = argparse.ArgumentParser()
    a.add_argument('--serve', action='store_true')
    a.add_argument('--url', default='http://127.0.0.1:8080/?action=snapshot')
    a.add_argument('--image')
    a.add_argument('--roi', default='auto')
    a.add_argument('--timeout', type=float, default=6.)
    a.add_argument('--expected-center', default=None)
    a.add_argument('--expected-radius', type=float, default=None)
    a.add_argument('--registration-anchor', default=None)
    a.add_argument('--registration-anchor-center', default=None)
    return a.parse_args(argv)


ALLOWED = frozenset(('request_id', 'url', 'image', 'roi', 'timeout', 'expected_center',
                     'expected_radius', 'registration_anchor', 'registration_anchor_center'))


def serve():
    for raw in sys.stdin:
        request_id=None
        try:
            if len(raw.encode('utf-8')) > MAX_REQUEST_BYTES:
                raise ValueError('Request exceeds size limit')
            req = json.loads(raw)
            if not isinstance(req, dict) or set(req) - ALLOWED or 'image' not in req:
                raise ValueError('Invalid vision request')
            request_id=req.pop('request_id',None)
            if request_id is not None and (type(request_id) is not int or
                                          not 0 < request_id <= 2147483647):
                raise ValueError('Invalid request ID')
            if not all(isinstance(req[k], str) for k in ('image',)):
                raise ValueError('Invalid image path')
            # Generate fresh Namespace each time. No per-request data caching.
            a = parse_args([])
            for key, value in req.items():
                if key == 'timeout' or key == 'expected_radius':
                    value = float(value) if value is not None else None
                if key not in ('expected_center','expected_radius','registration_anchor',
                               'registration_anchor_center') and value is None:
                    raise ValueError('Missing required value for '+key)
                setattr(a, key, value)
            reply = {'ok': True, 'result': capture(a), 'request_id':request_id}
        except Exception as exc:
            reply = {'ok': False, 'error': str(exc)[:500],
                     'request_id': locals().get('request_id')}
        sys.stdout.write(json.dumps(reply, allow_nan=False, separators=(',', ':')) + '\n')
        sys.stdout.flush()


def main():
    args = parse_args()
    if args.serve:
        serve()
        return
    if args.image is None:
        raise ValueError('--image is required unless --serve')
    print(json.dumps(capture(args), allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('VISION FAILURE: ' + str(exc), file=sys.stderr)
        sys.exit(2)
