#!/usr/bin/env python3
"""Retire exact identified Trial04 print interceptor, preserving active RC16.

The Trial04 module from the printer was identified by sha256 and its symlink.
It intercepts PROCESS_GCODE_FILE for every print, including COPY/MIRROR.
This transactional migration comments ONLY its single printer.cfg include.
It never deletes software, G-code, user macros, offsets or references.

Requires an idle/unloaded, fault-free RatOS+RC16 installation, prevalidated
sudo access, verified backup, one supervised Klipper restart, and post-restart
evidence that RC16's SD/resume protection is armed. Reverts on failure.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

HOME = Path('/home/pi')
PRINTER_CFG = 'printer_data/config/printer.cfg'
LEGACY_CFG = 'printer_data/config/auto_xy_local_recovery.cfg'
LEGACY_SOURCE = 'auto_xy/klipper/auto_xy_local_recovery.py'
LEGACY_EXTENSION = 'klipper/klippy/extras/auto_xy_local_recovery.py'
LEGACY_SHA256 = '12ddf4100b6b61cbb71f3cc1b26d0d87e2134a8c9cbdb0385f83aba50e11c03f'
INCLUDE = re.compile(r'^[ \t]*\[include[ \t]+auto_xy_local_recovery\.cfg\][ \t]*$', re.I)
SECTION = re.compile(r'^[ \t]*\[auto_xy_local_recovery\][ \t]*$', re.I)
STATUS_URL = (
    'http://127.0.0.1:7125/printer/objects/query?'
    'print_stats&virtual_sdcard&gcode_macro%20_VAOC&'
    'auto_xy_ratos_handoff_rc16&save_variables&configfile'
)
INFO_URL = 'http://127.0.0.1:7125/printer/info'


class Refuse(RuntimeError):
    pass


def require(test, reason):
    if not test:
        raise Refuse(reason)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def query(url):
    with urlopen(url, timeout=8) as response:
        payload = json.load(response)
    require('error' not in payload, 'Moonraker query returned an error')
    return payload['result']


def status():
    return query(STATUS_URL)['status']


def assert_ready():
    info = query(INFO_URL)
    require(info.get('state') == 'ready', 'Klipper is not Ready')


def is_on(value):
    return str(value).strip().lower() in ('1', '1.0', 'true')


def require_idle(s):
    stats = s.get('print_stats')
    sd = s.get('virtual_sdcard')
    vaoc = s.get('gcode_macro _VAOC')
    handoff = s.get('auto_xy_ratos_handoff_rc16')
    saved = s.get('save_variables')
    vars_ = saved.get('variables') if isinstance(saved, dict) else None
    require(
        isinstance(stats, dict)
        and stats.get('state') in ('standby', 'cancelled', 'complete')
        and isinstance(sd, dict)
        and sd.get('is_active') is False
        and sd.get('file_path') in (None, '')
        and sd.get('filename') in (None, '')
        and isinstance(vaoc, dict) and vaoc.get('is_started') is False
        and isinstance(handoff, dict)
        and handoff.get('enabled') is True
        and handoff.get('armed') is True
        and handoff.get('busy') is False
        and isinstance(vars_, dict)
        and all(not is_on(vars_.get(flag, 0)) for flag in (
            'auto_xy_v2_fault', 'auto_xy_v2_in_progress',
            'auto_xy_v2_retry_available')),
        'Printer must be Ready, idle/unloaded, VAOC closed, RC16 armed, '
        'with no unresolved fault, retry or recovery session'
    )


def require_rc16_post(s):
    require_idle(s)
    handoff = s['auto_xy_ratos_handoff_rc16']
    require(handoff.get('sd_api_guarded') is True
            and handoff.get('resume_api_guarded') is True,
            'RC16 virtual SD and resume API guards not both armed')
    config = s.get('configfile', {})
    settings = config.get('settings') if isinstance(config, dict) else None
    require(isinstance(settings, dict)
            and 'auto_xy_local_recovery' not in settings,
            'Legacy Trial04 still loaded in Klipper active configuration')


def regular(path):
    require(path.is_file() and not path.is_symlink()
            and not path.parent.is_symlink(), 'Unsafe or missing regular file: '+str(path))


def inspect(home):
    home = Path(home)
    cfg, legacy_cfg = home/PRINTER_CFG, home/LEGACY_CFG
    source, extension = home/LEGACY_SOURCE, home/LEGACY_EXTENSION
    for path in (cfg, legacy_cfg, source):
        regular(path)
    require(sha256(source.read_bytes()) == LEGACY_SHA256,
            'Legacy interceptor differs from reviewed Trial04 binary; refuse')
    require(extension.is_symlink() and extension.resolve() == source.resolve(),
            'Legacy Klipper extension is not the expected source symlink')
    text = cfg.read_bytes()
    try:
        decoded = text.decode('utf-8')
    except UnicodeError as e:
        raise Refuse('printer.cfg is not UTF-8') from e
    lines = decoded.splitlines(keepends=True)
    live = [i for i, line in enumerate(lines)
            if INCLUDE.fullmatch(line.rstrip('\r\n'))]
    commented = [i for i, line in enumerate(lines)
                 if re.fullmatch(r'[ \t]*#[ \t]*\[include[ \t]+auto_xy_local_recovery\.cfg\][ \t]*',
                                 line.rstrip('\r\n'), re.I)]
    require(len(live) + len(commented) == 1,
            'Require precisely one expected Trial04 include in printer.cfg')
    legacy_text = legacy_cfg.read_text(encoding='utf-8')
    require(sum(bool(SECTION.fullmatch(v.strip())) for v in legacy_text.splitlines()) == 1,
            'Trial04 configuration section missing/duplicated')
    # Do not accept an additional include file or a second loaded instance.
    if live:
        pos = live[0]
        lines[pos] = '# ' + lines[pos]
    replacement = ''.join(lines).encode('utf-8')
    return cfg, text, replacement, bool(live)


def checked(home, *, live=True):
    cfg, old, new, needs_change = inspect(home)
    if live:
        assert_ready()
        s = status()
        require_idle(s)
        if not needs_change:
            require_rc16_post(s)
    return cfg, old, new, needs_change


def replace(path, data, mode):
    fd, tmp = tempfile.mkstemp(prefix='.rc16-trial04-', dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def restart():
    subprocess.run(('sudo', 'systemctl', 'restart', 'klipper'),
                   check=True, timeout=120)


def verify_ready():
    import rc16_vision_refresh
    rc16_vision_refresh.ready()


def install(home, *, live=True, restart_service=restart,
            await_ready=verify_ready, read_status=status):
    cfg, old, updated, needs_change = checked(home, live=live)
    if not needs_change:
        return 'already-retired', None
    backups = Path(home)/'auto_xy/rollbacks'
    require(backups.is_dir() and not backups.is_symlink(),
            'Untrusted or missing rollback directory')
    backup = Path(tempfile.mkdtemp(prefix='rc16-retired-trial04-', dir=str(backups)))
    original = backup/'printer.cfg.original'
    original.write_bytes(old)
    os.chmod(original, 0o600)
    require(sha256(original.read_bytes()) == sha256(old),
            'Backup checksum mismatch')
    (backup/'receipt.json').write_text(json.dumps({
        'target':str(cfg), 'old_sha256':sha256(old),
        'new_sha256':sha256(updated),
        'trial04_source_sha256':LEGACY_SHA256,
        'original_backup':str(original)},indent=2),encoding='utf-8')
    changed = False
    mode = stat.S_IMODE(cfg.stat().st_mode)
    try:
        if live:
            assert_ready()
            require_idle(read_status())
        require(cfg.read_bytes() == old,
                'Configuration changed after preflight')
        replace(cfg, updated, mode)
        changed = True
        require(cfg.read_bytes() == updated, 'Configuration update verification failed')
        if live:
            restart_service()
            await_ready()
            require_rc16_post(read_status())
        return 'trial04-retired', backup
    except BaseException as exc:
        if not changed:
            raise Refuse('No config changed; refused: %s' % exc) from exc
        try:
            require(cfg.is_file() and not cfg.is_symlink()
                    and cfg.read_bytes() == updated,
                    'Config changed since install; automatic rollback unsafe')
            require(original.read_bytes() == old,
                    'Rollback snapshot modified')
            replace(cfg, old, mode)
            require(cfg.read_bytes() == old,
                    'Rollback did not restore printer.cfg')
            if live:
                restart_service()
                await_ready()
        except BaseException as rollback_exc:
            raise Refuse('INSTALL AND ROLLBACK FAILED, DO NOT PRINT. '
                         'Snapshot=%s; errors: %s / %s'
                         % (backup, exc, rollback_exc)) from rollback_exc
        raise Refuse('RC16 verification failed; previous printer.cfg restored '
                     'and Klipper reloaded when possible. Snapshot=%s; %s'
                     % (backup, exc)) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check','install'))
    args = parser.parse_args(argv)
    try:
        cfg, old, updated, needs_change = checked(HOME)
        print('RC16 TRIAL04 RETIREMENT PREFLIGHT PASS')
        print('printer.cfg:', cfg)
        print('Trial04 include:', 'active' if needs_change else 'already retired')
        print('Legacy source and symlink: exact reviewed Trial04')
        if args.action == 'check' or not needs_change:
            return 0
        import rc16_active_install
        rc16_active_install.authorize_restart()
        result, snapshot = install(HOME)
        print('Outcome:', result)
        print('Backup:', snapshot)
        return 0
    except (OSError, ValueError, KeyError, Refuse,
            subprocess.SubprocessError) as exc:
        print('RC16 TRIAL04 RETIREMENT REFUSED:', exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
