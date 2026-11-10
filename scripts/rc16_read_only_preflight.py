#!/usr/bin/env python3
"""AutoXY RC16 read-only printer preflight.

Prints only sanitized compatibility categories, no user config, offsets,
credentials, camera URLs, network addresses or serial numbers. No file writes,
downloads, heater commands, G-code commands, printer restarts, or motion.
Run with python3 -B to avoid creating Python bytecode caches.
"""
import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
from pathlib import Path
from urllib.request import urlopen

HOME = Path('/home/pi')
RC15_GATE_SHA256 = '0a865df007e01e3687f318c300e037c3ca05f6bbdefa13fba99edcb2fdea9df3'
MOONRAKER_QUERY = ('http://127.0.0.1:7125/printer/objects/query?'
                   'print_stats&virtual_sdcard&save_variables&idle_timeout&'
                   'heater_bed&extruder&extruder1&gcode_macro%20_VAOC&configfile')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def file_summary(home):
    printer = home / 'printer_data/config/printer.cfg'
    settings = home / 'printer_data/config/auto_xy.cfg'
    gate = home / 'auto_xy/klipper/auto_xy_gate.py'
    link = home / 'klipper/klippy/extras/auto_xy_gate.py'
    result = {'rc15_files': 'UNKNOWN', 'rc15_version': 'UNKNOWN',
              'protected_print_enabled': 'UNKNOWN',
              'rc16_include': 'UNKNOWN'}
    if any(not p.is_file() or p.is_symlink()
           for p in (printer, settings, gate)):
        result['rc15_files'] = 'MISSING_OR_UNSAFE'
        return result
    try:
        config = printer.read_text(encoding='utf-8')
        user = settings.read_text(encoding='utf-8')
        includes = re.findall(r'(?mi)^\s*\[include\s+([^\]\r\n]+)\]\s*$', config)
        result['rc16_include'] = ('PRESENT' if any(
            s.strip().lower() == 'auto_xy_rc16.cfg' for s in includes) else 'ABSENT')
        count = sum(s.strip().lower() == 'auto_xy.cfg' for s in includes)
        result['rc15_files'] = 'EXPECTED_LAYOUT' if (
            count == 1 and link.is_symlink() and link.resolve() == gate
        ) else 'UNEXPECTED_LAYOUT'
        result['rc15_version'] = ('EXACT_RC15_DEV6' if
                                  sha256(gate) == RC15_GATE_SHA256
                                  else 'OTHER_OR_MODIFIED')
        match = re.search(r'(?mi)^\s*protected_print_enabled:\s*(True|False)\s*$', user)
        if match:
            result['protected_print_enabled'] = match.group(1).upper()
    except (OSError, UnicodeError, ValueError):
        result['rc15_files'] = 'UNREADABLE'
    return result


def runtime_summary(url=MOONRAKER_QUERY):
    with urlopen(url, timeout=8) as response:
        objects = json.load(response)['result']['status']
    sd = objects.get('virtual_sdcard', {})
    state = objects.get('print_stats', {}).get('state', 'UNKNOWN')
    vaoc = objects.get('gcode_macro _VAOC', {}).get('is_started')
    heaters = [objects.get(k, {}) for k in ('heater_bed', 'extruder', 'extruder1')]
    # Require a positive Klipper idle_timeout state; unknown states fail closed.
    idle_state = objects.get('idle_timeout', {}).get('state')
    idle = (
        idle_state == 'Idle'
        and state in ('standby', 'complete', 'cancelled', 'error')
        and sd.get('is_active') is False
        and sd.get('file_path') in (None, '')
        and vaoc is False
        and all(float(h.get('target', -1)) == 0 and
                float(h.get('temperature', 100)) < 50 for h in heaters)
    )
    vars_ = objects.get('save_variables', {}).get('variables', {})
    keys = ('idex_xoffset', 'idex_yoffset', 'idex_zoffset')
    present = all(k in vars_ for k in keys)
    # Never display the offset values themselves.
    return {'moonraker': 'RESPONDING', 'print_state': str(state),
            'idle_and_cool': 'YES' if idle else 'NO',
            'saved_ratos_offsets': 'PRESENT' if present else 'MISSING',
            'native_vaoc_active': 'YES' if vaoc else ('NO' if vaoc is False else 'UNKNOWN')}


def vision_summary(home):
    python = home / 'auto-xy-venv/bin/python'
    if not python.is_file() or not os.access(python, os.X_OK):
        return 'NOT_FOUND'
    try:
        call = subprocess.run(
            [str(python), '-B', '-c',
             'import numpy,cv2; print(numpy.__version__ + "/" + cv2.__version__)'],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        if call.returncode == 0 and re.fullmatch(r'[\w.]+/[\w.]+\s*', call.stdout):
            return call.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return 'IMPORT_FAILED'


def inspect(home=HOME, use_network=True):
    home = Path(home).absolute()
    report = file_summary(home)
    report['host_architecture'] = platform.machine()
    report['python_version'] = platform.python_version()
    report['vision_numpy_opencv'] = vision_summary(home)
    if use_network:
        report['vaoc_camera_device'] = ('PRESENT' if
            Path('/dev/RatOS/rr-vaoc-camera').exists() else 'NOT_CONFIRMED')
        try:
            report.update(runtime_summary())
        except (OSError, ValueError, KeyError, TypeError):
            report['moonraker'] = 'UNAVAILABLE_OR_INVALID'
            report['idle_and_cool'] = 'UNKNOWN'
    else:
        report['moonraker'] = 'OFFLINE_SIMULATION'
        report['idle_and_cool'] = 'UNKNOWN'
    # Output is data-gathering only, NEVER a live installation authorisation.
    for key in sorted(report):
        print('%s: %s' % (key, report[key]))
    print('RC16 read-only preflight complete; no installation was attempted.')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=HOME,
                        help=argparse.SUPPRESS)
    parser.add_argument('--offline-test', action='store_true',
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.offline_test and args.home != HOME:
        parser.error('Production preflight supports only /home/pi')
    inspect(args.home, use_network=not args.offline_test)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
