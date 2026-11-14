#!/usr/bin/env python3
"""RC16 Dev1 guarded, disabled-only fresh installer (two-phase rollback).

Reuses the verified RC15 Dev6 fresh installer to establish its exact baseline,
then stages the fully pinned RC16 files WITHOUT restarting Klipper between
steps. RC16 protected printing remains OFF; no calibration is enabled.

Not qualified as a user-facing release until supervised hardware validation.
"""
import argparse
import json
import sys
from pathlib import Path

import install_autoxy as rc15
import rc16_stage_upgrade as rc16
import vision_env

HOME = Path('/home/pi')
RECEIPT = 'rc16_fresh_receipt.json'


def verify_venv(home, live):
    if live:
        return vision_env.verify_vision_env(home / 'auto-xy-venv')
    return None


def preflight(home, root, live):
    rc16.verify_sources(root)
    # An existing camera commission or AutoXY config is machine-specific;
    # never let an ostensibly "fresh" install overwrite or reuse it.
    for rel in ('printer_data/config/auto_xy.cfg',
                'auto_xy/camera_commission.json'):
        target = home / rel
        rc16.require(not target.exists() and not target.is_symlink(),
                     'Fresh install refused: existing machine-specific AutoXY file: ' + rel)
    changes = rc15.plan(home, root, 'fresh')
    verify_venv(home, live)
    offsets = rc15.live_safe(home, 'fresh') if live else None
    return changes, offsets


def fresh_install(home, root, live):
    baseline, offsets = preflight(home, root, live)
    rc15_snapshot = rc15.install(home, baseline, 'fresh', offsets)
    rc16_snapshot = None
    try:
        additional = rc16.check_installation(home, root, live=False)
        rc16_snapshot = rc16.stage(home, additional)
        receipt = {
            'schema': 1,
            'version': rc16.VERSION,
            'home': str(home),
            'rc15_snapshot': str(rc15_snapshot),
            'rc16_snapshot': str(rc16_snapshot),
            'protected_start_enabled': False
        }
        target = rc16_snapshot / RECEIPT
        with target.open('x', encoding='utf-8') as output:
            json.dump(receipt, output, sort_keys=True, indent=2)
            output.flush()
        return rc16_snapshot
    except BaseException:
        # Every stage runs its own exact rollback. Roll back to the original
        # fresh state when the RC16 second step fails.
        if rc16_snapshot is not None:
            rc16.rollback(home, rc16_snapshot, live=False)
        rc15.rollback(home, rc15_snapshot, live=live)
        raise


def fresh_rollback(home, snapshot, live):
    root = (home / 'auto_xy/rollbacks').resolve(strict=True)
    snap = Path(snapshot).resolve(strict=True)
    rc16.require(snap.parent == root, 'Snapshot must be inside AutoXY rollbacks')
    receipt_file = snap / RECEIPT
    rc16.require(receipt_file.is_file() and not receipt_file.is_symlink(),
                 'Missing RC16 fresh install receipt')
    receipt = json.loads(receipt_file.read_text(encoding='utf-8'))
    rc16.require(receipt.get('schema') == 1 and
                 receipt.get('version') == rc16.VERSION and
                 receipt.get('home') == str(home) and
                 receipt.get('rc16_snapshot') == str(snap) and
                 receipt.get('protected_start_enabled') is False,
                 'Invalid or mismatched RC16 fresh receipt')
    baseline = Path(receipt['rc15_snapshot']).resolve(strict=True)
    rc16.require(baseline.parent == root and baseline != snap,
                 'Invalid legacy rollback snapshot')
    # A changed RC16 managed file is a refusal, not an excuse to overwrite it.
    rc16.rollback(home, snap, live=live)
    rc15.rollback(home, baseline, live=live)
    return snap


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--install', action='store_true')
    group.add_argument('--rollback', type=Path)
    ap.add_argument('--confirm', default='')
    ap.add_argument('--home', type=Path, default=HOME, help=argparse.SUPPRESS)
    ap.add_argument('--offline', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    home = args.home.absolute()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.offline:
            rc16.require(home != HOME, 'Offline tests must not target live /home/pi')
        else:
            rc16.require(home == HOME, 'Live fresh install supports only /home/pi')
        if args.rollback:
            rc16.require(args.confirm == 'ROLLBACK_FRESH_RC16_DEV1',
                         'Explicit ROLLBACK_FRESH_RC16_DEV1 confirmation required')
            result = fresh_rollback(home, args.rollback, live=not args.offline)
            print('RC16 fresh rollback complete:', result,
                  '(Klipper restart required; saved RatOS offsets unchanged)')
            return 0
        if args.install:
            rc16.require(args.offline,
                         'Disabled fresh installation is offline-development only')
        plan, offsets = preflight(home, root, live=not args.offline)
        print('RC16 DEV1 FRESH PREFLIGHT PASS; source hashes checked,',
              len(plan), 'baseline managed paths')
        if args.check:
            print('CHECK PASSED; NO FILES CHANGED')
            return 0
        rc16.require(args.offline,
                     'Disabled fresh-install workflow is offline-development only; '
                     'a qualified active RC16 fresh installer is required')
        rc16.require(args.confirm == 'FRESH_RC16_DEV1_DISABLED',
                     'Installation requires --confirm FRESH_RC16_DEV1_DISABLED')
        receipt = fresh_install(home, root, live=not args.offline)
        print('RC16 DEV1 FRESH INSTALLED AND DISABLED')
        print('RC16/RC15 rollback receipts:', receipt)
        print('NO calibration/print interception enabled. Do not enable until supervised qualification.')
        print('Restart Klipper while idle and verify native RatOS before any future opt-in.')
        return 0
    except Exception as exc:
        print('RC16 FRESH REFUSED:', exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
