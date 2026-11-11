#!/usr/bin/env python3
"""Read-only commissioning ROI diagnosis with OPTIONAL annotated preview files.

Examines previously saved camera frames. It never captures, moves, heats,
homes, sets offsets, selects a nozzle or writes a commissioning profile.
The labelled circles are only possibilities for a human to inspect.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from auto_xy_detector import MIN_RADIUS, MAX_RADIUS

MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 20_000_000


def parse_roi(value, width, height):
    if value == 'auto':
        return (0, 0, width, height)
    try:
        parts = [int(x.strip()) for x in value.split(',')]
    except ValueError as exc:
        raise ValueError('ROI must be auto or four integer x,y,w,h values') from exc
    if len(parts) != 4:
        raise ValueError('ROI must be auto or four integer x,y,w,h values')
    x, y, w, h = parts
    if x < 0 or y < 0 or w < 200 or h < 200 or x + w > width or y + h > height:
        raise ValueError('ROI outside image or too small; preserve the complete nozzle rim')
    return x, y, w, h


def inspect_image(path, roi='auto'):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError('Image must be an existing regular file')
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError('Image exceeds diagnostic byte limit')
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError('Cannot decode camera image')
    height, width = image.shape[:2]
    if width * height > MAX_PIXELS:
        raise ValueError('Image dimensions exceed diagnostic limit')
    x, y, w, h = parse_roi(roi, width, height)
    gray = cv2.cvtColor(image[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY)
    # Keep exactly the same nominal Hough parameters as _hough(), with NO
    # candidate ranking or tolerance loosening. Diagnostics are not accepted
    # coordinates and are never fed into calibration/offset writes.
    smoothed = cv2.GaussianBlur(gray, (7, 7), 1.5)
    found = cv2.HoughCircles(smoothed, cv2.HOUGH_GRADIENT, dp=1.25,
                             minDist=105, param1=105, param2=36,
                             minRadius=MIN_RADIUS, maxRadius=MAX_RADIUS)
    candidates = []
    if found is not None:
        for candidate in np.asarray(found[0], dtype=float):
            cx, cy, radius = map(float, candidate)
            if MIN_RADIUS <= radius <= MAX_RADIUS:
                candidates.append((cx+x, cy+y, radius))
    return image, {
        'file': path.name,
        'image_size_px': [width, height],
        'roi_xywh': [x, y, w, h],
        'candidate_count': len(candidates),
        'candidates_xy_radius_px': [
            [round(cx, 2), round(cy, 2), round(r, 2)]
            for cx, cy, r in candidates],
        'status': 'AMBIGUOUS' if len(candidates) > 1
                  else 'ONE_POSSIBILITY_NOT_COMMISSIONED' if candidates
                  else 'NO_CANDIDATES',
    }


def annotate(image, result, destination):
    """Write visual evidence only; refuse overwrite and never change source."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Diagnostic preview already exists; will not overwrite')
    x, y, w, h = result['roi_xywh']
    canvas = image.copy()
    cv2.rectangle(canvas, (x, y), (x+w-1, y+h-1), (255, 200, 0), 2)
    for number, (cx, cy, radius) in enumerate(result['candidates_xy_radius_px'], 1):
        center = (int(round(cx)), int(round(cy)))
        cv2.circle(canvas, center, int(round(radius)), (0, 0, 255), 2)
        cv2.drawMarker(canvas, center, (0, 255, 255), cv2.MARKER_CROSS, 20, 2)
        cv2.putText(canvas, str(number), (center[0] + 10, center[1] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, .85, (255, 255, 255), 3)
        cv2.putText(canvas, str(number), (center[0] + 10, center[1] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, .85, (0, 0, 255), 2)
    if not cv2.imwrite(str(destination), canvas, [cv2.IMWRITE_JPEG_QUALITY, 88]):
        raise ValueError('Could not write annotated preview')
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images', nargs='+', required=True,
                        help='Saved T0/T1 JPEG frames, preferably all six commissioning frames')
    parser.add_argument('--roi', default='auto', help='auto or x,y,w,h')
    parser.add_argument('--no-preview', action='store_true',
                        help='Print candidate coordinates only; do not write preview JPEGs')
    args = parser.parse_args(argv)
    failed = False
    for name in args.images:
        try:
            image, result = inspect_image(name, args.roi)
            if not args.no_preview:
                path = Path(name)
                destination = path.with_name(path.stem + '_nozzle_candidates.jpg')
                result['preview'] = str(annotate(image, result, destination))
            print(json.dumps(result, sort_keys=True), flush=True)
            if result['candidate_count'] != 1:
                failed = True
        except (OSError, ValueError, cv2.error) as exc:
            failed = True
            print('ROI INSPECTION FAILED (%s): %s' % (Path(name).name, exc),
                  file=sys.stderr, flush=True)
    print('Circle labels are diagnostics, NOT nozzle identification. '
          'Human-confirm an ROI and rerun all commissioning images; '
          'do not change saved offsets.', flush=True)
    return 2 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
