"""Narrow RatOS 2.1 Klipper extension lifecycle for AutoXY only.

No function runs on import. The RatOS CLI owns registry changes; this helper
checks the unrelated extension registry before and after each operation.
"""

from pathlib import Path
import os
import re
import subprocess

NAME = 'auto_xy_gate'
EXTRAS = 'klipper/klippy/extras/auto_xy_gate.py'


def other_links(home, registry):
    """Observe the files as well as registry rows for unrelated extensions.

    Some RatOS-managed Klipper extensions are kinematics modules, so check
    both locations; missing/non-symlink files are recorded without guessing.
    """
    result = {}
    for name in sorted(registry):
        if name == NAME:
            continue
        for directory in ('klipper/klippy/extras', 'klipper/klippy/kinematics'):
            path = home / directory / (name + '.py')
            if path.is_symlink():
                state = ['symlink', os.readlink(path)]
            elif path.exists():
                state = ['present', None]
            else:
                state = ['absent', None]
            result[directory + '/' + name + '.py'] = state
    return result


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def call(argv, runner=subprocess.run):
    process = runner(argv, capture_output=True, text=True, timeout=30, check=False)
    require(process.returncode == 0,
            'RatOS extension command failed: ' + ' '.join(argv) + '\n' +
            (process.stderr or process.stdout)[-1500:])
    return process.stdout


def registered(runner=subprocess.run):
    lines = call(['ratos', 'extensions', 'list', '--klipper', '--non-interactive'], runner)
    require(re.search(r'\bRegistered Klipper Extensions:', lines) is not None,
            'Unexpected RatOS extension list format')
    items = {}
    for name, path in re.findall(r'^(\S+) -> (\S+)\s*$', lines, flags=re.M):
        require(name not in items, 'Duplicate RatOS extension registration: ' + name)
        items[name] = path
    return items


def snapshot(home, runner=subprocess.run):
    home = Path(home).resolve()
    registry = registered(runner)
    link = home / EXTRAS
    require(not link.exists() or link.is_symlink(), 'AutoXY Klipper target is not a symlink')
    return {'registered': registry.get(NAME),
            'others': {k: v for k, v in registry.items() if k != NAME},
            'other_links': other_links(home, registry),
            'link': os.readlink(link) if link.is_symlink() else None}


def same_others(before, after):
    require(before['others'] == after['others'],
            'OTHER Klipper extensions changed. Stop and recover manually.')
    require(before['other_links'] == after['other_links'],
            'OTHER Klipper extension symlinks changed. Stop and recover manually.')


def register(home, source, runner=subprocess.run):
    home = Path(home).resolve()
    source = Path(source).resolve(strict=True)
    require(source.is_file(), 'AutoXY registration source missing')
    require(source == home / 'auto_xy/klipper/auto_xy_gate.py',
            'Refusing a noncanonical AutoXY controller path')
    before = snapshot(home, runner)
    require(before['registered'] is None and before['link'] is None,
            'AutoXY is already registered or linked; fresh install requires a clean stage')
    call(['ratos', 'extensions', 'register', 'klipper', '--error-if-exists',
          NAME, str(source)], runner)
    try:
        after = snapshot(home, runner)
        same_others(before, after)
        require(after['registered'] == str(source), 'RatOS registration target differs')
        if after['link'] is None:
            # RatOS versions may register without immediately symlinking.
            # Avoid `ratos extensions symlink klipper` because that potentially
            # relinks ALL registered extensions, including unrelated ones.
            (home / EXTRAS).symlink_to(str(source))
            after = snapshot(home, runner)
        require(after['link'] is not None and (home / EXTRAS).resolve() == source,
                'RatOS did not create the expected Klipper symlink')
        return after
    except BaseException:
        # Register succeeded but its validation failed. Do not leave an
        # orphaned registration behind during fresh-install rollback.
        current = snapshot(home, runner)
        if current['registered'] == str(source):
            call(['ratos', 'extensions', 'unregister', 'klipper',
                  '--error-if-not-exists', NAME], runner)
        current = snapshot(home, runner)
        if current['link'] == str(source):
            (home / EXTRAS).unlink()
        raise


def unregister(home, expected_registered, expected_link, runner=subprocess.run):
    """Remove only AutoXY registration; preserve others. Return old snapshot.

    `expected_registered` and `expected_link` are separately checked because
    the verified RC7 host has DIFFERENT paths for the two gate sources.
    """
    home = Path(home).resolve()
    before = snapshot(home, runner)
    require(before['registered'] == expected_registered,
            'AutoXY registration changed since preflight')
    require(before['link'] == expected_link, 'AutoXY symlink changed since preflight')
    call(['ratos', 'extensions', 'unregister', 'klipper', '--error-if-not-exists',
          NAME], runner)
    after = snapshot(home, runner)
    same_others(before, after)
    require(after['registered'] is None, 'RatOS still registers AutoXY')
    if after['link'] is not None:
        # Some RatOS configurations leave a manually created stale symlink.
        # Remove only the *verified* AutoXY link, never an unrecognised target.
        require(after['link'] == expected_link,
                'Unexpected AutoXY symlink after RatOS unregister')
        (home / EXTRAS).unlink()
        after = snapshot(home, runner)
    require(after['link'] is None, 'AutoXY link remains after unregister')
    return before


def verify_absent(home, runner=subprocess.run):
    current = snapshot(home, runner)
    require(current['registered'] is None and current['link'] is None,
            'AutoXY still registered or symlinked')
    return current


def restore_snapshot(home, before, runner=subprocess.run):
    """Restore a previously verified AutoXY-only registration and symlink.

    Only suitable before any other new AutoXY installation or user edits.
    """
    home = Path(home).resolve()
    current = snapshot(home, runner)
    same_others(before, current)
    require(current['registered'] is None and current['link'] is None,
            'Refusing restoration over an existing AutoXY registration/link')
    original = Path(before['registered'])
    require(original.is_file() and str(original).startswith(str(home / 'auto_xy') + '/'),
            'Recorded AutoXY registration source not available')
    call(['ratos', 'extensions', 'register', 'klipper', '--error-if-exists',
          NAME, str(original)], runner)
    current = snapshot(home, runner)
    same_others(before, current)
    require(current['registered'] == str(original), 'Could not restore registered source')
    link = home / EXTRAS
    if current['link'] != before['link']:
        require(current['link'] in (None, str(original)),
                'Unexpected new AutoXY link during restoration')
        if link.is_symlink():
            link.unlink()
        link.symlink_to(before['link'])
    restored = snapshot(home, runner)
    require(restored['registered'] == before['registered'] and
            restored['link'] == before['link'], 'Restored AutoXY binding differs')
    same_others(before, restored)
    return restored
