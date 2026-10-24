#!/usr/bin/env python3
"""Opt-in repair for a Stage6 include appended below Klipper SAVE_CONFIG.

NO contact with Klipper/Moonraker and NO offset writes; creates a backup first.
Only relocates a known Auto XY include. Refuses other corrupt/unknown content.
"""
import argparse
import sys
import shutil
import time
from pathlib import Path
from printer_cfg_editor import repair_legacy_tail, atomic_write_text


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--repair', action='store_true')
    ap.add_argument('--config', type=Path,
                    default=Path('/home/pi/printer_data/config/printer.cfg'))
    args = ap.parse_args()
    original = args.config.read_text()
    repaired = repair_legacy_tail(original)
    if original == repaired:
        print('CHECK OK: No misplaced Auto XY include after SAVE_CONFIG.')
        print('No changes required.')
        return
    if args.check:
        print('FIX AVAILABLE: Found an Auto XY include after SAVE_CONFIG.')
        print('Run --repair only when the printer is idle, after reviewing a backup.')
        return
    backup = args.config.with_name(args.config.name + '.before_autoxy_saveconfig_fix_' +
                                   time.strftime('%Y%m%d_%H%M%S'))
    if backup.exists():
        raise RuntimeError('Backup filename already exists: ' + str(backup))
    shutil.copy2(args.config, backup)
    atomic_write_text(args.config, repaired)
    print('Repositioned the known Auto XY include ABOVE SAVE_CONFIG.')
    print('No #*# saved values were removed or rewritten.')
    print('Backup:', backup)
    print('Restart Klipper while idle and verify all saved configuration values.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('REPAIR REFUSED:', exc, file=sys.stderr)
        sys.exit(2)
