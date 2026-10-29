#!/usr/bin/env python3
"""Read-only source and configuration verifier for an already installed RC15 dev5."""
from pathlib import Path
import argparse,hashlib,re,sys,json
R=Path(__file__).resolve().parents[1]
FILES={
 'auto_xy/klipper/auto_xy_gate.py':'src/auto_xy_gate.py',
 'auto_xy/vision/auto_xy_detector.py':'src/auto_xy_detector.py',
 'auto_xy/vision/auto_xy_vision_worker.py':'src/auto_xy_vision_worker.py',
}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--home',type=Path,default=Path('/home/pi'))
 a=p.parse_args();home=a.home
 print('AutoXY v0.5.0-rc15-dev5 SOURCE CHECK (READ-ONLY)')
 ok=True
 for dest,source in FILES.items():
  actual=home/dest;expected=R/source
  if actual.is_file() and not actual.is_symlink():
   match=sha(actual)==sha(expected)
   print(('MATCH   ' if match else 'DIFFERS ')+str(actual))
   ok &= match
  else:
   print('MISSING '+str(actual));ok=False
 cfg=home/'printer_data/config/auto_xy.cfg'
 if cfg.exists():
  val=cfg.read_text()
  for key in ('camera_detector_mode','commissioned_detector_guard','camera_worker_mode','settling_path','thermal_mode','protected_print_enabled'):
   matches=re.findall(r'^\s*'+key+r':\s*(\S+)',val,re.M)
   print(key+':', ', '.join(matches) if matches else '(source default)')
 else:print('Configuration missing:',cfg);ok=False
 print('Saved offsets not accessed or modified.')
 print('RESULT:', 'RUNTIME FILES MATCH THIS PUBLICATION PACKAGE' if ok else 'CHECK FAILED: review differences, do NOT overwrite')
 return 0 if ok else 2
if __name__=='__main__':sys.exit(main())
