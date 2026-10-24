#!/usr/bin/env python3
"""Stage 3.1 conservative VAOC nozzle-rim detector.

Unchanged nominal Hough method for properly illuminated frames.  For dark
frames, use three contrast normalisations and accept ONLY if independent
estimates agree.  No tool movement or offset writes here.

Image detection is not physical nozzle-axis accuracy; review calibration data.
"""
from pathlib import Path

import cv2
import numpy as np

DEFAULT_ROI = (680, 275, 590, 555)
MIN_RADIUS, MAX_RADIUS = 115, 185
NOMINAL_CENTER = np.array((960., 540.))
MAX_CENTER_DIST = 250.0
# 8 px ~0.028 mm with previously measured ~285 px/mm.
MAX_DARK_CENTRE_SPREAD_PX = 8.0
MAX_DARK_RADIUS_SPREAD_PX = 12.0


def _hough(gray, x, y, radius, param2=36):
    smoothed = cv2.GaussianBlur(gray, (7, 7), 1.5)
    found = cv2.HoughCircles(smoothed, cv2.HOUGH_GRADIENT, dp=1.25,
                             minDist=105, param1=105, param2=param2,
                             minRadius=radius[0], maxRadius=radius[1])
    if found is None:
        return None
    circles = np.asarray(found[0], dtype=float)
    circles[:, 0] += x
    circles[:, 1] += y
    circles = [c for c in circles if
               np.linalg.norm(c[:2] - NOMINAL_CENTER) <= MAX_CENTER_DIST
               and radius[0] <= c[2] <= radius[1]]
    if not circles:
        return None
    return min(circles, key=lambda c:
               np.linalg.norm(c[:2] - NOMINAL_CENTER) +
               abs(c[2] - 150.0) * .85)


def detect(path, roi=DEFAULT_ROI, radius=(MIN_RADIUS, MAX_RADIUS), annotate=None):
    im = cv2.imread(str(path))
    if im is None:
        raise ValueError(f'Cannot read {path}')
    x, y, w, h = roi
    if x < 0 or y < 0 or x+w > im.shape[1] or y+h > im.shape[0]:
        raise ValueError(f'Image {path} does not contain requested ROI')
    gray = cv2.cvtColor(im[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY)
    # Brightness measured only around the nozzle, not over the entire frame.
    roi_mean = float(gray.mean())
    mode = 'normal'
    if roi_mean >= 45:
        best = _hough(gray, x, y, radius)
        if best is None:
            raise ValueError(f'No plausible nozzle rim in normally lit image {path}; '
                             f'ROI mean={roi_mean:.1f}; clean/focus/check lighting')
    else:
        # Reject effectively black frames before contrast enhancement;
        # stretching sensor noise into a circle would be unsafe.
        if roi_mean < 15 or np.percentile(gray, 98) < 55:
            raise ValueError(f'No usable nozzle illumination in {path}: '
                             f'ROI mean={roi_mean:.1f}. Restore lighting.')
        # The previous failure had ROI mean ~20 vs >90 for normal captures.
        # Different enhancement methods must produce consistent estimates.
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        equal = cv2.equalizeHist(gray)
        gamma = np.uint8(np.clip((gray / 255.) ** .45 * 255, 0, 255))
        variants = [('clahe', clahe), ('equalized', equal), ('gamma', gamma)]
        hits = []
        for name, image in variants:
            circle = _hough(image, x, y, radius)
            if circle is not None:
                hits.append((name, circle))
        if len(hits) < 3:
            raise ValueError(f'Low-light nozzle image ambiguous ({len(hits)}/3 '
                             f'enhancement methods agreed to detect a candidate): '
                             f'{path}. ROI mean={roi_mean:.1f}. Improve lighting.')
        estimates = np.asarray([hit[1] for hit in hits])
        centers = estimates[:, :2]
        centre_spread = max(float(np.linalg.norm(a-b))
                            for a in centers for b in centers)
        radius_spread = float(np.ptp(estimates[:, 2]))
        if (centre_spread > MAX_DARK_CENTRE_SPREAD_PX or
                radius_spread > MAX_DARK_RADIUS_SPREAD_PX):
            raise ValueError(f'Low-light nozzle detection inconsistent for {path}: '
                             f'centre spread={centre_spread:.1f}px, '
                             f'radius spread={radius_spread:.1f}px. '
                             f'Improve illumination rather than relaxing QC.')
        best = np.median(estimates, axis=0)
        mode = 'low-light-consensus'

    if annotate is not None:
        out = im.copy()
        cv2.rectangle(out, (x,y), (x+w,y+h), (60,200,255), 2)
        cx, cy, r = [int(round(v)) for v in best]
        cv2.circle(out, (cx,cy), r, (0,230,0), 3)
        cv2.drawMarker(out, (cx,cy), (0,0,255), cv2.MARKER_CROSS, 35, 3)
        cv2.putText(out, f'{mode} ROI brightness={roi_mean:.1f}',
                    (24, 48), cv2.FONT_HERSHEY_SIMPLEX, .9,
                    (0, 230, 0) if mode=='normal' else (0,180,255), 2)
        if not cv2.imwrite(str(annotate), out,
                           [cv2.IMWRITE_JPEG_QUALITY,85]):
            raise ValueError(f'Failed to write detection diagnostic {annotate}')
    return best
