#!/usr/bin/env python3
"""Guarded RC15 dev4 -> dev5 opt-in two-stage detector installer. Nothing modifies printer.cfg,
auto_xy.cfg, saved offsets, camera profile, or RatOS macros. No automatic
Klipper restart. --check is read-only; no install without explicit token.

Requires cooled, idle, non-faulted printer with native VAOC exited and
settling_path: original. Preserves existing persistent or legacy camera mode.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
HOME=Path('/home/pi')
FILES={
  'auto_xy/klipper/auto_xy_gate.py':'src/auto_xy_gate.py',
  'auto_xy/vision/auto_xy_vision_worker.py':'src/auto_xy_vision_worker.py',
  'auto_xy/vision/auto_xy_detector.py':'src/auto_xy_detector.py',
}
ORIGINAL_RC15_DEV4={
  'auto_xy/klipper/auto_xy_gate.py':'841d0d0f89367435c7cd60f0c807bff5921dd08fd2c598a206c6b0bcaf601446',
  'auto_xy/vision/auto_xy_vision_worker.py':'14f0cff2d15b65d0f2d06e1e70dbd27b36eea8ae22ebe5db1ea549bed76476f1',
  'auto_xy/vision/auto_xy_detector.py':'c9e531f9b8e38ed61a4d68463359459a7702af6d3cf656a03ab32dfde19c5751',
}

def require(ok,msg):
    if not ok:raise RuntimeError(msg)
def digest(data):return hashlib.sha256(data).hexdigest()
def sha(path):return digest(path.read_bytes())

def verify_package():
    manifest=json.loads((ROOT/'MANIFEST.json').read_text())
    require(manifest['version']=='0.5.0-rc15-dev5','Package version mismatch')
    for rel,sha256 in manifest['sha256'].items():
        file=ROOT/rel
        require(file.is_file() and sha(file)==sha256,'Package corrupted: '+rel)

def read_only_live_check():
    with urlopen('http://127.0.0.1:7125/printer/info',timeout=7) as r:
        info=json.load(r)['result']
    require(info['state']=='ready','Klipper not ready')
    objects='save_variables&gcode_macro%20_VAOC&print_stats&virtual_sdcard&toolhead&heater_bed&extruder&extruder1'
    with urlopen('http://127.0.0.1:7125/printer/objects/query?'+objects,timeout=7) as r:
        st=json.load(r)['result']['status']
    vars=st['save_variables']['variables']
    for name in ('auto_xy_v2_enabled','auto_xy_v2_fault','auto_xy_v2_in_progress',
                 'auto_xy_v2_retry_available','auto_xy_v2_manual_in_progress'):
        require(str(vars.get(name,0)).strip().lower() not in ('1','1.0','true'),
                'AutoXY must be OFF, cleared and idle: '+name)
    require(not st['gcode_macro _VAOC'].get('is_started'),'Native VAOC still active')
    require(st['print_stats'].get('state') in ('standby','complete','cancelled','error'),
            'Print currently active')
    require(not st['virtual_sdcard'].get('is_active'),'Virtual SD active')
    require(float(st['toolhead'].get('velocity',0))==0,'Toolhead moving')
    for name in ('heater_bed','extruder','extruder1'):
        obj=st[name]
        require(float(obj.get('target',0))==0 and float(obj.get('temperature',999))<50,
                'Heater hot or enabled: '+name)
    offsets={}
    for key in ('idex_xoffset','idex_yoffset','idex_zoffset'):
        require(key in vars and math.isfinite(float(vars[key])),'Offset missing/invalid: '+key)
        offsets[key]=vars[key]
    return offsets

def config_check(home, for_rollback=False):
    cfg=home/'printer_data/config/auto_xy.cfg'
    pc=home/'printer_data/config/printer.cfg'
    require(cfg.is_file() and not cfg.is_symlink(),'Missing or symlinked auto_xy.cfg')
    require(pc.is_file() and not pc.is_symlink(),'Missing or symlinked printer.cfg')
    text=cfg.read_text()
    for name,accepted in (('settling_path',('original',)),
                          ('allow_experimental_alignment_writes',('false',)),
                          ('camera_diagnostics_enabled',('false','true')), 
                          ('camera_worker_mode',('legacy','persistent')),
                          ('camera_detector_mode',('legacy','two_stage','two_stage_roi') if for_rollback else ('legacy','two_stage'))):
        vals=[v.strip().lower() for v in re.findall(r'^\s*'+name+r'\s*:\s*([^#\r\n]+)',text,re.M)]
        require(len(vals)<=1 and (not vals or vals[0] in accepted),
                'Use safe setting before upgrade: '+name+' -> '+str(vals))
    require(len(re.findall(r'(?mi)^\s*\[include auto_xy.cfg\]\s*$',pc.read_text()))==1,
            'Expected one printer include')
    require(len(re.findall(r'(?mi)^\s*\[include auto_xy_macros.cfg\]\s*$',text))==1,
            'Expected one macro include')
    link=home/'klipper/klippy/extras/auto_xy_gate.py'
    require(link.is_symlink() and os.readlink(link)==str(home/'auto_xy/klipper/auto_xy_gate.py'),
            'Unexpected Klipper extension symlink')

def check_sources(home, hashes):
    for name,expected in hashes.items():
        f=home/name
        require(f.is_file() and not f.is_symlink(),'Missing/symlinked runtime file: '+name)
        require(sha(f)==expected,'Different runtime source: '+name+'; refusing overwrite')

def write_atomic(path,data):
    import stat
    mode=stat.S_IMODE(path.stat().st_mode)
    fd,tmp=tempfile.mkstemp(prefix='.autoxy-rc15-',dir=str(path.parent))
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def create_backup(home,offsets):
    folder=home/'auto_xy/rollbacks'/(time.strftime('%Y%m%d-%H%M%S')+'-0.5.0-rc15-dev5')
    require(not folder.parent.is_symlink(),'Rollback directory is symlinked')
    folder.mkdir(parents=True,exist_ok=False)
    backups={}
    for key in FILES:
        f=home/key
        data=f.read_bytes()
        backups[key]={'sha256':digest(data),'data_name':key.replace('/','__')}
        (folder/backups[key]['data_name']).write_bytes(data)
    (folder/'snapshot.json').write_text(json.dumps({'version':'0.5.0-rc15-dev5',
        'offset_snapshot_read_only':offsets,'files':backups},indent=2))
    return folder

def rollback(home,folder):
    folder=folder.resolve()
    require(folder.parent==(home/'auto_xy/rollbacks').resolve(),'Not a canonical rollback folder')
    manifest=json.loads((folder/'snapshot.json').read_text())
    require(manifest['version']=='0.5.0-rc15-dev5','Unknown rollback manifest')
    require(set(manifest['files'])==set(FILES),'Rollback file set mismatched')
    check_sources(home,{name:sha(ROOT/src) for name,src in FILES.items()})
    cfg=(home/'printer_data/config/auto_xy.cfg').read_text()
    require(not re.search(r'^\s*camera_detector_mode\s*:\s*two_stage_roi\b',cfg,re.M|re.I),
            'Change camera_detector_mode to two_stage or legacy before rolling back to dev4')
    for name,entry in manifest['files'].items():
        data=(folder/entry['data_name']).read_bytes()
        require(digest(data)==ORIGINAL_RC15_DEV4[name]==entry['sha256'],'Rollback dev4 source hash mismatch: '+name)
    for name,entry in manifest['files'].items():
        write_atomic(home/name,(folder/entry['data_name']).read_bytes())
    print('ROLLBACK RESTORED RC15 DEV4 SOURCES. Full Klipper service restart required.')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check',action='store_true')
    mode.add_argument('--install',action='store_true')
    mode.add_argument('--rollback',type=Path)
    p.add_argument('--confirm',default='')
    p.add_argument('--home',type=Path,default=HOME,help=argparse.SUPPRESS)
    p.add_argument('--offline',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args()
    verify_package()
    home=a.home
    if a.offline:
        require(home!=HOME,'Offline testing must not operate on real /home/pi')
    else:
        require(home==HOME,'Cannot target a nonstandard live printer')
    offsets=None if a.offline else read_only_live_check()
    config_check(home, for_rollback=bool(a.rollback))
    if a.rollback:
        require(a.confirm=='ROLLBACK_RC15_DEV5','Rollback confirmation token required')
        rollback(home,a.rollback)
        return
    check_sources(home,ORIGINAL_RC15_DEV4)
    if a.check:
        print('PREFLIGHT PASS: RC15 dev4 hashes, safe configuration and live readiness verified.')
        print('READ-ONLY; saved offsets:',offsets)
        print('Would update ONLY:',*FILES,sep='\n - ')
        print('Current detector mode unchanged; two_stage_roi is separately opt-in.')
        return
    require(a.confirm=='INSTALL_RC15_DEV5','Install confirmation token required')
    folder=create_backup(home,offsets)
    try:
        for name,source in FILES.items():
            write_atomic(home/name,(ROOT/source).read_bytes())
        check_sources(home,{name:sha(ROOT/src) for name,src in FILES.items()})
    except Exception:
        for name,entry in json.loads((folder/'snapshot.json').read_text())['files'].items():
            write_atomic(home/name,(folder/entry['data_name']).read_bytes())
        raise
    print('RC15 DEV5 FILE INSTALL COMPLETE; rollback snapshot:',folder)
    print('NO Klipper restart performed; no settings or offsets changed.')
    print('After full Klipper service restart, your current detector mode is preserved.')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('RC15 DEV5 UPGRADE REFUSED:',exc,file=sys.stderr)
        sys.exit(2)
