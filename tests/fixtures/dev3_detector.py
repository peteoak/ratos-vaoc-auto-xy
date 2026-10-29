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

DEFAULT_ROI = None  # None means entire current camera frame; no fixed screen crop
MIN_RADIUS, MAX_RADIUS = 115, 185
# 8 px ~0.028 mm with previously measured ~285 px/mm.
MAX_DARK_CENTRE_SPREAD_PX = 8.0
MAX_DARK_RADIUS_SPREAD_PX = 12.0


def _hough(gray, x, y, radius, param2=36, expected_center=None, expected_radius=None):
    smoothed = cv2.GaussianBlur(gray, (7, 7), 1.5)
    found = cv2.HoughCircles(smoothed, cv2.HOUGH_GRADIENT, dp=1.25,
                             minDist=105, param1=105, param2=param2,
                             minRadius=radius[0], maxRadius=radius[1])
    if found is None:
        return None
    circles = np.asarray(found[0], dtype=float)
    circles[:, 0] += x
    circles[:, 1] += y
    circles = [c for c in circles if radius[0] <= c[2] <= radius[1]]
    # RC14 opt-in profile gate: reject background rings and reflections
    # BEFORE resolving multiple Hough candidates; do not simply choose the
    # nearest circle. More than one plausible remaining candidate still fails.
    if expected_center is not None:
        circles=[c for c in circles
                 if float(np.linalg.norm(c[:2]-expected_center)) <= 96.]
    if expected_radius is not None:
        circles=[c for c in circles if abs(float(c[2])-expected_radius) <= 18.]
    if not circles:
        return None
    if len(circles) != 1:
        raise ValueError('Ambiguous camera ROI: %d nozzle-sized circles detected; '
                         'commission a tighter image crop rather than guessing' % len(circles))
    return circles[0]


def detect(path, roi=DEFAULT_ROI, radius=(MIN_RADIUS, MAX_RADIUS), annotate=None,
           expected_center=None, expected_radius=None):
    if (expected_center is None) != (expected_radius is None):
        raise ValueError('Commissioned guard needs both nozzle centre and radius')
    if expected_center is not None:
        expected_center=np.asarray(expected_center,dtype=float)
        expected_radius=float(expected_radius)
        if (expected_center.shape!=(2,) or not np.all(np.isfinite(expected_center))
                or not np.isfinite(expected_radius) or not 115<=expected_radius<=185):
            raise ValueError('Invalid commissioned nozzle guard')
    im = cv2.imread(str(path))
    if im is None:
        raise ValueError(f'Cannot read {path}')
    if roi is None or roi == 'auto':
        x, y, w, h = (0, 0, im.shape[1], im.shape[0])
    else:
        x, y, w, h = roi
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x+w > im.shape[1] or y+h > im.shape[0]:
        raise ValueError(f'Image {path} does not contain requested ROI')
    gray = cv2.cvtColor(im[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY)
    # Brightness measured only around the nozzle, not over the entire frame.
    roi_mean = float(gray.mean())
    mode = 'normal'
    if roi_mean >= 45:
        best = _hough(gray, x, y, radius, expected_center=expected_center, expected_radius=expected_radius)
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
            circle = _hough(image, x, y, radius, expected_center=expected_center, expected_radius=expected_radius)
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


def register_nozzle_features(anchor_path, sample_path, anchor_center, hough_center,
                             half_sizes=(64, 96), search_px=16,
                             min_score=.990, max_template_agreement_px=1.5,
                             max_hough_disagreement_px=8.0):
    """Fail-closed image registration for a single nozzle's calibration sweep.

    Use the existing Hough detection to locate the same nozzle in both images.
    Track interior nozzle features using two independent template sizes.
    This is EXPERIMENTAL; passing does not establish physical accuracy.
    """
    base = cv2.imread(str(anchor_path), cv2.IMREAD_GRAYSCALE)
    sample = cv2.imread(str(sample_path), cv2.IMREAD_GRAYSCALE)
    if base is None or sample is None or base.shape != sample.shape:
        raise ValueError('Registration images missing or dimensions differ')
    anchor = np.asarray(anchor_center, dtype=float)
    circle = np.asarray(hough_center, dtype=float)
    if (anchor.shape != (2,) or circle.shape != (2,)
            or not np.all(np.isfinite(anchor)) or not np.all(np.isfinite(circle))):
        raise ValueError('Non-finite or invalid registration centres')
    ax, ay = [int(round(v)) for v in anchor]
    cx, cy = [int(round(v)) for v in circle]
    estimates, scores = [], []
    for half in half_sizes:
        template = base[ay-half:ay+half, ax-half:ax+half]
        search = sample[cy-half-search_px:cy+half+search_px,
                        cx-half-search_px:cx+half+search_px]
        if (template.shape != (2*half, 2*half) or
                search.shape != (2*(half+search_px), 2*(half+search_px))):
            raise ValueError('Nozzle registration template outside camera frame')
        if float(template.std()) < 10 or float(search.std()) < 10:
            raise ValueError('Registration image has insufficient features')
        correlation = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, top_left = cv2.minMaxLoc(correlation)
        if not np.isfinite(score) or score < min_score:
            raise ValueError('Nozzle registration correlation too weak: %.4f' % score)
        # Top-left of match is offset from the sample's Hough-centred crop.
        estimate = anchor + np.asarray([cx+top_left[0]-search_px-ax,
                                        cy+top_left[1]-search_px-ay], dtype=float)
        estimates.append(estimate)
        scores.append(float(score))
    disagreement = float(np.linalg.norm(estimates[0]-estimates[1]))
    if disagreement > max_template_agreement_px:
        raise ValueError('Two nozzle templates disagree by %.2f px' % disagreement)
    refined = np.mean(estimates, axis=0)
    hough_gap = float(np.linalg.norm(refined-circle))
    if hough_gap > max_hough_disagreement_px:
        raise ValueError('Hough versus feature location differs by %.2f px' % hough_gap)
    return {'center_px': refined.tolist(),
            'hough_center_px': circle.tolist(),
            'scores': scores, 'template_disagreement_px': disagreement,
            'hough_disagreement_px': hough_gap}
