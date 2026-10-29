#!/usr/bin/env python3
"""AutoXY v0.5.0 RC15 dev5: guarded fresh-only installer (new users).

Safe by default: --check only reads; --install never changes RatOS saved offsets.
Only use --install after separately reviewing preflight results, with a cool,
idle printer and no active fault, VAOC, or print.
"""
import argparse
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import ratos_extension
from urllib.request import Request, urlopen

VERSION = '0.5.0-rc15-dev5'
HOME = Path('/home/pi')
EXPECTED_RC7 = {
    'auto_xy/klipper/auto_xy_gate.py':'9c67c41059a6e234f9a2630c76a5d4ebf070ecb32434509366a1af19bcc77cdc',
    'auto_xy/vision/auto_xy_detector.py':'4c6e1f37fc60e6ad79a6fd474730ba1ba4f2916cc813e9d176580fbb23b519e6',
    'auto_xy/vision/auto_xy_vision_worker.py':'87a76b9a11330b60965f01292749c452cdbcd2a3bbeda533a9ac9e2b8cb6f5ec',
    'auto_xy/vision/auto_xy_retention.py':'f85b99f36e28a54b5405b785279fa667276a5b57e068deb349de6af08db2627a',
    'printer_data/config/auto_xy_macros.cfg':'fd4e19d28d9082118c43c7d49f602a99abd49e15a1dac216c66e2d446e8b5fa2',
}
MANAGED = (
    'auto_xy/klipper/auto_xy_gate.py',
    'auto_xy/vision/auto_xy_detector.py',
    'auto_xy/vision/auto_xy_vision_worker.py',
    'auto_xy/vision/auto_xy_retention.py',
    'printer_data/config/auto_xy.cfg',
    'printer_data/config/auto_xy_macros.cfg',
    'printer_data/config/printer.cfg',
    'auto_xy/camera_commission.json',
    'klipper/klippy/extras/auto_xy_gate.py',
)


# A private operation marker; never written to any printer file.
RETIRE = b'\x00AUTOXY_RETIRE_EXISTING_PROFILE\x00'

def sha(buf): return hashlib.sha256(buf).hexdigest()
def digest(p): return sha(p.read_bytes())
def require(ok,msg):
    if not ok: raise RuntimeError(msg)

def verify_package(root):
    manifest=json.loads((root/'MANIFEST.json').read_text())
    require(manifest['version']==VERSION, 'Package manifest version incorrect')
    for name,checksum in manifest['sha256'].items():
        f=root/name
        require(f.is_file() and digest(f)==checksum,'Package file differs: '+name)
    gate=(root/'src/auto_xy_gate.py').read_text()
    require('RELEASE_VERSION = "'+VERSION+'"' in gate,'Klipper controller wrong version')
    macros=(root/'config/auto_xy_macros.cfg').read_text()
    names=re.findall(r'^\[gcode_macro\s+([^\]]+)\]\s*$',macros,re.M)
    require(len(names)==20 and len(set(names))==20,'Expected exactly 20 unique managed macros')
    config=(root/'config/auto_xy.cfg.example').read_text()
    require(len(re.findall(r'^\[include auto_xy_macros.cfg\]\s*$',config,re.M))==1,
            'Exactly one nested include required')
    require(not re.search(r'(?m)^\s*idex_[xyz]offset\s*[:=]',config),
            'Package must never hard-code nozzle offsets')


def moonraker_status():
    fields={'save_variables':None,'print_stats':None,'virtual_sdcard':None,
            'gcode_macro _VAOC':None,'heater_bed':None,'extruder':None,
            'extruder1':None,'toolhead':None,'configfile':None}
    data=json.dumps({'objects':fields}).encode()
    req=Request('http://127.0.0.1:7125/printer/objects/query',data=data,
                headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=8) as response:
        status=json.load(response)['result']['status']
    with urlopen('http://127.0.0.1:7125/printer/info',timeout=8) as response:
        info=json.load(response)['result']
    return status,info


STOCK_RATOS_NOOP_HOOKS = frozenset((
    '_USER_START_PRINT', '_USER_START_PRINT_AFTER_HEATING_BED',
))


def normalised_config_names(settings):
    """Check real Klipper section names independently of display case."""
    require(isinstance(settings, dict), 'Klipper settings unavailable')
    return {str(key).casefold() for key in settings}


def require_ratos_components(settings):
    names = normalised_config_names(settings)
    for name in ('gcode_macro _VAOC', 'gcode_macro RatOS'):
        require(name.casefold() in names, 'Missing RatOS component: ' + name)
    return names


def validate_macro_collisions(settings, raw_config, macro_names):
    """Allow only the two observed no-op RatOS hooks, never custom G-code.

    The `settings` map detects loaded macros and Moonraker's raw `config`
    map proves the actual selected macro body is blank. If that provenance
    cannot be checked, an existing name is a blocking collision.
    """
    require(isinstance(settings, dict), 'Klipper settings unavailable')
    loaded = normalised_config_names(settings)
    actual = ({str(name).lower(): value for name, value in raw_config.items()}
              if isinstance(raw_config, dict) else {})
    conflicts = []
    for name in macro_names:
        key = 'gcode_macro ' + name.lower()
        if key not in loaded:
            continue
        definition = actual.get(key)
        is_blank_ratos_hook = (
            name.upper() in STOCK_RATOS_NOOP_HOOKS and
            isinstance(definition, dict) and
            isinstance(definition.get('gcode'), str) and
            not definition['gcode'].strip()
        )
        if not is_blank_ratos_hook:
            conflicts.append(name)
    require(not conflicts, 'Klipper G-code macro collision(s): ' + ', '.join(conflicts))


def live_safe(home,mode):
    s,info=moonraker_status()
    require(info.get('state')=='ready','Klipper must be Ready')
    vars=s['save_variables']['variables']
    truthy = lambda key: str(vars.get(key,0)).strip().lower() in ('1','1.0','true')
    if mode in ('fresh', 'rollback'):
        require(not truthy('auto_xy_enabled'), 'Legacy AutoXY still enabled')
        require(not truthy('auto_xy_retry_available'),
                'Legacy AutoXY still enabled/held; manual recovery required')
        # Old RC7 fault/in_progress are a preserved, inert record after
        # an independently verified quarantine of the old installation.
        # DO NOT clear them or edit ratos-variables.cfg.
        for key in ('auto_xy_v2_enabled','auto_xy_v2_fault',
                    'auto_xy_v2_in_progress','auto_xy_v2_retry_available'):
            require(not truthy(key), 'Active or latched new AutoXY state: '+key)
        if mode=='fresh':
            ratos_extension.verify_absent(home)
    else:
        for key in ('auto_xy_enabled','auto_xy_fault',
                    'auto_xy_in_progress','auto_xy_retry_available'):
            require(not truthy(key), 'Active or latched AutoXY state: '+key)
    require(not s['gcode_macro _VAOC'].get('is_started'),'RatOS VAOC must be exited')
    require(s['print_stats']['state'] in ('standby','complete','cancelled','error'), 'Print active')
    require(not s['virtual_sdcard'].get('is_active'), 'Virtual SD printing')
    require(float(s['toolhead'].get('velocity',0))==0, 'Printer is moving')
    for heater in ('extruder','extruder1','heater_bed'):
        v=s[heater]
        require(float(v.get('target',0))==0,'Heater target active: '+heater)
        require(float(v.get('temperature',0))<50,'Heater still hot: '+heater)
    settings=s['configfile']['settings']
    if mode in ('upgrade','clean-reinstall'):
        require(settings.get('auto_xy_gate',{}).get('worker')==
                str(home/'auto_xy/vision/auto_xy_vision_worker.py'),
                'Live RC7 worker path differs')
    else:
        venv=home/'auto-xy-venv/bin/python'
        require(venv.is_file(), 'Vision Python venv missing: '+str(venv))
        deps=subprocess.run([str(venv),'-c','import cv2, numpy; print(cv2.__version__,numpy.__version__)'], capture_output=True, text=True, timeout=12)
        require(deps.returncode==0, 'Vision venv missing numpy/OpenCV: '+deps.stderr.strip())
        require('auto_xy_gate' not in normalised_config_names(settings),
                'AutoXY already loaded: use upgrade/migration, not fresh')
        macro_names=re.findall(r'^\[gcode_macro\s+([^\]]+)\]\s*$',
                               (Path(__file__).resolve().parents[1]/'config/auto_xy_macros.cfg').read_text(),re.M)
        validate_macro_collisions(settings, s['configfile'].get('config'), macro_names)
        # Verify this is an explicitly commissioned *compatible RatOS* setup.
        require_ratos_components(settings)
    # Current offsets always belong to the RatOS installation being checked.
    # Never substitute the developer's offsets or arbitrary zero defaults.
    offset_keys=('idex_xoffset','idex_yoffset','idex_zoffset')
    for key in offset_keys:
        require(key in vars,'RatOS offset missing: '+key+'. Calibrate with native VAOC first.')
        try: require(math.isfinite(float(vars[key])),'RatOS offset not finite: '+key)
        except (TypeError,ValueError): raise RuntimeError('RatOS offset is invalid: '+key)
    if mode=='fresh':
        from verify_stock_camera import validate_stock_camera
        validate_stock_camera()
    return {k:vars[k] for k in offset_keys}


def identify(home):
    config=home/'printer_data/config'
    if (config/'auto_xy.cfg').exists() or (home/'auto_xy/klipper/auto_xy_gate.py').exists():
        return 'upgrade'
    return 'fresh'


def add_single_include_safely(text):
    """Add AutoXY above Klipper SAVE_CONFIG; never append inside generated block.

    Klipper writes managed SAVE_CONFIG data below a #*# delimiter, and
    material after that delimiter is not ordinary printer configuration.
    """
    marker=re.search(r'(?m)^#\*#',text)  # FIRST generated line, independent of RatOS/Klipper header styling
    if marker:
        # KEEP the SAVE_CONFIG section byte-for-byte; insert a genuine
        # [include] before the start of Klipper's generated-comment block.
        i=marker.start()
        lead=text[:i].rstrip('\r\n')
        rest=text[i:]
        return lead+'\n\n# AutoXY (one direct include)\n[include auto_xy.cfg]\n\n'+rest
    return text.rstrip('\r\n')+'\n\n# AutoXY (one direct include)\n[include auto_xy.cfg]\n'


def plan(home,root,mode):
    verify_package(root)
    home=home.resolve()
    require(home.is_dir(),'Home directory does not exist')
    require(not (home/'auto_xy/rollbacks').is_symlink(),'Rollback directory symlinked')
    config_dir=home/'printer_data/config'
    require(config_dir.is_dir(),'Missing Klipper configuration directory')
    printer=config_dir/'printer.cfg'
    require(printer.is_file() and not printer.is_symlink(),'printer.cfg missing or symlinked')
    link=home/'klipper/klippy/extras/auto_xy_gate.py'
    require(link.parent.is_dir(),'Klipper extension directory missing')
    pc=printer.read_text()
    includes=re.findall(r'(?mi)^\s*\[include\s+([^\]\r\n]+)\]\s*$',pc)
    sources={
        'auto_xy/klipper/auto_xy_gate.py':'src/auto_xy_gate.py',
        'auto_xy/vision/auto_xy_detector.py':'src/auto_xy_detector.py',
        'auto_xy/vision/auto_xy_vision_worker.py':'src/auto_xy_vision_worker.py',
        'auto_xy/vision/auto_xy_retention.py':'src/auto_xy_retention.py',
        'printer_data/config/auto_xy_macros.cfg':'config/auto_xy_macros.cfg',
    }
    changes={name:(root/src).read_bytes() for name,src in sources.items()}
    if mode in ('upgrade','clean-reinstall'):
        for rel,d in EXPECTED_RC7.items():
            path=home/rel
            require(path.is_file() and not path.is_symlink() and digest(path)==d,
                    'RC7 source differs, refusing overwrite: '+rel)
        require(link.is_symlink() and link.resolve()==home/'auto_xy/klipper/auto_xy_gate.py',
                'RC7 extension symlink changed')
        user_cfg=config_dir/'auto_xy.cfg'
        require(user_cfg.is_file() and not user_cfg.is_symlink(),'No canonical RC7 settings')
        user_data=user_cfg.read_text()
        require(len(re.findall(r'(?mi)^\s*\[include auto_xy_macros.cfg\]\s*$',user_data))==1,
                'Unexpected RC7 macro include')
        require(len([x for x in includes if x.lower()=='auto_xy.cfg'])==1 and
                not any(x.lower().startswith('auto_xy') and x.lower()!='auto_xy.cfg' for x in includes),
                'Expected exactly one AutoXY include in printer.cfg')
        require('auto_xy_start.cfg' not in user_data,'Unexpected legacy start include')
        # Bring the INSTALLED runtime under the same reviewed source as GitHub.
        # Existing camera/thermal/tolerance CONFIGURATION remains untouched.
        # Rollback snapshots guard vision changes and the controller together.
        if mode=='upgrade':
            changes={name:value for name,value in changes.items()
                     if name in ('auto_xy/klipper/auto_xy_gate.py',
                                 'auto_xy/vision/auto_xy_detector.py',
                                 'auto_xy/vision/auto_xy_vision_worker.py',
                                 'printer_data/config/auto_xy_macros.cfg')}
        else:
            # A fresh USER EXPERIENCE on a previously commissioned RC7 printer:
            # stock, default-OFF config, no previous camera profile. The camera
            # profile is backed up inside the exact rollback snapshot, not
            # copied into the new installation. Saved RatOS offsets and
            # printer.cfg remain BYTE-FOR-BYTE untouched.
            example=(root/'config/auto_xy.cfg.example').read_text()
            require('protected_print_enabled: False' in example and
                    'camera_registration_enabled: False' in example and
                    'inline_print_retry: False' in example,
                    'Clean reinstall requires uncommissioned fail-closed defaults')
            changes['printer_data/config/auto_xy.cfg']=example.encode()
            profile=home/'auto_xy/camera_commission.json'
            if profile.exists() or profile.is_symlink():
                require(profile.is_file() and not profile.is_symlink(),
                        'Existing camera profile must be a regular file')
                changes['auto_xy/camera_commission.json']=RETIRE
    elif mode=='fresh':
        require(not any(x.lower().startswith('auto_xy') for x in includes),
                'AutoXY include already present')
        require(not (config_dir/'auto_xy_start.cfg').exists() and
                not (config_dir/'auto_xy_controls.cfg').exists(),
                'Legacy AutoXY config exists: not a fresh installation')
        for name in sources:
            require(not (home/name).exists() and not (home/name).is_symlink(),
                    'File already present; refusing fresh installation: '+name)
        require(not link.exists() and not link.is_symlink(),
                'Klipper AutoXY extension already present')
        example=(root/'config/auto_xy.cfg.example').read_text()
        # Fresh installation remains OFF + uncommissioned.
        require('protected_print_enabled: False' in example and
                'camera_registration_enabled: False' in example,
                'Fresh example must be fail-closed by default')
        changes['printer_data/config/auto_xy.cfg']=example.encode()
        changes['printer_data/config/printer.cfg']=add_single_include_safely(pc).encode()
        changes['klipper/klippy/extras/auto_xy_gate.py']=('SYMLINK:'+str(home/'auto_xy/klipper/auto_xy_gate.py')).encode()
    else:
        raise RuntimeError('Unknown installation mode')
    for rel in changes:
        require(rel in MANAGED,'Unexpected managed path: '+rel)
        path=home/rel
        # Link explicitly allowed only when upgrade isn't modifying it.
        require(not path.is_symlink(),'Refusing symlink as managed write target: '+rel)
    return changes


def write_atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    require(not path.is_symlink(),'Refusing to write into symlink: '+str(path))
    fd,tmp=tempfile.mkstemp(prefix='.autoxy-rc15dev5-',dir=str(path.parent))
    try:
        if path.exists(): os.fchmod(fd,stat.S_IMODE(path.stat().st_mode))
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def snapshot(home,changes,mode,offsets):
    root=home/'auto_xy/rollbacks'
    root.mkdir(parents=True,exist_ok=True)
    require(not root.is_symlink(),'Rollback directory symlinked')
    folder=root/(time.strftime('%Y%m%d-%H%M%S')+'-'+VERSION+'-'+mode)
    folder.mkdir(exist_ok=False)
    entries=[]
    for rel,content in changes.items():
        path=home/rel
        require(not path.is_symlink(),'Managed write target symlinked: '+rel)
        item={'path':rel,'old_exists':path.exists(),
              'new_sha256': None if content.startswith(b'SYMLINK:') or content==RETIRE else sha(content),
              'new_retired': content==RETIRE,
              'new_link':content.decode()[8:] if content.startswith(b'SYMLINK:') else None}
        if path.exists():
            old=path.read_bytes()
            backup=folder/'files'/rel
            backup.parent.mkdir(parents=True,exist_ok=True)
            backup.write_bytes(old)
            item['old_sha256']=sha(old)
            item['old_mode']=stat.S_IMODE(path.stat().st_mode)
        entries.append(item)
    manifest={'version':VERSION,'mode':mode,'home':str(home),'offset_snapshot_read_only':offsets,'entries':entries}
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2))
    return folder,manifest


def restore(home,folder,manifest,verify_installed=True):
    require(manifest.get('home')==str(home) and manifest.get('version')==VERSION,
            'Rollback manifest does not belong to this install')
    for ent in manifest['entries']:
        rel=ent['path']
        require(rel in MANAGED,'Unknown rollback path '+rel)
        path=home/rel
        if verify_installed:
            if ent.get('new_retired'):
                require(not path.exists() and not path.is_symlink(),
                        'Retired camera profile reappeared; refusing rollback')
            elif ent['new_link'] is not None:
                require(path.is_symlink() and os.readlink(path)==ent['new_link'],
                        'Installed extension link changed; refusing rollback')
            else:
                require(path.is_file() and not path.is_symlink() and digest(path)==ent['new_sha256'],
                        'Installed file changed; refusing rollback: '+rel)
    for ent in reversed(manifest['entries']):
        path=home/ent['path']
        if ent['old_exists']:
            data=(folder/'files'/ent['path']).read_bytes()
            require(sha(data)==ent['old_sha256'], 'Backup file corrupt: '+ent['path'])
            write_atomic(path,data)
            os.chmod(path,ent['old_mode'])
        else:
            if path.is_symlink() or path.exists():path.unlink()


def install(home,changes,mode,offsets):
    folder,manifest=snapshot(home,changes,mode,offsets)
    live_fresh=(home.resolve()==HOME and mode=='fresh')
    if live_fresh:
        ratos_extension.verify_absent(home)
    registered=False
    try:
        for rel,content in changes.items():
            path=home/rel
            if content==RETIRE:
                require(path.is_file() and not path.is_symlink(),
                        'Camera profile changed before retirement')
                path.unlink()
            elif content.startswith(b'SYMLINK:'):
                require(not path.exists() and not path.is_symlink(),'Extension target appeared before commit')
                if not live_fresh:
                    path.symlink_to(content.decode()[8:])
            else:write_atomic(path,content)
        if live_fresh:
            ratos_extension.register(home,home/'auto_xy/klipper/auto_xy_gate.py')
            registered=True
        for ent in manifest['entries']:
            path=home/ent['path']
            if ent.get('new_retired'):
                require(not path.exists() and not path.is_symlink(), 'Camera profile retirement failed')
            elif ent['new_link'] is not None:
                require(path.is_symlink() and os.readlink(path)==ent['new_link'], 'Extension link mismatch')
            else:
                require(path.is_file() and digest(path)==ent['new_sha256'], 'Post-install checksum mismatch')
    except BaseException:
        if live_fresh:
            current=ratos_extension.snapshot(home)
            expected=str(home/'auto_xy/klipper/auto_xy_gate.py')
            if current['registered']==expected:
                ratos_extension.unregister(home,expected,current['link'])
        restore(home,folder,manifest,verify_installed=False)
        raise
    return folder


def rollback(home,folder,live=True):
    root=(home/'auto_xy/rollbacks').resolve()
    f=folder.resolve(strict=True)
    require(f.parent==root,'Rollback snapshot outside managed folder')
    manifest=json.loads((f/'manifest.json').read_text())
    if live:live_safe(home,'rollback')
    if live and manifest.get('mode')=='fresh':
        require(home==HOME,'Live rollback requires canonical Pi home')
        expected=str(home/'auto_xy/klipper/auto_xy_gate.py')
        before=ratos_extension.snapshot(home)
        require(before['registered']==expected and before['link']==expected,
                'Installed RatOS AutoXY registration/link changed; abort rollback')
        # Prevalidate all tracked files BEFORE unlinking the extension.
        for ent in manifest['entries']:
            path=home/ent['path']
            if ent.get('new_retired'):
                require(not path.exists(), 'Retired profile changed')
            elif ent['new_link'] is not None:
                require(path.is_symlink() and os.readlink(path)==ent['new_link'],
                        'Installed link changed')
            else:
                require(path.is_file() and not path.is_symlink() and
                        digest(path)==ent['new_sha256'],
                        'Installed file changed: '+ent['path'])
        ratos_extension.unregister(home,expected,expected)
        restore(home,f,manifest,verify_installed=False)
        ratos_extension.verify_absent(home)
    else:
        restore(home,f,manifest)
    print('ROLLBACK COMPLETE. Restart Klipper while idle/cool; no saved offsets modified.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    choice=parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--check',action='store_true')
    choice.add_argument('--install',action='store_true')
    choice.add_argument('--rollback',type=Path)
    parser.add_argument('--mode',choices=('fresh','auto'),default='auto')
    parser.add_argument('--home',type=Path,default=HOME,help=argparse.SUPPRESS)
    parser.add_argument('--offline',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--confirm',default='',help='Required approval token for actual installation')
    a=parser.parse_args()
    home=a.home.resolve()
    root=Path(__file__).resolve().parents[1]
    if not a.offline:require(home==HOME,'Live install only supports /home/pi on RatOS 2.1')
    if a.rollback:
        rollback(home,a.rollback,live=not a.offline);return
    mode=identify(home) if a.mode=='auto' else a.mode
    if not a.rollback and mode!='fresh':
        raise RuntimeError('This is a fresh-install-only entry point; an existing AutoXY installation must NOT be overwritten. Use check_installed_dev5.py or reviewed upgrade instead.')
    if not a.offline:
        require(mode=='fresh',
                'RC13 live upgrade/clean-reinstall disabled: use reviewed RC7 quarantine then --mode fresh')
    changes=plan(home,root,mode)
    offsets=live_safe(home,mode) if not a.offline else None
    print('AutoXY',VERSION,'preflight PASS; mode:',mode)
    print('Saved nozzle offsets (READ ONLY):',offsets)
    print('Planned managed files:')
    for rel,data in changes.items():
        print(' ',home/rel, '-> symlink' if data.startswith(b'SYMLINK:') else '-> archive/retire existing profile' if data==RETIRE else 'SHA256 '+sha(data))
    if a.check:
        print('CHECK PASSED; NO FILES CHANGED')
    else:
        require(a.confirm=='INSTALL_RC15_DEV5_FRESH','Installation requires --confirm INSTALL_RC15_DEV5_FRESH')
        folder=install(home,changes,mode,offsets)
        print('AutoXY',VERSION,'installed; rollback:',folder)
        if mode in ('fresh','clean-reinstall'):
            print('FRESH INSTALL IS DEFAULT-OFF/UNCOMMISSIONED. DO NOT ENABLE CALIBRATION UNTIL FULL CAMERA COMMISSIONING.')
        print('Saved nozzle offsets NEVER modified. FULL Klipper service restart required while idle/cool.')
        print('Verify loaded code/config and perform supervised tests before any protected print.')

if __name__=='__main__':
    try: main()
    except Exception as error:
        print('AutoXY installer REFUSED:',error,file=sys.stderr)
        sys.exit(2)
