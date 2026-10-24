#!/usr/bin/env python3
"""Recovery only: remove Stage6 include while preserving all current saved offsets.
Run on Pi with printer idle and verify calibration state before printing.
"""
from pathlib import Path
import shutil,time,sys
cfg=Path('/home/pi/printer_data/config/printer.cfg')
try:
    text=cfg.read_text()
    if '[include auto_xy_print.cfg]' not in text:
        print('Stage6 include already absent; no change.');sys.exit(0)
    backup=cfg.with_name('printer.cfg.pre_stage6_disable_'+time.strftime('%Y%m%d-%H%M%S'))
    shutil.copy2(cfg,backup)
    text=text.replace('[include auto_xy_print.cfg]\n','').replace('[include auto_xy_print.cfg]','')
    legacy=cfg.parent/'auto_xy_control.cfg'
    if legacy.exists() and '[include auto_xy_control.cfg]' not in text:
        text=text.rstrip()+'\n[include auto_xy_control.cfg]\n'
    cfg.write_text(text)
    print('Removed Stage6 print gate include. Your RatOS saved tool offsets were NOT changed.')
    print('Backup: ',backup)
    print('Restart Klipper while idle, then check any saved calibration fault / runtime offsets BEFORE printing.')
except Exception as e:
    print('RECOVERY ERROR: '+str(e),file=sys.stderr);sys.exit(2)
