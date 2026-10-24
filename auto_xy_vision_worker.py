#!/usr/bin/env python3
"""Single camera capture + outer-nozzle rim detection, intentionally OUTSIDE Klipper.

Only stdout carries a small JSON result. Errors go to stderr and exit != 0.
The Klipper-side gate launches this via Popen and yields its reactor until exit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen
from auto_xy_detector import detect


def main():
    a=argparse.ArgumentParser()
    a.add_argument('--url', default='http://127.0.0.1:8080/?action=snapshot')
    a.add_argument('--image', required=True)
    a.add_argument('--roi', default='680,275,590,555')
    a.add_argument('--timeout', type=float, default=6.0)
    a=a.parse_args()
    roi=tuple(int(k) for k in a.roi.split(','))
    if len(roi)!=4 or any(v<0 for v in roi): raise ValueError('Invalid ROI')
    if not (1<=a.timeout<=12): raise ValueError('Camera timeout outside allowed range')
    image=Path(a.image)
    image.parent.mkdir(parents=True,exist_ok=True)
    req=Request(a.url,headers={'Accept':'image/jpeg'})
    with urlopen(req,timeout=a.timeout) as res:
        data=res.read(12_000_001)
    if (not 1000<len(data)<=12_000_000 or not data.startswith(b'\xff\xd8')
        or not data.endswith(b'\xff\xd9')):
        raise ValueError('Snapshot was missing, truncated or not a complete JPEG')
    image.write_bytes(data)
    circle=detect(image,roi=roi,annotate=image.with_name('detected_'+image.name))
    if not (115<=float(circle[2])<=185): raise ValueError('Nozzle circle radius invalid')
    print(json.dumps({'center_px':[float(circle[0]),float(circle[1])],
                      'radius_px':float(circle[2]),'sha256':hashlib.sha256(data).hexdigest()}))

if __name__=='__main__':
    try: main()
    except Exception as e:
        print('VISION FAILURE: '+str(e),file=sys.stderr)
        sys.exit(2)
