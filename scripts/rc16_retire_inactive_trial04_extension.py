#!/usr/bin/env python3
"""RC16: retire the exact inactive Trial04 Klipper import symlink only.

No G-code, printer cfg/macro, installed source, camera references, offsets,
preexisting backup, or RatOS file is changed. No Klipper restart needed:
the old extension is proven NOT loaded, and removing an unloaded symlink
only prevents a later config from importing it until explicitly restored.

Commands: check, retire, restore --receipt <this script's receipt.json>
Runs only on a Klipper Ready, idle and unloaded RC16 installation with
VAOC closed, an active SD/resume guard, and no AutoXY error/retry markers.
All source and symlink identities are pinned to the owner's audit ZIP.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

HOME = Path('/home/pi')
EXTRA = 'klipper/klippy/extras/auto_xy_local_recovery.py'
SOURCE = 'auto_xy/klipper/auto_xy_local_recovery.py'
PRINTER_CFG = 'printer_data/config/printer.cfg'
RC16_CONFIG = 'printer_data/config/auto_xy_rc16.cfg'
LEGACY_CONFIG = 'printer_data/config/auto_xy_local_recovery.cfg'
ROLLBACK_ROOT = 'auto_xy/rollbacks'
LEGACY_SHA256 = '12ddf4100b6b61cbb71f3cc1b26d0d87e2134a8c9cbdb0385f83aba50e11c03f'
INFO = 'http://127.0.0.1:7125/printer/info'
STATUS = ('http://127.0.0.1:7125/printer/objects/query?'
          'print_stats&virtual_sdcard&gcode_macro%20_VAOC&'
          'auto_xy_ratos_handoff_rc16&save_variables&configfile')
LEGACY_INCLUDE = re.compile(r'^\s*\[include\s+auto_xy_local_recovery\.cfg\]\s*$', re.I)


class Refuse(RuntimeError):
    pass


def require(ok, msg):
    if not ok:
        raise Refuse(msg)


def request(url):
    with urlopen(url, timeout=8) as r:
        body = json.load(r)
    require(isinstance(body, dict) and not body.get('error') and
            isinstance(body.get('result'), dict), 'Moonraker query refused')
    return body['result']


def active_status():
    require(request(INFO).get('state') == 'ready', 'Klipper is not ready')
    return request(STATUS).get('status', {})


def is_on(v):
    return str(v).strip().lower() in ('true', '1', '1.0')


def assert_safe(s):
    stats, sd = s.get('print_stats'), s.get('virtual_sdcard')
    hand, vaoc = s.get('auto_xy_ratos_handoff_rc16'), s.get('gcode_macro _VAOC')
    saved = s.get('save_variables', {}).get('variables') if isinstance(s.get('save_variables'),dict) else None
    config = s.get('configfile', {}).get('settings') if isinstance(s.get('configfile'), dict) else None
    require(isinstance(stats,dict) and stats.get('state') in ('standby','complete','cancelled')
            and isinstance(sd,dict) and sd.get('is_active') is False
            and sd.get('file_path') in (None, '') and sd.get('filename') in (None, '')
            and isinstance(vaoc,dict) and vaoc.get('is_started') is False
            and isinstance(hand,dict) and all(hand.get(k) is True for k in
                 ('enabled','armed','sd_api_guarded','resume_api_guarded'))
            and hand.get('busy') is False and isinstance(saved,dict)
            and all(not is_on(saved.get(f,0)) for f in
                    ('auto_xy_v2_fault','auto_xy_v2_in_progress','auto_xy_v2_retry_available'))
            and isinstance(config,dict) and 'auto_xy_local_recovery' not in config
            and 'auto_xy_ratos_handoff_rc16' in config,
            'Require ready, idle/unloaded, no native VAOC or AutoXY fault/retry, '
            'RC16 SD/resume guards active, legacy extension not loaded')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular(path):
    require(path.is_file() and not path.is_symlink(), 'Unsafe/missing regular file '+str(path))


def inspect(home, *, require_link=True):
    home = Path(home)
    source, ext = home/SOURCE, home/EXTRA
    for p in [source,home/PRINTER_CFG,home/RC16_CONFIG,home/LEGACY_CONFIG]:
        regular(p)
    require(sha(source) == LEGACY_SHA256, 'Installed legacy source is not exact audited SHA256')
    require(ext.parent.is_dir() and not ext.parent.is_symlink(),
            'Klipper extras directory is missing or symlinked')
    # Only recognise the observed singleton inactive integration; exact active
    # startup state is verified separately by Moonraker configfile.settings.
    lines=(home/PRINTER_CFG).read_text(errors='replace').splitlines()
    require(not any(LEGACY_INCLUDE.fullmatch(line) for line in lines),
            'Trial04 still included in printer.cfg. Use separate RC16 retirement migration.')
    try:
        linecount=sum(1 for line in (home/RC16_CONFIG).read_text(errors='replace').splitlines()
                      if line.strip() == '[auto_xy_ratos_handoff_rc16]')
    except OSError as e:
        raise Refuse('Cannot read RC16 config') from e
    require(linecount == 1, 'RC16 handoff configuration not as expected')
    if require_link:
        require(ext.is_symlink() and ext.resolve() == source.resolve(),
                'Expected exact Trial04 symlink not present; do not touch unknown extension')
        target=os.readlink(ext)
    else:
        require(not os.path.lexists(ext), 'Expected retired link absent; unknown extension present')
        target=None
    return source, ext, target


def receipt_store(home, target):
    root=Path(home)/ROLLBACK_ROOT
    require(root.is_dir() and not root.is_symlink(), 'Unsafe/missing AutoXY rollback root')
    # Receipt is small; never copy a large private file or alter previous backups.
    rollback=Path(tempfile.mkdtemp(prefix='retired-inactive-trial04-',dir=str(root)))
    data={'schema':1,'target':EXTRA,'source':SOURCE,'link_text':target,
          'source_sha256':LEGACY_SHA256,'created_utc':datetime.now(timezone.utc).isoformat(),
          'note':'Only the obsolete, unloaded import symlink was unlinked; source retained'}
    receipt=rollback/'receipt.json'
    fd=os.open(str(receipt),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        json.dump(data,f,indent=2)
        f.write('\n')
        f.flush();os.fsync(f.fileno())
    return receipt


def verify_snapshot(home, receipt):
    home=Path(home)
    receipt=Path(receipt)
    root=home/ROLLBACK_ROOT
    require(root.is_dir() and not root.is_symlink() and
            receipt.name == 'receipt.json' and
            receipt.parent.parent.resolve() == root.resolve() and
            receipt.parent.name.startswith('retired-inactive-trial04-') and
            receipt.is_file() and not receipt.is_symlink(),
            'Receipt must be an AutoXY Trial04 retirement receipt in rollbacks')
    data=json.loads(receipt.read_text())
    require(data.get('schema')==1 and data.get('target')==EXTRA
            and data.get('source')==SOURCE and data.get('source_sha256')==LEGACY_SHA256
            and isinstance(data.get('link_text'),str),
            'Receipt does not match this exact migration')
    require((home/EXTRA).parent.joinpath(data['link_text']).resolve() == (home/SOURCE).resolve(),
            'Receipt has a different link target')
    return data


def retire(home=HOME, query=active_status):
    assert_safe(query())
    source,ext,target=inspect(home)
    # Don't silently remove a file introduced after the first preflight.
    require(ext.is_symlink() and os.readlink(ext)==target and sha(source)==LEGACY_SHA256,
            'State changed during preflight')
    receipt=receipt_store(home,target)
    did_unlink=False
    try:
        assert_safe(query())
        require(ext.is_symlink() and os.readlink(ext)==target, 'Symlink changed before retirement')
        ext.unlink()
        did_unlink=True
        inspect(home,require_link=False)
        assert_safe(query())
        return receipt
    except BaseException as e:
        if did_unlink:
            try:
                require(not os.path.lexists(ext), 'Another extension appeared; rollback unsafe')
                require(sha(source)==LEGACY_SHA256,'Legacy source changed; rollback unsafe')
                os.symlink(target,ext)
                require(ext.is_symlink() and ext.resolve()==source.resolve(),
                        'Rollback could not recreate original symlink')
            except BaseException as problem:
                raise Refuse('RETIREMENT AND ROLLBACK FAILED. Do not restart or print until inspected. '
                             f'Receipt={receipt}; reason={e}; rollback={problem}') from problem
        raise Refuse(f'Retirement refused/reverted: {e}. Receipt={receipt}') from e


def restore(home, receipt, query=active_status):
    data=verify_snapshot(home,receipt)
    assert_safe(query())
    source,ext,_=inspect(home,require_link=False)
    require(sha(source)==LEGACY_SHA256,'Legacy source changed before restore')
    # This restores only the old dormant *symlink*, NOT its active config include.
    os.symlink(data['link_text'],ext)
    try:
        inspect(home)
        assert_safe(query())
    except BaseException as e:
        if ext.is_symlink() and os.readlink(ext)==data['link_text']:
            ext.unlink()
        raise Refuse('Restore verification failed and new symlink was removed: '+str(e)) from e
    return ext


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('check','retire','restore'))
    p.add_argument('--receipt',help='Exact receipt printed by prior retire (restore only)')
    args=p.parse_args(argv)
    try:
        if args.action=='restore':
            require(args.receipt, 'Specify --receipt to restore a retired symlink')
            path=restore(HOME,args.receipt)
            print('RESTORED dormant legacy symlink:',path)
            print('Trial04 configuration remains inactive; no Klipper restart performed')
        else:
            assert_safe(active_status())
            _,ext,target=inspect(HOME)
            print('PRECHECK PASS: legacy Trial04 unloaded, RC16 guarded, exact symlink:',ext)
            print('Target:',target)
            if args.action=='retire':
                receipt=retire(HOME)
                print('RETIRED INACTIVE SYMLINK:',ext)
                print('Rollback receipt:',receipt)
                print('Source retained:',HOME/SOURCE)
                print('No Klipper restart, offsets, config or G-code changed')
        return 0
    except (OSError,ValueError,KeyError,Refuse) as e:
        print('REFUSED:',e,file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())