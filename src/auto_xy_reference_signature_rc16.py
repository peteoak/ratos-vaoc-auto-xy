#!/usr/bin/env python3
"""Compact illumination-normalised nozzle appearance evidence (RC16).

The accepted Hough circle remains the sole coordinate estimate. This module
does not choose a circle or write offsets. It compares a low-cost radial
appearance fingerprint against the previously operator-commissioned nozzle.
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
    difference=float(np.linalg.norm(actual-expected)/math.sqrt(len(BANDS)))
    max_allowed=max(.35,3.*spread+.12)
    if difference>max_allowed:
        raise ValueError(
            'Live nozzle appearance differs from commissioned reference '
            '(score %.3f > allowed %.3f); recommission or inspect lighting'
            % (difference,max_allowed))
    return {'method':'radial-v1','difference':round(difference,5),
            'limit':round(max_allowed,5),'matched':True}
