#!/usr/bin/env python3
"""Read-only Klipper state/config summary after supervised installation and restart."""
import json
import sys
import ratos_extension
from pathlib import Path
from urllib.request import urlopen

HOME=Path('/home/pi')
EXPECTED_MACROS=(
    'AUTO_XY_ON','AUTO_XY_OFF','AUTO_XY_MEASURE_ONLY_ON','AUTO_XY_RUN_NOW',
    'AUTO_XY_STATUS','AUTO_XY_VIEW_START','AUTO_XY_VIEW_END',
    'AUTOXY_PRINT_PREPARE','AUTOXY_PRINT_FINISH',
)

def main():
    with urlopen('http://127.0.0.1:7125/printer/objects/query?configfile&save_variables&print_stats',timeout=8) as r:
        data=json.load(r)['result']['status']
    cfg=data['configfile']['settings'];variables=data['save_variables']['variables']
    names={str(name).casefold() for name in cfg}
    printer=(HOME/'printer_data/config/printer.cfg').read_text()
    user=(HOME/'printer_data/config/auto_xy.cfg').read_text()
    include_printer=sum(l.strip().lower()=='[include auto_xy.cfg]' for l in printer.splitlines())
    include_user=sum(l.strip().lower()=='[include auto_xy_macros.cfg]' for l in user.splitlines())
    expected=HOME/'auto_xy/klipper/auto_xy_gate.py'
    resolved=(HOME/'klipper/klippy/extras/auto_xy_gate.py').resolve()
    worker=cfg.get('auto_xy_gate',{}).get('worker')
    print('=== AutoXY read-only installed layout ===')
    print('print state:',data['print_stats']['state'])
    print('printer.cfg AutoXY include:',include_printer,'(required 1)')
    print('auto_xy.cfg macro include:',include_user,'(required 1)')
    binding=ratos_extension.snapshot(HOME)
    print('registered RatOS AutoXY gate:',binding['registered'],'correct:',
          binding['registered']==str(expected))
    print('active Klipper extension:',resolved,'correct:',resolved==expected)
    print('active camera worker:',worker,'correct:',worker==str(HOME/'auto_xy/vision/auto_xy_vision_worker.py'))
    for key in ('idex_xoffset','idex_yoffset','idex_zoffset'):
        print(key,variables.get(key,'NOT SET'))
    for name in EXPECTED_MACROS:
        available=('gcode_macro '+name).casefold() in names
        print('macro',name, 'OK' if available else 'MISSING')
    ok=(include_printer==1 and include_user==1 and resolved==expected
        and binding['registered']==str(expected) and binding['link']==str(expected)
        and worker==str(HOME/'auto_xy/vision/auto_xy_vision_worker.py')
        and all(('gcode_macro '+name).casefold() in names for name in EXPECTED_MACROS))
    print('VALIDATION', 'PASS' if ok else 'FAILED; STOP, DO NOT PRINT')
    return 0 if ok else 2

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as e:
        print('Read-only validation failed:',e,file=sys.stderr);sys.exit(2)
