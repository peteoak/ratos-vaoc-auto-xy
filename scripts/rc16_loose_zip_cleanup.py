#!/usr/bin/env python3
"""Narrow reviewed cleanup of loose, superseded *installer* ZIPs only.

No changes to printer software or native config. Refuses any file outside a
short fixed list of root-level historical development packages; never touches
config archives, nozzle images, diagnostics, customer G-code, or backup trees.

check: print eligibility and optionally write a hash-bound plan JSON.
remove: requires the exact plan, unchanged file identities and an affirmative
        interactive prompt; stores a receipt before deleting eligible ZIPs.

Deletion is not reversible by this script. Copy any wanted old package off Pi
before running remove. Nothing is installed or restarted.
"""
import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HOME = Path('/home/pi')
STATE = 'auto_xy/rollbacks'
MAX_BYTES = 2_000_000
# Superseded installer/test downloads. These exact names came from the owner's
# Nov 28 listing, not a wildcard. Historical experiments may still have value.
PACKAGES = (
    'AutoXY_RC15_Dev2_Diagnostic_Logging.zip',
    'AutoXY_RC15_Dev3_Endurance_Diagnostics.zip',
    'AutoXY_RC15_Dev4_Two_Stage_Detector.zip',
    'AutoXY_RC15_Dev5_Cropped_Detector.zip',
    'AutoXY_RC15_Dev5_100_Change_Results.zip',
    'AutoXY_RC15_Persistent_Camera_Integration.zip',
    'AutoXY_RC15_Camera_Optimization_Lab.zip',
)

class Refuse(RuntimeError):
    pass

def require(cond, reason):
    if not cond:
        raise Refuse(reason)

def checksum(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def inspected(path, home=HOME):
    require(path.parent == home, 'Archive must be root-level /home/pi')
    require(path.name in PACKAGES, 'Archive not on approved package list')
    require(not path.is_symlink(), 'Symlink refused: ' + str(path))
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode), 'Not a normal file: ' + str(path))
    require(s.st_nlink == 1, 'Hardlink refused: ' + str(path))
    require(0 < s.st_size <= MAX_BYTES, 'Unexpected ZIP size: ' + str(path))
    try:
        with zipfile.ZipFile(path) as z:
            infos=z.infolist()
            require(len(infos) <= 5000 and
                    sum(x.file_size for x in infos) <= 256*1024*1024 and
                    all(x.file_size <= 128*1024*1024 for x in infos),
                    'Oversized/unexpected ZIP contents: '+str(path))
            require(z.testzip() is None, 'Damaged ZIP: ' + str(path))
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as e:
        raise Refuse('Archive integrity check refused: '+str(path)) from e
    digest = checksum(path)
    post = path.lstat()
    require(stat.S_ISREG(post.st_mode) and not path.is_symlink() and
            (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_nlink) ==
            (post.st_dev,post.st_ino,post.st_size,post.st_mtime_ns,post.st_nlink),
            'Archive changed during inspection: '+str(path))
    return {'name':path.name, 'size':s.st_size, 'sha256':digest,
            'dev':s.st_dev, 'ino':s.st_ino, 'mtime_ns':s.st_mtime_ns}

def audit(home=HOME):
    results = []
    for name in PACKAGES:
        path = home / name
        if not path.exists() and not path.is_symlink():
            continue
        results.append(inspected(path,home))
    return results

def save_json(path, obj):
    require(not path.exists() and not path.is_symlink(), 'Output already exists: '+str(path))
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as out:
        json.dump(obj,out,indent=2,sort_keys=True)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())

def prepare_plan(home=HOME, dest=None):
    found = audit(home)
    if dest is None:
        return found
    dest = Path(dest)
    require(dest.parent == home, 'Plan must be saved directly in home')
    require(dest.name.startswith('AutoXY_Zip_Package_Plan_') and dest.suffix == '.json', 'Unexpected plan name')
    save_json(dest, {'schema':1,'purpose':'reviewed root package ZIP cleanup',
                     'created_utc':datetime.now(timezone.utc).isoformat(),
                     'entries':found})
    return found

def validate_plan(home, plan_path):
    plan_path = Path(plan_path)
    require(plan_path.parent == home and plan_path.name.startswith('AutoXY_Zip_Package_Plan_')
            and plan_path.suffix == '.json' and not plan_path.is_symlink(),
            'Plan must be the generated root-level JSON')
    require(plan_path.is_file() and plan_path.stat().st_size <= 20000, 'Missing or oversized plan')
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    require(plan.get('schema') == 1 and plan.get('purpose') == 'reviewed root package ZIP cleanup',
            'Unsupported plan type')
    entries = plan.get('entries')
    require(isinstance(entries,list) and 0 < len(entries) <= len(PACKAGES), 'Invalid package selection')
    require(len(set(x.get('name') for x in entries)) == len(entries), 'Duplicate plan entry')
    require(all(x.get('name') in PACKAGES for x in entries), 'Unexpected plan file name')
    for item in entries:
        current = inspected(home/item['name'],home)
        require(current == item, 'Archive identity changed: '+item['name'])
    require(audit(home) == entries, 'Package selection changed since plan')
    return entries

def remove(home, plan_path, *, confirm, state_dir=None):
    require(home.is_dir() and not home.is_symlink(), 'Invalid home')
    entries=validate_plan(home, plan_path)
    require(confirm == 'DELETE OLD PACKAGES', 'Explicit confirmation required')
    state_dir=state_dir or home/STATE
    require(state_dir.is_dir() and not state_dir.is_symlink(), 'Missing safe receipt directory')
    receipt = state_dir / ('zip-package-retirement-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    save_json(receipt, {'schema':1,'action':'remove old loose ZIP packages',
                        'plan_sha256':checksum(plan_path),'entries':entries,
                        'warning':'Content not recoverable from this receipt'})
    for item in entries:
        path=home/item['name']
        require(inspected(path,home)==item, 'Changed before unlink: '+item['name'])
        path.unlink()
    return receipt, entries

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('check','remove'))
    p.add_argument('--plan',help='Exactly the plan generated by check')
    args=p.parse_args(argv)
    try:
        if args.action=='check':
            path = HOME / ('AutoXY_Zip_Package_Plan_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
            entries=prepare_plan(HOME,path)
            print('REVIEW ONLY: These historical loose-package ZIPs are candidates; they are not active code.')
            for x in entries:
                print(f"{x['size']:>9} bytes  {x['name']}")
            print('Total bytes eligible:', sum(x['size'] for x in entries))
            print('Plan:',path)
            print('Config backups, camera references, diagnostic ZIPs, and all other files: EXCLUDED')
            print('No ZIP deleted. Old packages may hold historical material not reproduced on GitHub.')
        else:
            require(args.plan is not None, 'Supply --plan from check')
            entries=validate_plan(HOME,args.plan)
            print('About to PERMANENTLY remove',len(entries),'historical installer packages, not backups/diagnostics.')
            print('Bytes:',sum(x['size'] for x in entries))
            answer=input('Type DELETE OLD PACKAGES to confirm: ')
            receipt, removed=remove(HOME,Path(args.plan),confirm=answer)
            for item in removed: print('REMOVED:',str(HOME/item['name']))
            print('Receipt:',receipt)
            print('No AutoXY/Klipper active files, config, backups, references or G-code changed.')
    except (Refuse,OSError,ValueError,KeyError,TypeError,EOFError) as exc:
        print('REFUSED:',str(exc),file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':
    sys.exit(main())