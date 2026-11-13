#!/usr/bin/env python3
"""Compact illumination-normalised nozzle appearance evidence (RC16).

The accepted Hough circle remains the sole coordinate estimate. This module
does not choose a circle or write offsets. It compares a low-cost radial
appearance fingerprint against the previously operator-commissioned nozzle.
The live acceptance stage uses outer-rim likeness so normal ooze inside the
nozzle circle cannot veto a unique, geometrically verified nozzle centre.
"""
import math
from pathlib import Path
import cv2
import numpy as np

BANDS=((0.,.16),(.16,.39),(.39,.61),(.61,.82),(.82,1.04),(1.04,1.22))
SIDE=96


def fingerprint(path,center,radius):
    image=cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError('Nozzle reference image unreadable')
    cx,cy=[float(v) for v in center]
    radius=float(radius)
    if not all(math.isfinite(v) for v in (cx,cy,radius)) or not 115.<=radius<=185.:
        raise ValueError('Invalid nozzle appearance centre/radius')
    h,w=image.shape
    size=int(math.ceil(radius*1.24))
    left=int(round(cx))-size
    right=int(round(cx))+size
    top=int(round(cy))-size
    bottom=int(round(cy))+size
    if left<0 or top<0 or right>w or bottom>h:
        raise ValueError('Nozzle appearance cannot be measured at frame edge')
    patch=cv2.resize(image[top:bottom,left:right],(SIDE,SIDE),
                     interpolation=cv2.INTER_AREA)
    yy,xx=np.indices(patch.shape)
    ctr=(SIDE-1)/2.
    rho=np.hypot(xx-ctr, yy-ctr)/(SIDE/(2.*1.24))
    means=[]
    for lo,hi in BANDS:
        pixels=patch[(rho>=lo)&(rho<hi)]
        if pixels.size<16:
            raise ValueError('Not enough nozzle rim appearance pixels')
        means.append(float(np.mean(pixels)))
    lo,hi=min(means),max(means)
    if hi-lo<18.:
        raise ValueError('Nozzle rim lacks reliable visual contrast')
    # Relative radial shape, invariant to gain/offset shifts in brightness.
    return [(v-lo)/(hi-lo) for v in means]


def signature_from_frames(paths,circles):
    if len(paths)!=3 or len(circles)!=3:
        raise ValueError('Three independent commissioned frames required')
    rows=np.asarray([
        fingerprint(path,(circle[0],circle[1]),circle[2])
        for path,circle in zip(paths,circles)],dtype=float)
    midpoint=np.median(rows,axis=0)
    deviations=np.linalg.norm(rows-midpoint,axis=1)/math.sqrt(len(BANDS))
    if not np.all(np.isfinite(rows)) or max(deviations)>.30:
        raise ValueError('Nozzle visual appearance inconsistent across commissioning frames')
    return {'format':'radial-v1',
            'values':[round(float(x),5) for x in midpoint],
            'max_spread':round(float(max(deviations)),5)}


def compare(path,center,radius,reference):
    if not isinstance(reference,dict) or reference.get('format')!='radial-v1':
        raise ValueError('Unknown nozzle appearance signature')
    raw=reference.get('values')
    if not isinstance(raw,list) or len(raw)!=len(BANDS):
        raise ValueError('Incomplete nozzle appearance signature')
    try:
        expected=np.asarray([float(v) for v in raw],dtype=float)
        spread=float(reference.get('max_spread',0.))
    except (TypeError,ValueError) as exc:
        raise ValueError('Invalid saved nozzle appearance signature') from exc
    if (not np.all(np.isfinite(expected)) or
            np.any(expected<0.) or np.any(expected>1.) or
            not math.isfinite(spread) or not 0<=spread<=.30):
        raise ValueError('Saved nozzle appearance signature outside valid range')
    actual=np.asarray(fingerprint(path,center,radius),dtype=float)
    # RC16 supervised field finding: hot nozzle tips can ooze across most of
    # the centre while leaving the outer circular rim clearly measurable.
    # The six-band whole-face score falsely rejected these photographs even
    # AFTER two independent geometric detectors agreed on one nozzle circle.
    # Reuse the existing commissioned radial-v1 data: compare the OUTER
    # annulus independently of inner-face luminance, which is irrelevant to
    # Hough XY precision. No recommissioning or reference file migration.
    #
    # This is still a corroborating test, not a replacement for the separately
    # mandatory two-stage coarse annulus, unique precision Hough circle,
    # commissioned radius/position guard, and per-frame capture verification.
    def rim_shape(values):
        outer=values[3:]  # 0.61-0.82r, 0.82-1.04r, 1.04-1.22r
        minimum=float(np.min(outer))
        extent=float(np.max(outer))-minimum
        if extent < .18:
            raise ValueError('Nozzle rim lacks enough optical contrast for reference comparison')
        return (outer-minimum)/extent

    reference_rim=rim_shape(expected)
    actual_rim=rim_shape(actual)
    difference=float(np.linalg.norm(actual_rim-reference_rim)/math.sqrt(3.))
    max_allowed=.38
    if difference>max_allowed:
        raise ValueError(
            'Live nozzle outer rim lacks commissioned likeness '
            '(rim score %.3f > allowed %.3f); clean/check view and lighting'
            % (difference,max_allowed))
    face_difference=float(np.linalg.norm(actual-expected)/math.sqrt(len(BANDS)))
    return {'method':'outer-rim-v1','difference':round(difference,5),
            'limit':max_allowed,'matched':True,
            'full_face_difference_diagnostic':round(face_difference,5)}


# The normal camera detector is the geometry authority. This appearance
# evidence is used ONLY when two circles remain plausible after ALL geometric
# checks, and never vetoes a unique geometrically verified nozzle.
PHOTO_TIEBREAK_WEIGHT = 0.25
GEOMETRY_TIEBREAK_WEIGHT = 1.0 - PHOTO_TIEBREAK_WEIGHT
TIEBREAK_MIN_SCORE_GAP = 0.08
TIEBREAK_MIN_PHOTO_GAP = 0.12


def rim_likeness_score(path, center, radius, reference):
    """Unthresholded outer-rim difference (smaller is closer).

    An already unique geometric candidate must not call this function.
    No saved reference format or nozzle XY coordinate is modified.
    """
    if not isinstance(reference, dict) or reference.get('format') != 'radial-v1':
        raise ValueError('Unknown nozzle appearance signature')
    values = reference.get('values')
    if not isinstance(values, list) or len(values) != len(BANDS):
        raise ValueError('Incomplete nozzle appearance signature')
    try:
        expected = np.asarray([float(value) for value in values], dtype=float)
        spread = float(reference.get('max_spread', 0.))
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid commissioned appearance evidence') from exc
    if (not np.all(np.isfinite(expected)) or np.any(expected < 0.) or
            np.any(expected > 1.) or not math.isfinite(spread) or
            not 0. <= spread <= .30):
        raise ValueError('Invalid commissioned appearance evidence')
    actual = np.asarray(fingerprint(path, center, radius), dtype=float)

    def outer_normalized(row):
        outer = row[3:]
        amount = float(np.max(outer) - np.min(outer))
        if amount < .18:
            raise ValueError('Nozzle rim has insufficient optical contrast')
        return (outer - np.min(outer)) / amount

    return float(np.linalg.norm(outer_normalized(actual) -
                                outer_normalized(expected)) / math.sqrt(3.))


def select_ambiguous_pair(path, circles, reference, coarse_center,
                          expected_center, expected_radius):
    """Select only an independently corroborated TWO-circle geometric tie.

    The 75% geometric score dominates the maximum 25% photographic input.
    Two lookalikes or an unsupported ambiguity still fail closed. No photo
    ever moves or averages a Hough centre: the chosen centre is the raw Hough.
    """
    if (len(circles) != 2 or coarse_center is None or
            expected_center is None or expected_radius is None):
        raise ValueError('Photo tie-break requires exactly two plausible geometric circles')
    rough = np.asarray(coarse_center, dtype=float)
    reference_center = np.asarray(expected_center, dtype=float)
    expected_radius = float(expected_radius)
    scores = []
    for circle in circles:
        c = np.asarray(circle, dtype=float)
        if c.shape != (3,) or not np.all(np.isfinite(c)):
            raise ValueError('Invalid ambiguous nozzle circle')
        rough_px = float(np.linalg.norm(c[:2] - rough))
        origin_px = float(np.linalg.norm(c[:2] - reference_center))
        radius_px = abs(float(c[2]) - expected_radius)
        if rough_px > 18. or origin_px > 96. or radius_px > 18.:
            raise ValueError('Ambiguous candidate does not satisfy geometric limits')
        geometric = (.60 * rough_px / 18. +
                     .20 * origin_px / 96. +
                     .20 * radius_px / 18.)
        appearance = rim_likeness_score(path, c[:2], c[2], reference)
        scores.append((GEOMETRY_TIEBREAK_WEIGHT * geometric +
                       PHOTO_TIEBREAK_WEIGHT * appearance,
                       geometric, appearance))
    margin = abs(scores[0][0] - scores[1][0])
    photo_margin = abs(scores[0][2] - scores[1][2])
    if margin < TIEBREAK_MIN_SCORE_GAP or photo_margin < TIEBREAK_MIN_PHOTO_GAP:
        raise ValueError('Two nozzle candidates remain ambiguous; image likeness is inconclusive')
    chosen = 0 if scores[0][0] < scores[1][0] else 1
    return chosen, {'method': 'geometry-photo-tiebreak-v1',
                    'matched': True, 'used_for_selection': True,
                    'photo_weight': PHOTO_TIEBREAK_WEIGHT,
                    'geometry_weight': GEOMETRY_TIEBREAK_WEIGHT,
                    'candidate_scores': [round(a[0], 5) for a in scores],
                    'photo_scores': [round(a[2], 5) for a in scores],
                    'score_gap': round(margin, 5)}
