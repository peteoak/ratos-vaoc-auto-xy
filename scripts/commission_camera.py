#!/usr/bin/env python3
"""Offline optical profile creation. NEVER moves, homes, heats or captures.

The operator must have completed manual RatOS VAOC before creating ANY of
these pictures. This command explicitly acknowledges that human verification.
Use 3 independent camera frames for each tool, taken AFTER the manual VAOC,
with T0/T1 brought to the camera position using native supervised VAOC tools.
"""
import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from auto_xy_detector import detect


def create_profile(t0,t1,roi,camera_url,confirmed):
    if not confirmed:
        raise ValueError('Complete and confirm successful native manual RatOS VAOC BEFORE taking commissioning images')
    if len(t0)<3 or len(t1)<3:
        raise ValueError('Need >=3 independent camera images of EACH nozzle')
    if roi != 'auto':
        try: v=tuple(int(k) for k in roi.split(','))
        except Exception as e: raise ValueError('ROI must be auto or x,y,w,h integers') from e
        if len(v)!=4 or any(k<0 for k in v) or v[2]<200 or v[3]<200:
            raise ValueError('ROI is invalid/too small to verify nozzle rim safely')
    else:v=None
    profile={'format':1,'status':'COMMISSIONED_FROM_IMAGES',
             'manual_vaoc_confirmed':True,
             'manual_vaoc_confirmation':'Operator explicitly certified a completed native RatOS manual alignment prior to all commissioning captures; not independently verified',
             'commissioned_utc':datetime.now(timezone.utc).isoformat(),
             'camera_url':camera_url,'roi':roi}
    used=set()
    for key,images in (('tool0',t0),('tool1',t1)):
        circles=[]
        for image in images:
            path=Path(image)
            if not path.is_file():raise ValueError('Missing camera frame: '+str(path))
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in used:raise ValueError('Duplicate camera frame: '+str(path))
            used.add(digest)
            circle=detect(path,roi=v)
            if not all(math.isfinite(float(x)) for x in circle):
                raise ValueError('Nonfinite circle detection')
            circles.append(circle)
        # Same tool should not drift more than 10 pixels while stationary;
        # fail, never average unstable or ambiguously positioned hardware.
        xmid=sum(float(c[0]) for c in circles)/len(circles)
        ymid=sum(float(c[1]) for c in circles)/len(circles)
        maxdist=max(math.hypot(float(c[0])-xmid,float(c[1])-ymid) for c in circles)
        if maxdist>10.0:
            raise ValueError('%s camera detection drifts %.1f px; fix mount/lighting before commissioning' %(key,maxdist))
        radii=sorted(float(c[2]) for c in circles)
        r=radii[len(radii)//2]
        if max(abs(u-r) for u in radii)>10.0:
            raise ValueError('%s radius varies excessively; refocus/clean camera' % key)
        profile[key]={'num_images':len(circles),'radius_px':r,
                      'registration_center_px':[xmid,ymid],
                      'max_spread_px':maxdist}
    return profile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--t0',nargs='+',required=True,help='At least 3 unique AFTER-manual-VAOC T0 JPEG frames')
    parser.add_argument('--t1',nargs='+',required=True,help='At least 3 unique AFTER-manual-VAOC T1 JPEG frames')
    parser.add_argument('--roi',default='auto')
    parser.add_argument('--camera-url',default='http://127.0.0.1:8080/?action=snapshot')
    parser.add_argument('--confirm-native-manual-vaoc',action='store_true',required=True,
                        help='I completed and visually verified native manual VAOC before these images')
    parser.add_argument('--output',type=Path,default=Path('/home/pi/auto_xy/camera_commission.json'))
    a=parser.parse_args()
    data=create_profile(a.t0,a.t1,a.roi,a.camera_url,a.confirm_native_manual_vaoc)
    if a.output.exists():raise RuntimeError('Refusing to overwrite existing camera commissioning profile; inspect/backup first')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(data,indent=2)+'\n')
    print('Photo-only commissioning PROFILE CREATED:',a.output)
    print('Operator VAOC confirmation is recorded, not independently validated.')
    print('Do not enable automatic offset writes before a supervised no-write Run Now passes.')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('CAMERA COMMISSIONING FAILED:',str(exc),file=sys.stderr)
        sys.exit(2)
