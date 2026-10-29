#!/usr/bin/env python3
"""Read-only check for the standard RatOS 2.1 V-Core 4 IDEX VAOC camera.

The stable udev alias identifies the device; crowsnest maps that device to a
camera-streamer listener (normally port 8080). The local JPEG endpoint is
probed but no images are saved and no printer movement occurs.
"""
import configparser
import sys
from pathlib import Path
from urllib.request import urlopen

DEVICE = Path('/dev/RatOS/rr-vaoc-camera')
CROWSNEST = Path('/home/pi/printer_data/config/crowsnest.conf')
PORT = 8080
SNAPSHOT = 'http://127.0.0.1:8080/?action=snapshot'


def check_crowsnest(device=DEVICE, config=CROWSNEST):
    if not device.exists():
        raise RuntimeError('Stock RatOS VAOC camera alias missing: '+str(device))
    if not config.is_file():
        raise RuntimeError('Stock crowsnest configuration missing: '+str(config))
    p=configparser.ConfigParser(interpolation=None,inline_comment_prefixes=('#',';'),strict=False)
    p.read(str(config))
    matches=[]
    for section in p.sections():
        if not section.lower().startswith('cam '):
            continue
        value=p.get(section,'device',fallback='').strip()
        if not value:continue
        candidate=Path(value)
        if candidate==device or (candidate.exists() and candidate.resolve()==device.resolve()):
            matches.append((section,p.get(section,'port',fallback='').strip(),
                            p.get(section,'mode',fallback='').strip().lower(),value))
    if len(matches)!=1:
        raise RuntimeError('Expected exactly one crowsnest camera using the RatOS VAOC alias; found %d' % len(matches))
    section,port,mode,value=matches[0]
    if port!=str(PORT):
        raise RuntimeError('RatOS VAOC camera is on port %s, not stock port %d; review the local configuration' % (port,PORT))
    if mode!='camera-streamer':
        raise RuntimeError('Native RatOS VAOC expects camera-streamer on port %d; found mode %s' % (PORT,mode))
    return section,value


def check_snapshot(url=SNAPSHOT,opener=urlopen):
    try:
        with opener(url,timeout=6) as r:
            typ=r.headers.get('Content-Type','').lower()
            magic=r.read(12)
    except Exception as e:
        raise RuntimeError('Stock VAOC camera snapshot not reachable at %s: %s' %(url,e)) from e
    if not (typ.startswith('image/') and (magic.startswith(b'\xff\xd8') or magic.startswith(b'\x89PNG\r\n\x1a\n'))):
        raise RuntimeError('VAOC snapshot endpoint did not return a JPEG/PNG image (%s)' % typ)
    return typ


def validate_stock_camera(device=DEVICE,config=CROWSNEST,probe=True):
    if device==DEVICE and not device.is_char_device():
        raise RuntimeError('RatOS VAOC alias does not resolve to a working video device: '+str(device))
    section,path=check_crowsnest(device,config)
    content_type=check_snapshot() if probe else 'not probed'
    return {'device':str(device),'crowsnest_section':section,'configured_device':path,
            'snapshot':SNAPSHOT,'snapshot_content_type':content_type}


def main():
    try:
        result=validate_stock_camera()
        print('STOCK RATOS VAOC CAMERA PASSED (read only)')
        for k,v in result.items():print('%s: %s'%(k,v))
        print('Camera ROI, focus, lighting and geometry still require independent commissioning.')
    except RuntimeError as e:
        print('STOCK RATOS VAOC CAMERA CHECK FAILED: '+str(e),file=sys.stderr)
        sys.exit(2)
if __name__=='__main__':main()
