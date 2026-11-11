#!/usr/bin/env python3
"""Remove retired nozzle reference generations only 30+ days after replacement.

Separate from print-run image cleanup. Active references and unknown files
are never eligible. Every retired generation must have a valid manifest and
an exact archived profile. Revalidation happens before every deletion.
"""
import argparse
import hashlib
import json
import math
import re
import time
from pathlib import Path

RETAIN_DAYS = 30
MANIFEST_PATTERN = re.compile(r'^retired_([a-f0-9]{32})[.]json$')
FRAME_PATTERN = re.compile(r'^t[01]_[a-f0-9]{12}_[123][.]jpg$')
IMAGE_SCHEMA = 'rc16-full-frame-raw-jpeg-v1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def references(profile, strict):
    if not isinstance(profile, dict):
        raise ValueError('Profile is not a JSON object')
    if strict and profile.get('reference_schema') != IMAGE_SCHEMA:
        raise ValueError('Current profile lacks full-frame image references')
    images = []
    for tool in ('tool0', 'tool1'):
        group = profile.get(tool, {})
        if not isinstance(group, dict):
            raise ValueError('Missing profile tool entry')
        refs = group.get('reference_images', [])
        if not isinstance(refs, list) or (strict and len(refs) != 3):
            raise ValueError('Incomplete nozzle image references')
        for row in refs:
            if (not isinstance(row, dict) or not isinstance(row.get('path'), str)
                    or not isinstance(row.get('sha256'), str)
                    or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])):
                raise ValueError('Malformed reference image entry')
            images.append(row)
    if strict and (len(images) != 6 or
                   len({row['path'] for row in images}) != 6):
        raise ValueError('Six unique current reference images required')
    return images


def plan(reference_root, active_profile, now=None, days=RETAIN_DAYS):
    now = time.time() if now is None else float(now)
    if not (math.isfinite(now) and 30 <= days <= 3650):
        raise ValueError('Reference retention must be >=30 days')
    root = Path(reference_root)
    archive = root / 'archived'
    active = Path(active_profile)
    if (root.is_symlink() or not root.is_dir() or archive.is_symlink()
            or not archive.is_dir() or active.is_symlink() or not active.is_file()):
        return [], ['Reference library/archive/current profile unavailable: preserving all']
    root = root.resolve(strict=True)
    archive = root / 'archived'
    try:
        active_data = active.read_bytes()
        active_hash = hashlib.sha256(active_data).hexdigest()
        active_refs = {row['path'] for row in references(json.loads(active_data), True)}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [], ['Invalid current reference profile: ' + str(exc)]
    selected, warnings = [], []
    for manifest in sorted(archive.iterdir()):
        match = MANIFEST_PATTERN.fullmatch(manifest.name)
        if not match or manifest.is_symlink() or not manifest.is_file():
            continue
        try:
            row = json.loads(manifest.read_text())
            if not isinstance(row, dict) or row.get('schema') != 1:
                raise ValueError('Unknown retirement schema')
            retired_at = row.get('retired_at_unix')
            if (not isinstance(retired_at, (int, float)) or
                    not math.isfinite(retired_at) or retired_at <= 0 or retired_at > now):
                raise ValueError('Invalid retirement timestamp')
            if now-retired_at < days*86400:
                continue
            backup = archive / ('camera_commission_' + match.group(1) + '.json')
            if (backup.is_symlink() or not backup.is_file() or
                    row.get('backup_profile') != str(backup)):
                raise ValueError('Unknown backup profile')
            backup_bytes = backup.read_bytes()
            backup_hash = hashlib.sha256(backup_bytes).hexdigest()
            if backup_hash != row.get('prior_profile_sha256'):
                raise ValueError('Archive hash mismatch')
            old_refs = references(json.loads(backup_bytes), False)
            if row.get('images') != old_refs:
                raise ValueError('Retirement manifest does not match previous profile')
            images = []
            for entry in old_refs:
                path = Path(entry['path'])
                if (path.is_symlink() or path.parent.parent != root or
                        path.parent.is_symlink() or
                        not FRAME_PATTERN.fullmatch(path.name)):
                    raise ValueError('Unrecognised retired reference image')
                if entry['path'] in active_refs:
                    raise ValueError('Still referenced by active profile')
                # A previous deletion may have stopped halfway through this
                # generation. Already-missing images need no further unlink.
                # Never relax path ownership, active-reference or hash checks.
                if path.exists():
                    if not path.is_file() or digest(path) != entry['sha256']:
                        raise ValueError('Retired reference has changed')
                    images.append((path, entry['sha256']))
            if len({str(p) for p, _ in images}) != len(images):
                raise ValueError('Duplicated retired reference')
            selected.append({'manifest': manifest, 'backup': backup,
                             'images': images, 'active_hash': active_hash,
                             'backup_hash': backup_hash})
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            warnings.append('%s protected: %s' % (manifest.name, exc))
    return selected, warnings


def clean(reference_root, active_profile, now=None, days=RETAIN_DAYS, execute=False):
    candidates, warnings = plan(reference_root, active_profile, now, days)
    removed_generations = 0
    removed_images = 0
    if execute:
        for candidate in candidates:
            try:
                # Fail closed when active profile is changed/recommissioned
                # or any owned image / backup is no longer what was validated.
                fresh, _ = plan(reference_root, active_profile, now, days)
                if not any(x['manifest'] == candidate['manifest'] and
                           x['active_hash'] == candidate['active_hash'] and
                           x['backup_hash'] == candidate['backup_hash']
                           for x in fresh):
                    continue
                for path, expected in candidate['images']:
                    if (digest(active_profile) != candidate['active_hash'] or
                            path.is_symlink() or digest(path) != expected):
                        raise ValueError('Reference changed during cleanup')
                    path.unlink()
                    removed_images += 1
                if digest(active_profile) != candidate['active_hash']:
                    raise ValueError('Active references changed during cleanup')
                candidate['backup'].unlink()
                candidate['manifest'].unlink()
                removed_generations += 1
            except (OSError, ValueError) as exc:
                warnings.append('Retired reference cleanup stopped: ' + str(exc))
    return {'eligible_generations': len(candidates),
            'removed_generations': removed_generations,
            'removed_images': removed_images, 'warnings': warnings}


SESSION_PATTERN = re.compile(r'^session-[a-zA-Z0-9_-]{8,16}
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--reference-root', type=Path, required=True)
    cli.add_argument('--active-profile', type=Path, required=True)
    cli.add_argument('--run', action='store_true')
    args = cli.parse_args()
    result = clean(args.reference_root, args.active_profile, execute=args.run)
    abandoned = clean_abandoned_sessions(
        args.reference_root, args.active_profile, execute=args.run)
    result['abandoned_capture_sessions'] = abandoned
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
)


def abandoned_sessions(reference_root, active_profile, now=None, days=30):
    """Read-only candidates for old, entirely unreferenced Mainsail captures.

    Conservative by design: if any active/archived profile is unreadable,
    preserve ALL sessions. No current or historical reference is eligible.
    """
    now = time.time() if now is None else float(now)
    if not math.isfinite(now) or days < 30:
        raise ValueError('Unreferenced capture retention is at least 30 days')
    root = Path(reference_root)
    current = Path(active_profile)
    archive = root / 'archived'
    if (root.is_symlink() or not root.is_dir() or archive.is_symlink()
            or not archive.is_dir() or current.is_symlink()
            or not current.is_file()):
        return [], ['Reference library/profile missing; abandoned sessions preserved']
    root = root.resolve(strict=True)
    try:
        active_bytes = current.read_bytes()
        active_sha = hashlib.sha256(active_bytes).hexdigest()
        protected = {r['path'] for r in references(json.loads(active_bytes), True)}
        for backup in archive.iterdir():
            if backup.is_symlink() or not backup.is_file():
                return [], ['Unknown archived entry; abandoned sessions preserved']
            if backup.name.startswith('camera_commission_') and backup.suffix == '.json':
                protected.update(r['path'] for r in references(
                    json.loads(backup.read_text()), False))
            elif backup.name.startswith('retired_') and backup.suffix == '.json':
                manifest = json.loads(backup.read_text())
                if not isinstance(manifest, dict) or not isinstance(manifest.get('images'), list):
                    raise ValueError('Unknown retirement marker')
                for entry in manifest['images']:
                    if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
                        raise ValueError('Bad retirement image reference')
                    protected.add(entry['path'])
            else:
                return [], ['Unknown archived file; abandoned sessions preserved']
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return [], ['Cannot verify all references; abandoned sessions preserved: ' + str(exc)]
    selected = []
    for folder in sorted(root.iterdir()):
        if not SESSION_PATTERN.fullmatch(folder.name) or folder.is_symlink() or not folder.is_dir():
            continue
        try:
            st = folder.stat()
            if now-st.st_mtime < days*86400 or now < st.st_mtime:
                continue
            files = list(folder.iterdir())
            if any(p.is_symlink() or not p.is_file() or
                   not FRAME_PATTERN.fullmatch(p.name) or
                   str(p) in protected for p in files):
                continue
            hashes = [(p, digest(p)) for p in files]
            selected.append({'folder': folder, 'files': hashes,
                             'active_sha': active_sha, 'mtime_ns': st.st_mtime_ns})
        except (OSError, ValueError):
            continue
    return selected, []


def clean_abandoned_sessions(reference_root, active_profile, now=None,
                             days=30, execute=False):
    items, warnings = abandoned_sessions(reference_root, active_profile, now, days)
    deleted = 0
    images = 0
    if execute:
        for item in items:
            try:
                # Re-list every prospective deletion after potential concurrent
                # recommissioning or archive changes. Never follow symlinks.
                fresh, notes = abandoned_sessions(reference_root, active_profile,
                                                   now, days)
                if notes or not any(
                    x['folder'] == item['folder'] and
                    x['active_sha'] == item['active_sha'] and
                    x['mtime_ns'] == item['mtime_ns'] and
                    x['files'] == item['files'] for x in fresh):
                    continue
                for path, expected in item['files']:
                    if digest(active_profile) != item['active_sha'] or (
                            path.is_symlink() or not path.is_file() or
                            digest(path) != expected):
                        raise ValueError('Capture changed; preserving remainder')
                    path.unlink()
                    images += 1
                if item['folder'].is_symlink():
                    raise ValueError('Capture directory changed')
                item['folder'].rmdir()
                deleted += 1
            except (OSError, ValueError) as exc:
                warnings.append('Abandoned capture preserved after change: ' + str(exc))
    return {'eligible_sessions': len(items), 'removed_sessions': deleted,
            'removed_images': images, 'warnings': warnings}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--reference-root', type=Path, required=True)
    cli.add_argument('--active-profile', type=Path, required=True)
    cli.add_argument('--run', action='store_true')
    args = cli.parse_args()
    result = clean(args.reference_root, args.active_profile, execute=args.run)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
