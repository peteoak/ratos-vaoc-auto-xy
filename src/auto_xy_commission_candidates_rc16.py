#!/usr/bin/env python3
"""RC16 operator-anchored full-frame commissioning ambiguity fallback.

ONLY applies to separately captured stills when native VAOC has been manually
positioned at the camera reticle. It NEVER changes the tuned two_stage_roi
precision detector, offset calculation, or any ongoing print. Multiple
independent cues must agree; otherwise fail closed.
"""
import math
from pathlib import Path
import cv2
import numpy as np

from auto_xy_detector import MIN_RADIUS, MAX_RADIUS


class CommissionAmbiguity(ValueError):
    pass


def concentric_rim_evidence(gray, cx, cy, radius):
    """Bright annulus vs inner body, calculated on the FULL original frame."""
    h,w=gray.shape
    xx0=max(0,int(math.floor(cx-radius)))
    xx1=min(w,int(math.ceil(cx+radius+1)))
    yy0=max(0,int(math.floor(cy-radius)))
    yy1=min(h,int(math.ceil(cy+radius+1)))
    if xx0>=xx1 or yy0>=yy1:
        raise CommissionAmbiguity('Candidate outside the captured image')
    yy,xx=np.ogrid[yy0:yy1,xx0:xx1]
    d2=(xx-cx)**2+(yy-cy)**2
    section=gray[yy0:yy1,xx0:xx1]
    core=section[(d2>=(.15*radius)**2)&(d2<(.38*radius)**2)]
    rim=section[(d2>=(.65*radius)**2)&(d2<(.85*radius)**2)]
    if min(core.size,rim.size)<100:
        raise CommissionAmbiguity('Candidate rim/centre not fully visible')
    return float(np.mean(rim)-np.mean(core))


def resolve_manual_vaoc_ambiguity(path, operator_confirmed=False):
    """Select only a uniquely camera-centred, independently supported rim.

    No permanent ROI; optical evidence is compared over the entire image.
    This is a manual-VAOC *commissioning* aid only, never a print detector.
    The operator must explicitly affirm the nozzle is at the camera reticle.
    """
    if not operator_confirmed:
        raise CommissionAmbiguity('Native manual VAOC position confirmation is required')
    image=cv2.imread(str(path))
    if image is None:
        raise CommissionAmbiguity('Camera reference JPEG could not be read')
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    h,w=gray.shape
    if not 640<=w<=4096 or not 480<=h<=3072:
        raise CommissionAmbiguity('Unsupported camera geometry for reticle evidence')
    smoothed=cv2.GaussianBlur(gray,(7,7),1.5)
    found=cv2.HoughCircles(smoothed,cv2.HOUGH_GRADIENT,dp=1.25,
                           minDist=105,param1=105,param2=36,
                           minRadius=MIN_RADIUS,maxRadius=MAX_RADIUS)
    if found is None or len(found[0])<2:
        raise CommissionAmbiguity('Expected multiple candidate circles for ambiguity review')
    # The operator is positioning the actual nozzle at the VAOC reticle, not
    # choosing the visually brightest circle. If the camera's reticle is NOT
    # central, this evidence is inappropriate and MUST refuse safely.
    reticle_x,reticle_y=w/2.,h/2.
    tolerance=min(60.,.07*min(w,h))
    candidates=[]
    for cx,cy,r in np.asarray(found[0],dtype=float):
        if not MIN_RADIUS<=r<=MAX_RADIUS:continue
        dist=math.hypot(cx-reticle_x,cy-reticle_y)
        contrast=concentric_rim_evidence(gray,cx,cy,r)
        candidates.append({'centre':(float(cx),float(cy)),
                           'radius':float(r),
                           'distance':dist,'contrast':contrast})
    if len(candidates)<2:
        raise CommissionAmbiguity('Insufficient independent candidate evidence')
    eligible=[c for c in candidates if
              c['distance']<=tolerance and c['contrast']>=55.]
    if len(eligible)!=1:
        raise CommissionAmbiguity(
            'Unable to identify one camera-reticle rim from independent evidence')
    selected=eligible[0]
    competitors=[c for c in candidates if c is not selected]
    # Two overlapping Hough centres can be duplicate fits to the same
    # reflective rim. A single high contrast score must not arbitrarily
    # select between these competing geometries.
    if any(math.hypot(selected['centre'][0]-c['centre'][0],
                      selected['centre'][1]-c['centre'][1]) <
           .65*max(selected['radius'],c['radius']) for c in competitors):
        raise CommissionAmbiguity(
            'Overlapping candidate circles; neither geometric fit is trusted')
    if any(selected['contrast']-c['contrast']<45. for c in competitors):
        raise CommissionAmbiguity(
            'Competing circular edge has similar rim evidence; no selection')
    if not math.isfinite(selected['contrast']):
        raise CommissionAmbiguity('Invalid candidate contrast')
    return (selected['centre'][0],selected['centre'][1],selected['radius'])


def inspect_three_frames(paths, operator_confirmed=False):
    """Require all ambiguous frames to agree geometrically; no reusing images."""
    circles=[]
    for path in paths:
        circles.append(resolve_manual_vaoc_ambiguity(
            path,operator_confirmed=operator_confirmed))
    cx=sum(c[0] for c in circles)/len(circles)
    cy=sum(c[1] for c in circles)/len(circles)
    radius=sorted(c[2] for c in circles)[len(circles)//2]
    if (max(math.hypot(x-cx,y-cy) for x,y,_ in circles)>10. or
            max(abs(r-radius) for _,_,r in circles)>10.):
        raise CommissionAmbiguity('Candidate not repeatable across manual VAOC captures')
    return circles
