#!/usr/bin/env python3
"""Conservative local cleanup of closed Auto XY camera runs.

No printer API, G-code, saved offsets, or Klipper config modifications.
Only timestamp-named run folders directly below the configured results root
are eligible. Unknown/corrupt/in-progress reports and unexpected files remain.
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

RUN_RE = re.compile(r'^20\d{6}-\d{6}_(?:print|idle)(?:_\d{2})?$')
IMAGE_RE = re.compile(r'^\d{3}_[\w.+-]+\.(?:jpg|jpeg|png|webp)$', re.I)
SUCCESS = {'CALIBRATED_AND_VAOC_EXITED', 'RECOVERED_INLINE'}
MAX_SIZE = 4 * 1024 * 1024


def retention_plan(results, now=None, success_days=7, failed_days=30, report_days=90):
    """Return (actions, skipped): actions are (kind, Path, reason)."""
    if now is None:
        now = dt.datetime.now()
    results = Path(results)
    if results.is_symlink():
        raise ValueError('Results root is a symbolic link; refuse automatic deletion')
    if not results.exists():
        return [], ['Results directory does not exist']
    if not results.is_dir():
        raise ValueError('Results path is not a directory')
    if not (1 <= success_days <= failed_days <= report_days <= 3650):
        raise ValueError('Retention requires 1 <= success <= failed <= report <= 3650 days')
    actions, skipped = [], []
    for run in sorted(results.iterdir()):
        if run.is_symlink() or not run.is_dir() or not RUN_RE.fullmatch(run.name):
            skipped.append(f'{run.name}: not a known Auto XY run folder')
            continue
        path = run / 'report.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SIZE:
            skipped.append(f'{run.name}: missing/untrusted report.json')
            continue
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(report, dict):
                raise ValueError('not a report object')
            status = report['status']
            if not isinstance(status, str) or status in ('STARTING', 'CALIBRATED'):
                raise ValueError('incomplete run')
            if not (status in SUCCESS or status.startswith('FAILED') or status.startswith('INLINE_')):
                # Other completed failure statuses are preserved until classified.
                raise ValueError('unrecognized completion status')
            started = dt.datetime.strptime(run.name[:15], '%Y%m%d-%H%M%S')
            if started > now + dt.timedelta(minutes=5):
                raise ValueError('future date')
            age_days = (now - started).total_seconds() / 86400.0
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, OSError) as exc:
            skipped.append(f'{run.name}: incomplete or unrecognised report ({exc})')
            continue
        contents = list(run.iterdir())
        # Do not manipulate symlinks, subdirectories or unknown files.
        known = lambda p: (p.name == 'report.json' or IMAGE_RE.fullmatch(p.name)) and p.is_file() and not p.is_symlink()
        if age_days >= report_days:
            if all(known(p) for p in contents):
                actions.append(('run', run, f'{age_days:.1f} days old; report retention {report_days} days'))
            else:
                skipped.append(f'{run.name}: contains extra files/symlinks; preserving directory')
        elif age_days >= (success_days if status in SUCCESS else failed_days):
            for p in contents:
                if IMAGE_RE.fullmatch(p.name) and p.is_file() and not p.is_symlink():
                    actions.append(('image', p, f'{status}, {age_days:.1f} days old'))
    return actions, skipped


def execute(actions):
    deleted = 0
    for kind, path, _ in actions:
        if kind == 'image':
            if not path.is_symlink() and path.is_file():
                path.unlink(); deleted += 1
        else:
            # Reconfirm folder contents immediately before unlinking.
            if path.is_symlink() or not path.is_dir():
                continue
            items = list(path.iterdir())
            if any(p.is_symlink() or not p.is_file() or
                   (p.name != 'report.json' and not IMAGE_RE.fullmatch(p.name)) for p in items):
                continue
            for p in items:
                p.unlink()
            path.rmdir(); deleted += 1
    return deleted


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--check', action='store_true', help='Preview actions; no deletion')
    g.add_argument('--run', action='store_true', help='Apply selected deletions')
    ap.add_argument('--results', type=Path, default=Path('/home/pi/auto_xy/print_gate_runs'))
    ap.add_argument('--success-days', type=int, default=7)
    ap.add_argument('--failed-days', type=int, default=30)
    ap.add_argument('--report-days', type=int, default=90)
    args = ap.parse_args(argv)
    actions, skipped = retention_plan(args.results, success_days=args.success_days,
                                     failed_days=args.failed_days, report_days=args.report_days)
    print(f'Auto XY cleanup: {len(actions)} eligible actions, {len(skipped)} preserved/ignored')
    for kind, path, reason in actions:
        print(f'  {kind.upper()}: {path} ({reason})')
    for reason in skipped[:20]:
        print(f'  PRESERVED: {reason}')
    if args.run:
        print('Cleanup completed:', execute(actions), 'items removed')
    else:
        print('DRY RUN ONLY: nothing deleted')
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('CLEANUP REFUSED:', exc, file=sys.stderr)
        sys.exit(2)
