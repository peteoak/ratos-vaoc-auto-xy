#!/usr/bin/env python3
"""AutoXY RC16 retention: per-category age limits plus a SOFT global image quota.

Only closed/known runs in the configured AutoXY results root are eligible.
Unknown, corrupt, in-progress or symlink-containing runs are preserved. All
images, including raw and annotated camera frames, count against the combined
quota; only images belonging to verified closed runs may be deleted. A quota
warning NEVER blocks calibration or prevents new images being captured.
--run is the only destructive mode.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import time
from collections import defaultdict
from pathlib import Path

MIB = 1024 * 1024
DEFAULT_TOTAL_IMAGES_MIB = 512
DEFAULT_REFERENCE_ROOT = Path('/home/pi/auto_xy/nozzle_references')
ALL_IMAGE_EXTENSIONS = frozenset(('.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff'))
RUN_NAME = re.compile(r'^\d{8}-\d{6}_(print|idle)(?:_\d{2})?$')
STATES = {
    'CALIBRATED_AND_VAOC_EXITED': 'success_images',
    'FAILED_DO_NOT_PRINT': 'failed_images',
    'MEASURE_ONLY_VERIFIED_AND_VAOC_EXITED': 'diagnostic_images',
    'ENDURANCE_COMPLETE_AND_VAOC_EXITED': 'diagnostic_images',
}
DEFAULTS = {
    'success_images': 256,
    'failed_images': 512,
    'diagnostic_images': 512,
    'reports': 128,
    'exports': 128,
}


def _entry(path, category, run, status, date):
    stat = path.lstat()
    return {'path': path, 'category': category, 'run': run,
            'status': status, 'date': date, 'size': stat.st_size,
            'fingerprint': (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)}


def inventory(root):
    """Scan direct AutoXY-owned runs; never follow symlinks or stray paths."""
    files, runs, warnings = [], [], []
    if root.is_symlink() or not root.is_dir():
        return files, runs, ['Unsafe or unavailable results root']
    for folder in root.iterdir():
        if not RUN_NAME.fullmatch(folder.name) or folder.is_symlink() or not folder.is_dir():
            continue
        report = folder / 'report.json'
        if report.is_symlink() or not report.is_file():
            warnings.append('Unfinished or missing report: ' + folder.name)
            continue
        try:
            report_before = report.stat()
            data = json.loads(report.read_text())
            report_after = report.stat()
            def report_identity(st):
                return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            if report_identity(report_before) != report_identity(report_after):
                warnings.append('Changing report protected in ' + folder.name)
                continue
            status = data.get('status')
            if status not in STATES:
                warnings.append('Nonterminal/unknown status: ' + folder.name)
                continue
            # All direct children must be ordinary known-owned files. Otherwise
            # this folder's provenance is ambiguous and nothing is removed.
            children = list(folder.iterdir())
            if any(p.is_symlink() or not p.is_file() for p in children):
                warnings.append('Symlink/unknown entry in ' + folder.name)
                continue
            referenced = set()
            for row in data.get('captures', []) + data.get('capture_failures', []):
                if not isinstance(row, dict):
                    raise ValueError('Invalid capture entry')
                image = row.get('image')
                if not isinstance(image, str):
                    continue
                p = Path(image)
                # Existing AutoXY reports use absolute paths. A relative
                # filename is also accepted, but not a nested path.
                if p.is_absolute():
                    if p.parent != folder:
                        continue
                elif p.parent == Path('.'):
                    p = folder / p.name
                else:
                    continue
                if (p.suffix.lower() in ('.jpg', '.jpeg') and
                        re.fullmatch(r'\d{3}_.+_attempt_[12]\.jpe?g', p.name, re.I)):
                    referenced.add(p.name)
                    referenced.add('detected_' + p.name)  # worker creates annotated frame
            allowed_exports = {p.name for p in children if
                               p.name == 'endurance_images.csv' or
                               (p.name.startswith('endurance_') and p.suffix == '.svg')}
            allowed = referenced | allowed_exports | {'report.json'}
            extras = [p.name for p in children if p.name not in allowed]
            if extras:
                warnings.append('Unrecognised files protected in ' + folder.name)
                continue
            date = report.stat().st_mtime
            run_files = []
            for p in children:
                if p.name == 'report.json':
                    cat = 'reports'
                elif p.name in allowed_exports:
                    cat = 'exports'
                else:
                    cat = STATES[status]
                entry = _entry(p, cat, folder, status, date)
                entry['report_fingerprint'] = report_identity(report_after)
                run_files.append(entry)
            files.extend(run_files)
            runs.append({'folder': folder, 'report': report, 'status': status,
                         'date': date, 'entries': run_files})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            warnings.append('Unreadable/invalid run ' + folder.name + ': ' + str(exc))
    return files, runs, warnings


def all_image_usage(root):
    """Count ALL image bytes in the AutoXY results tree, even unknown or active.

    Never follow symlinks. Unknown image files count against the soft quota but
    cannot be deleted without a trusted terminal report and an owned name.
    This only runs in the external retention subprocess, not Klipper.
    """
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return 0, ['Unsafe or unavailable results root']
    used = 0
    warnings = []
    def scan_error(exc):
        warnings.append('Image inventory incomplete; size is a lower bound')
    for base, dirs, names in os.walk(str(root), topdown=True,
                                     followlinks=False, onerror=scan_error):
        dirs[:] = [d for d in dirs if not (Path(base)/d).is_symlink()]
        for name in names:
            p = Path(base)/name
            if p.suffix.lower() not in ALL_IMAGE_EXTENSIONS:
                continue
            try:
                info = p.lstat()
                if stat.S_ISREG(info.st_mode):
                    used += info.st_size
            except OSError:
                warnings.append('Image changed during inventory; accounting incomplete')
    return used, warnings


def plan(root, success_days=7, failed_days=30, report_days=90, now=None,
         diagnostic_days=7, exports_days=30, limits_mib=None,
         keep_latest_failures=2, max_total_images_mib=DEFAULT_TOTAL_IMAGES_MIB,
         reference_root=DEFAULT_REFERENCE_ROOT):
    """Return an oldest-first safe deletion plan and accounting summary.

    Age eligibility OR category byte cap triggers reclamation. A report is
    deleted only with *all* remaining owned artifacts in its run, preventing
    orphaned image files. Latest failure reports are protected from quota
    eviction (but not explicit retention expiry) for forensic review.
    """
    now = time.time() if now is None else now
    limits = DEFAULTS.copy()
    if limits_mib:
        limits.update(limits_mib)
    if not 1 <= success_days <= failed_days <= report_days:
        raise ValueError('Require success days <= failure days <= report days')
    if not 1 <= diagnostic_days <= report_days or not 1 <= exports_days <= report_days:
        raise ValueError('Diagnostic/export retention must be <= report retention')
    if any(not isinstance(v, int) or not 1 <= v <= 102400 for v in limits.values()):
        raise ValueError('Limits must be 1..102400 MiB')
    if type(max_total_images_mib) is not int or not 1 <= max_total_images_mib <= 102400:
        raise ValueError('Total image soft limit must be 1..102400 MiB')
    root = Path(root)
    reference_root = Path(reference_root) if reference_root is not None else None
    if reference_root is not None:
        results_abs = root.resolve(strict=False)
        references_abs = reference_root.resolve(strict=False)
        if (results_abs == references_abs or results_abs in references_abs.parents
                or references_abs in results_abs.parents):
            raise ValueError('Protected nozzle reference library overlaps disposable results')
    files, runs, warnings = inventory(root)
    total_image_bytes, inventory_warnings = all_image_usage(root)
    warnings.extend(inventory_warnings)
    protected_reference_bytes = 0
    if reference_root is not None and reference_root.exists():
        protected_reference_bytes, ref_warnings = all_image_usage(reference_root)
        warnings.extend('Protected references: ' + x for x in ref_warnings)
        # Reference images are included in storage accounting, but no reference
        # path is EVER inserted into deletion eligibility or returned to execute.
        total_image_bytes += protected_reference_bytes
    chosen = set()
    failed = sorted((r for r in runs if r['status'] == 'FAILED_DO_NOT_PRINT'),
                    key=lambda r: (r['date'], r['folder'].name), reverse=True)
    reserved = {r['folder'] for r in failed[:keep_latest_failures]}
    newest = max(runs, key=lambda r: (r['date'], r['folder'].name))['folder'] if runs else None
    age_limits = {'success_images': success_days, 'failed_images': failed_days,
                  'diagnostic_images': diagnostic_days, 'exports': exports_days}

    # First expire image/export bytes. Keep the newest run's evidence during
    # quota pressure but not past explicit time retention.
    for e in files:
        age = (now - e['date']) / 86400
        if e['category'] in age_limits and age >= age_limits[e['category']]:
            chosen.add(e['path'])

    # Expired reports are removed together with all known-owned run artifacts.
    for r in runs:
        if (now-r['date'])/86400 >= report_days:
            chosen.update(e['path'] for e in r['entries'])

    # For a report quota, remove entire oldest terminal runs to prevent losing
    # ownership tracking of the images that remain in the directory.
    budgets = {k: v * MIB for k, v in limits.items()}
    def used(cat):
        return sum(e['size'] for e in files if e['category'] == cat and e['path'] not in chosen)
    for r in sorted(runs, key=lambda x: (x['date'], x['folder'].name)):
        if used('reports') <= budgets['reports']:
            break
        if r['folder'] == newest or r['folder'] in reserved:
            continue
        chosen.update(e['path'] for e in r['entries'])

    # Each category has an independent cap: oldest eligible bytes go first.
    for category in ('success_images', 'failed_images', 'diagnostic_images', 'exports'):
        remaining = used(category)
        oldest = sorted((e for e in files if e['category'] == category and e['path'] not in chosen),
                        key=lambda e: (e['date'], e['run'].name, e['path'].name))
        for e in oldest:
            if remaining <= budgets[category]:
                break
            if e['run'] == newest or e['run'] in reserved:
                continue
            chosen.add(e['path'])
            remaining -= e['size']

    # Enforce a TOTAL soft image budget across all success, failure and
    # diagnostic images, including annotated frames. This is a cleanup
    # trigger, never a capture admission/stop rule. Unknown or active runs
    # count toward usage but are NEVER deleted just to reach the target.
    image_categories = frozenset(('success_images', 'failed_images',
                                   'diagnostic_images'))
    total_image_limit = max_total_images_mib * MIB
    def scheduled_image_bytes():
        return sum(e['size'] for e in files
                   if e['category'] in image_categories and e['path'] in chosen)
    remaining_total = total_image_bytes - scheduled_image_bytes()
    if remaining_total > total_image_limit:
        oldest_owned = sorted(
            (e for e in files
             if e['category'] in image_categories and e['path'] not in chosen),
            key=lambda e: (e['date'], e['run'].name, e['path'].name))
        # Prefer retaining the newest run and last two failures, but if quota
        # remains exceeded, remove those closed-run images too. Reports remain
        # for diagnostics. Never touch a currently open/nonterminal run.
        for protected_round in (False, True):
            for e in oldest_owned:
                if remaining_total <= total_image_limit:
                    break
                if e['path'] in chosen:
                    continue
                protected = (e['run'] == newest or e['run'] in reserved)
                if protected and not protected_round:
                    continue
                if not protected and protected_round:
                    continue
                chosen.add(e['path'])
                remaining_total -= e['size']

    selected = [e for e in files if e['path'] in chosen]
    selected.sort(key=lambda e: (e['category'] == 'reports', e['date'], e['run'].name, e['path'].name))
    tally = {}
    for category in DEFAULTS:
        total = sum(e['size'] for e in files if e['category'] == category)
        reclaim = sum(e['size'] for e in selected if e['category'] == category)
        tally[category] = {'bytes': total, 'reclaim_bytes': reclaim,
                           'after_bytes': total-reclaim, 'limit_bytes': budgets[category],
                           'files_to_remove': sum(e['category'] == category for e in selected)}
        if total-reclaim > budgets[category]:
            warnings.append('Protected/unknown data prevents achieving quota: ' + category)
    total_reclaim = scheduled_image_bytes()
    tally['all_images'] = {
        'bytes': total_image_bytes, 'reclaim_bytes': total_reclaim,
        'after_bytes': max(0, total_image_bytes-total_reclaim),
        'limit_bytes': total_image_limit,
        'protected_reference_bytes': protected_reference_bytes,
        'files_to_remove': sum(e['category'] in image_categories for e in selected)
    }
    if total_image_bytes-total_reclaim > total_image_limit:
        warnings.append('Total image soft quota remains exceeded: unknown or active-run images cannot be safely removed')
    return selected, tally, warnings


def execute(root, selected):
    """Recheck root, file identity, and known closed run before each unlink."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return 0
    removed = 0
    for e in selected:
        p = e['path']
        if p.parent.parent != root or p.parent.is_symlink() or p.is_symlink():
            continue
        try:
            # A new write, replacement or newly active run must never be deleted.
            report = p.parent/'report.json'
            if report.is_symlink() or not report.is_file():
                continue
            report_before = report.stat()
            report_id = (report_before.st_dev, report_before.st_ino,
                         report_before.st_size, report_before.st_mtime_ns,
                         report_before.st_ctime_ns)
            if report_id != e.get('report_fingerprint'):
                continue
            state = json.loads(report.read_text()).get('status')
            report_after = report.stat()
            if ((report_after.st_dev, report_after.st_ino, report_after.st_size,
                 report_after.st_mtime_ns, report_after.st_ctime_ns) != report_id
                    or state not in STATES or state != e['status']):
                continue
            st = p.lstat()
            if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) != e['fingerprint']:
                continue
            p.unlink()
            removed += 1
        except (OSError, ValueError, TypeError):
            continue
    return removed


REFERENCE_RETENTION_DAYS = 30
RETIRED_MARKER = re.compile(r'^retired_([a-f0-9]{32})\.json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', action='store_true', help='Delete eligible owned files (default: preview)')
    p.add_argument('--results', type=Path, required=True)
    p.add_argument('--reference-root', type=Path, default=DEFAULT_REFERENCE_ROOT,
                   help='Protected nozzle references; only replaced generations retire after 30 days')
    p.add_argument('--active-reference-profile', type=Path,
                   default=Path('/home/pi/auto_xy/camera_commission.json'))
    p.add_argument('--success-days', type=int, default=7)
    p.add_argument('--failed-days', type=int, default=30)
    p.add_argument('--diagnostic-days', type=int, default=7)
    p.add_argument('--report-days', type=int, default=90)
    p.add_argument('--exports-days', type=int, default=30)
    p.add_argument('--max-total-images-mib', type=int, default=DEFAULT_TOTAL_IMAGES_MIB,
                   help='Soft total image target in MiB; never blocks captures')
    for kind, mib in DEFAULTS.items():
        p.add_argument('--max-' + kind.replace('_', '-') + '-mib', type=int, default=mib)
    opts = p.parse_args()
    limits = {k: getattr(opts, 'max_' + k + '_mib') for k in DEFAULTS}
    try:
        chosen, stats, warnings = plan(opts.results, opts.success_days,
                                      opts.failed_days, opts.report_days,
                                      diagnostic_days=opts.diagnostic_days,
                                      exports_days=opts.exports_days,
                                      limits_mib=limits,
                                      max_total_images_mib=opts.max_total_images_mib,
                                      reference_root=opts.reference_root)
    except ValueError as exc:
        p.error(str(exc))
    print('AutoXY storage retention:', 'DELETE' if opts.run else 'PREVIEW')
    for name, s in stats.items():
        print('%s: %d file(s), %.2f MiB to reclaim; %.2f / %d MiB after' % (
            name, s['files_to_remove'], s['reclaim_bytes']/MIB,
            s['after_bytes']/MIB, limits.get(name, opts.max_total_images_mib)))
    for message in warnings:
        print('WARNING:', message)
    if opts.run:
        print('Removed files:', execute(opts.results, chosen))
    retired = cleanup_retired_references(opts.reference_root,
                                        opts.active_reference_profile,
                                        execute=opts.run)
    print('Retired reference generations:', retired['eligible_generations'],
          'removed:', retired['removed_generations'],
          'images:', retired['removed_images'])
    for warning in retired['warnings']:
        print('WARNING:', warning)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
)


def _reference_list(profile):
    """Get the signed current references; malformed/legacy profiles prevent cleanup."""
    if not isinstance(profile, dict) or profile.get('reference_schema') != 'rc16-full-frame-raw-jpeg-v1':
        raise ValueError('Current full-frame image-reference profile unavailable')
    result = {}
    for tool in ('tool0', 'tool1'):
        entry = profile.get(tool, {})
        images = entry.get('reference_images', [])
        if not isinstance(images, list) or len(images) != 3:
            raise ValueError('Current T0/T1 reference group is incomplete')
        for item in images:
            if not isinstance(item, dict) or not isinstance(item.get('path'), str):
                raise ValueError('Invalid current image reference')
            path = Path(item['path'])
            if not isinstance(item.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
                raise ValueError('Invalid current image hash')
            result[str(path)] = item['sha256']
    if len(result) != 6:
        raise ValueError('Current image references not unique')
    return result


def retired_reference_candidates(reference_root, profile_path, now=None,
                                 days=REFERENCE_RETENTION_DAYS):
    """Read-only archive selection. Never select active, unknown, or unverified images."""
    now = time.time() if now is None else float(now)
    if not 30 <= days <= 3650:
        raise ValueError('Retired image retention must be at least 30 days')
    root = Path(reference_root).resolve(strict=False)
    active = Path(profile_path)
    archive = root / 'archived'
    if (not root.is_dir() or root.is_symlink() or not archive.is_dir()
            or archive.is_symlink() or not active.is_file() or active.is_symlink()):
        return [], ['Reference library, active profile, or archive unavailable']
    try:
        current_bytes = active.read_bytes()
        current = json.loads(current_bytes)
        current_refs = _reference_list(current)
        current_digest = hashlib.sha256(current_bytes).hexdigest()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return [], ['Current profile invalid; no retired references deleted: ' + str(exc)]
    eligible, warnings = [], []
    for manifest in sorted(archive.glob('retired_*.json')):
        if not RETIRED_MARKER.fullmatch(manifest.name) or not manifest.is_file() or manifest.is_symlink():
            continue
        try:
            row = json.loads(manifest.read_text())
            if not isinstance(row, dict) or row.get('schema') != 1:
                raise ValueError('Unknown retirement schema')
            retired_at = row.get('retired_at_unix')
            if not isinstance(retired_at, (int, float)) or not 0 < retired_at <= now:
                raise ValueError('Invalid retirement timestamp')
            if now - retired_at < days * 86400:
                continue
            token = RETIRED_MARKER.fullmatch(manifest.name).group(1)
            backup = archive / ('camera_commission_%s.json' % token)
            if (backup.is_symlink() or not backup.is_file()
                    or row.get('backup_profile') != str(backup)):
                raise ValueError('Backup not recognised')
            raw = backup.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != row.get('prior_profile_sha256'):
                raise ValueError('Backup profile changed')
            old_profile = json.loads(raw)
            listed = row.get('images')
            if not isinstance(listed, list) or not isinstance(old_profile, dict):
                raise ValueError('Invalid retired image list')
            old_references = []
            for tool in ('tool0', 'tool1'):
                entries = old_profile.get(tool, {}).get('reference_images', [])
                if not isinstance(entries, list):
                    raise ValueError('Retired reference list corrupt')
                old_references.extend(entries)
            if listed != old_references:
                raise ValueError('Retirement manifest differs from exact previous profile')
            old_images = []
            for item in listed:
                if not isinstance(item, dict) or not isinstance(item.get('path'), str):
                    raise ValueError('Invalid retired image entry')
                path = Path(item['path'])
                if (path.is_symlink() or not path.is_file() or
                        path.parent.parent != root or path.parent.is_symlink() or
                        not re.fullmatch(r't[01]_[a-f0-9]{12}_[123]\.jpg', path.name)):
                    raise ValueError('Missing/unowned retired image')
                if str(path) in current_refs:
                    raise ValueError('Image still in active commissioning profile')
                image_hash = item.get('sha256')
                if not isinstance(image_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', image_hash):
                    raise ValueError('Invalid retired image hash')
                if hashlib.sha256(path.read_bytes()).hexdigest() != image_hash:
                    raise ValueError('Retired image changed')
                old_images.append((path, image_hash))
            if len({str(p) for p, _ in old_images}) != len(old_images):
                raise ValueError('Duplicate retired image references')
            eligible.append({'marker': manifest, 'backup': backup,
                             'images': old_images, 'profile': active,
                             'active_sha256': current_digest,
                             'backup_sha256': digest})
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            warnings.append('Retirement %s preserved: %s' % (manifest.name, exc))
    return eligible, warnings


def cleanup_retired_references(reference_root, profile_path, now=None,
                               execute=False, days=REFERENCE_RETENTION_DAYS):
    """Archive cleanup, separate from the ordinary per-print image eviction."""
    eligible, warnings = retired_reference_candidates(
        reference_root, profile_path, now=now, days=days)
    if not execute:
        return {'eligible_generations': len(eligible), 'removed_generations': 0,
                'removed_images': 0, 'warnings': warnings}
    removed_generations = 0
    removed_images = 0
    for item in eligible:
        try:
            # Re-evaluate eligibility and active references immediately before
            # each generation. A new commission can never make an old selection safe.
            fresh, _ = retired_reference_candidates(
                reference_root, profile_path, now=now, days=days)
            if not any(x['marker'] == item['marker'] and
                       x['active_sha256'] == item['active_sha256'] and
                       x['backup_sha256'] == item['backup_sha256'] for x in fresh):
                continue
            # A partial prior cleanup must fail closed: its marker survives,
            # allowing manual recovery instead of deleting unidentified files.
            for path, digest in item['images']:
                if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError('Retired image changed after planning')
                if hashlib.sha256(Path(profile_path).read_bytes()).hexdigest() != item['active_sha256']:
                    raise ValueError('Active profile changed during cleanup')
                path.unlink()
                removed_images += 1
            if hashlib.sha256(Path(profile_path).read_bytes()).hexdigest() != item['active_sha256']:
                raise ValueError('Active profile changed during cleanup')
            item['backup'].unlink()
            item['marker'].unlink()
            removed_generations += 1
        except (OSError, ValueError) as exc:
            warnings.append('Retired reference cleanup paused: ' + str(exc))
    return {'eligible_generations': len(eligible),
            'removed_generations': removed_generations,
            'removed_images': removed_images, 'warnings': warnings}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', action='store_true', help='Delete eligible owned files (default: preview)')
    p.add_argument('--results', type=Path, required=True)
    p.add_argument('--reference-root', type=Path, default=DEFAULT_REFERENCE_ROOT,
                   help='Protected nozzle reference library: count only, NEVER delete')
    p.add_argument('--success-days', type=int, default=7)
    p.add_argument('--failed-days', type=int, default=30)
    p.add_argument('--diagnostic-days', type=int, default=7)
    p.add_argument('--report-days', type=int, default=90)
    p.add_argument('--exports-days', type=int, default=30)
    p.add_argument('--max-total-images-mib', type=int, default=DEFAULT_TOTAL_IMAGES_MIB,
                   help='Soft total image target in MiB; never blocks captures')
    for kind, mib in DEFAULTS.items():
        p.add_argument('--max-' + kind.replace('_', '-') + '-mib', type=int, default=mib)
    opts = p.parse_args()
    limits = {k: getattr(opts, 'max_' + k + '_mib') for k in DEFAULTS}
    try:
        chosen, stats, warnings = plan(opts.results, opts.success_days,
                                      opts.failed_days, opts.report_days,
                                      diagnostic_days=opts.diagnostic_days,
                                      exports_days=opts.exports_days,
                                      limits_mib=limits,
                                      max_total_images_mib=opts.max_total_images_mib,
                                      reference_root=opts.reference_root)
    except ValueError as exc:
        p.error(str(exc))
    print('AutoXY storage retention:', 'DELETE' if opts.run else 'PREVIEW')
    for name, s in stats.items():
        print('%s: %d file(s), %.2f MiB to reclaim; %.2f / %d MiB after' % (
            name, s['files_to_remove'], s['reclaim_bytes']/MIB,
            s['after_bytes']/MIB, limits.get(name, opts.max_total_images_mib)))
    for message in warnings:
        print('WARNING:', message)
    if opts.run:
        print('Removed files:', execute(opts.results, chosen))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
