#!/usr/bin/env python3
"""Recovery: disable Stage6 without damaging Klipper's SAVE_CONFIG block.

Run only while the printer is idle. Keeps RatOS saved XY offsets untouched.
If an earlier installer misplaced an include BELOW SAVE_CONFIG, first run
`repair_save_config.py --check` / `--repair` as described in docs.
"""
from pathlib import Path
import shutil
import sys
import time
from printer_cfg_editor import remove_include, atomic_write_text


def main():
    cfg = Path('/home/pi/printer_data/config/printer.cfg')
    text = cfg.read_text()
    legacy = cfg.parent / 'auto_xy_control.cfg'
    updated = remove_include(text, restore_legacy=legacy.exists())
    if updated == text:
        print('Stage6 include already absent; no change.')
        return
    backup = cfg.with_name('printer.cfg.pre_stage6_disable_' + time.strftime('%Y%m%d_%H%M%S'))
    shutil.copy2(cfg, backup)
    atomic_write_text(cfg, updated)
    print('Stage6 include removed or replaced with old include safely ABOVE SAVE_CONFIG.')
    print('RatOS saved tool offsets were NOT changed.')
    print('Backup:', backup)
    print('Restart Klipper while idle and verify saved offsets / compensation before printing.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('RECOVERY ERROR:', exc, file=sys.stderr)
        sys.exit(2)
