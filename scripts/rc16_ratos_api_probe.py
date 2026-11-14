#!/usr/bin/env python3
"""Read-only RC16 RatOS / virtual-SD API compatibility probe.

Reports only code fingerprints and public API shape. Reads no printer config,
saved variables, G-code files or credentials. No file writes, subprocesses,
Moonraker actions, restarts, printer movement or heater commands.
"""
import ast
import hashlib
from pathlib import Path

EXTRAS = Path('/home/pi/klipper/klippy/extras')
MODULES = ('ratos.py', 'virtual_sdcard.py')
IMPORTANT = ('PROCESS_GCODE_FILE', 'SDCARD_PRINT_FILE',
             'SDCARD_PRINT_FILE_BASE', 'M23', 'M26', 'M24')
FUNCTIONS = ('cmd_SDCARD_PRINT_FILE', 'cmd_PROCESS_GCODE_FILE',
             'do_resume', 'cmd_M24', 'cmd_M23', 'cmd_M26',
             'process_gcode_file', 'register_command')


def inspect_source(source):
    tree = ast.parse(source)
    functions = set()
    commands = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.add(node.name)
        if isinstance(node, ast.Call):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value in IMPORTANT:
                        commands.add(arg.value)
    return {'functions': {name: name in functions for name in FUNCTIONS},
            'command_literals': {name: name in commands for name in IMPORTANT}}


def inspect_file(path):
    if not path.is_file():
        return {'status': 'NOT_FOUND'}
    try:
        raw = path.read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            return {'status': 'TOO_LARGE'}
        detail = inspect_source(raw.decode('utf-8'))
        detail.update({'status': 'PARSED',
                       'sha256': hashlib.sha256(raw).hexdigest()})
        return detail
    except (OSError, ValueError, SyntaxError, UnicodeError):
        return {'status': 'UNREADABLE_OR_UNPARSEABLE'}


def main():
    for name in MODULES:
        print('=== %s ===' % name)
        result = inspect_file(EXTRAS / name)
        print('status:', result['status'])
        if result['status'] == 'PARSED':
            print('source_sha256:', result['sha256'])
            for method, exists in sorted(result['functions'].items()):
                print('method_%s: %s' % (method, 'YES' if exists else 'NO'))
            for cmd, exists in sorted(result['command_literals'].items()):
                print('literal_%s: %s' % (cmd, 'YES' if exists else 'NO'))
    print('Read-only API probe complete. No installation or printer actions.')


if __name__ == '__main__':
    main()
