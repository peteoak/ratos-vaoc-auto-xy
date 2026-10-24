#!/usr/bin/env python3
"""Conservative clean uninstaller for RatOS VAOC Auto XY.

--check shows what would be changed; --uninstall needs an idle printer with
Auto XY OFF. --purge-project-files deletes ~/auto_xy INCLUDING run images and
prior stages only after a separate explicit confirmation. Backups are placed
OUTSIDE that tree. RatOS saved_variables (including IDEX offsets) are NEVER
edited or deleted.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from printer_cfg_editor import remove_all_autoxy_includes, atomic_write_text

HOME = Path('/home/pi')
PURGE_PHRASE = 'DELETE_AUTO_XY_DATA'


def target_paths(home):
    config = home / 'printer_data/config'
    return {'cfg': config/'printer.cfg',
            'variables': config/'ratos-variables.cfg',
            'print_cfg': config/'auto_xy_print.cfg',
            'old_cfg': config/'auto_xy_control.cfg',
            'klipper': home/'klipper/klippy/extras/auto_xy_gate.py',
            'installed': home/'auto_xy/stage6',
            'project': home/'auto_xy',
            'venv': home/'auto-xy-venv',
            'backup_root': home/'auto_xy_uninstall_backups'}


def inspect(home, purge_project=False, purge_venv=False):
    paths = target_paths(home)
    printer_cfg = paths['cfg']
    if not printer_cfg.is_file() or printer_cfg.is_symlink():
        raise RuntimeError('Normal printer.cfg missing or symlinked; manual review needed')
    text = printer_cfg.read_text()
    if '[include RatOS.cfg]' not in text:
        raise RuntimeError('This does not appear to be a RatOS printer.cfg')
    updated = remove_all_autoxy_includes(text)
    for key in ('print_cfg','old_cfg'):
        p = paths[key]
        if p.is_symlink():
            raise RuntimeError('Refusing to remove symlink: '+str(p))
        if p.exists() and p.is_file():
            data = p.read_text()
            if '[gcode_macro AUTO_XY_' not in data and '[auto_xy_gate]' not in data:
                raise RuntimeError('Unrecognised existing config; inspect before deleting: '+str(p))
    ext = paths['klipper']
    if ext.is_symlink():
        raise RuntimeError('Refusing to delete a symlinked Klipper extension')
    if ext.exists() and ('class AutoXYGate' not in ext.read_text() or 'cmd_AUTO_XY_GATE' not in ext.read_text()):
        raise RuntimeError('Klipper extension does not look like this AutoXY project; review: '+str(ext))
    for key in ('installed','project','venv'):
        if paths[key].is_symlink():
            raise RuntimeError('Refusing to remove a symlinked directory: '+str(paths[key]))
    # Never let the cleanup include its own preserved backups.
    if paths['backup_root'].is_symlink() or paths['backup_root'].resolve() == paths['project'].resolve():
        raise RuntimeError('Unsafe backup directory')
    actions = []
    if updated != text: actions.append(('edit',printer_cfg))
    for key in ('print_cfg','old_cfg','klipper'):
        if paths[key].is_file(): actions.append(('delete',paths[key]))
    if paths['installed'].is_dir() and not purge_project:
        actions.append(('delete-installed-folder',paths['installed']))
    if purge_project and paths['project'].is_dir():
        actions.append(('PURGE-PROJECT-ALL-HISTORY',paths['project']))
    if purge_venv and paths['venv'].is_dir():
        actions.append(('PURGE-VENV',paths['venv']))
    return paths, updated, actions


def require_printer_idle():
    # Reuse the same read-only safety check as the installer.
    from install_stage6 import live_status
    live_status()


def apply_uninstall(home, purge_project=False, purge_venv=False):
    paths, updated, actions = inspect(home,purge_project,purge_venv)
    backup = paths['backup_root'] / time.strftime('%Y%m%d-%H%M%S')
    if backup.exists():
        raise RuntimeError('Backup folder already exists; do not overwrite: '+str(backup))
    backup.mkdir(parents=True, exist_ok=False)
    # Back up the actual running settings BEFORE touching any installed files.
    for key in ('cfg','variables','print_cfg','old_cfg','klipper'):
        p=paths[key]
        if p.is_file():
            shutil.copy2(p,backup/(key+'__'+p.name))
    (backup/'uninstall_manifest.json').write_text(json.dumps({
        'actions':[(kind,str(path)) for kind,path in actions],
        'saved_ratOS_variables_preserved':True,
        'backup_location':str(backup)},indent=2))
    if updated != paths['cfg'].read_text():
        atomic_write_text(paths['cfg'],updated)
    for key in ('print_cfg','old_cfg','klipper'):
        if paths[key].is_file():
            paths[key].unlink()
    # Do not remove unknown files in the dedicated stage6 path on ordinary uninstall.
    installed=paths['installed']
    if installed.is_dir() and not purge_project:
        allowed = {'auto_xy_vision_worker.py','auto_xy_detector.py','disable_stage6.py',
                   'check_last_failure.py','printer_cfg_editor.py','repair_save_config.py',
                   'auto_xy_retention.py','uninstall_auto_xy.py'}
        for child in installed.iterdir():
            if child.is_file() and not child.is_symlink() and child.name in allowed:
                child.unlink()
        if not list(installed.iterdir()):
            installed.rmdir()
    if purge_project and paths['project'].is_dir():
        shutil.rmtree(paths['project'])
    if purge_venv and paths['venv'].is_dir():
        shutil.rmtree(paths['venv'])
    return backup


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    m=ap.add_mutually_exclusive_group(required=True)
    m.add_argument('--check',action='store_true')
    m.add_argument('--uninstall',action='store_true')
    ap.add_argument('--purge-project-files',action='store_true',
                    help='Delete ALL ~/auto_xy files, including history and old stages')
    ap.add_argument('--purge-venv',action='store_true',
                    help='Delete ~/auto-xy-venv too (dependencies must be reinstalled)')
    ap.add_argument('--confirm-purge',default='',
                    help='Required exact text: DELETE_AUTO_XY_DATA')
    args=ap.parse_args(argv)
    home=HOME
    if (args.purge_project_files or args.purge_venv) and args.uninstall and args.confirm_purge!=PURGE_PHRASE:
        ap.error('Purging data requires --confirm-purge '+PURGE_PHRASE)
    release_folder=Path(__file__).resolve().parent
    if args.uninstall and args.purge_project_files and release_folder.is_relative_to((home/'auto_xy').resolve()):
        raise RuntimeError('Place this release outside ~/auto_xy before deleting the project tree')
    paths, _, actions=inspect(home,args.purge_project_files,args.purge_venv)
    print('Auto XY uninstall plan (NO changes to RatOS saved offsets):')
    for kind,path in actions:
        print('  %s: %s'%(kind,path))
    if args.check:
        print('DRY RUN COMPLETE. Nothing was removed.')
        return
    require_printer_idle()
    backup=apply_uninstall(home,args.purge_project_files,args.purge_venv)
    print('UNINSTALL COMPLETE. Configuration and saved-variable backup:',backup)
    print('IMPORTANT: Restart Klipper NOW before using or reinstalling Auto XY.')
    print('The preserved ratos-variables.cfg still contains the last calibrated IDEX offsets and Auto XY flags.')

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print('UNINSTALL REFUSED:',exc,file=sys.stderr)
        sys.exit(2)
