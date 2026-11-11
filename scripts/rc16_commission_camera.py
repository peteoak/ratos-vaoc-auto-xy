#!/usr/bin/env python3
"""RC16 offline full-frame camera commissioning: no crop, motion or offset writes.

Reuses the signed RC15 optical measurement rules. Ambiguous results remain
fatal, with diagnostic guidance that does NOT recommend tighter cropping.
"""
import argparse
import json
import sys
from pathlib import Path

from commission_camera import create_profile as original_create_profile


def create_profile(t0, t1, roi, camera_url, confirmed):
    if roi != 'auto':
        raise ValueError('RC16 full-frame commissioning requires --roi auto; '
                         'do not tighten or crop the camera frame')
    try:
        return original_create_profile(t0, t1, roi, camera_url, confirmed)
    except ValueError as error:
        message = str(error)
        if 'Ambiguous camera ROI:' in message:
            # Keep the exact candidate count but drop obsolete crop advice.
            concise = message.split(';', 1)[0]
            raise ValueError(
                '%s. Keep the complete camera frame and inspect the saved '
                'T0/T1 images with scripts/inspect_camera_roi.py. Check rim '
                'wear or shape, reflections, focus and lighting. No nozzle '
                'candidate was selected.' % concise) from error
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--t0', nargs='+', required=True)
    parser.add_argument('--t1', nargs='+', required=True)
    parser.add_argument('--roi', default='auto')
    parser.add_argument('--camera-url',
                        default='http://127.0.0.1:8080/?action=snapshot')
    parser.add_argument('--confirm-native-manual-vaoc', action='store_true',
                        required=True)
    parser.add_argument('--output', type=Path,
                        default=Path('/home/pi/auto_xy/camera_commission.json'))
    args = parser.parse_args(argv)
    profile = create_profile(args.t0, args.t1, args.roi, args.camera_url,
                             args.confirm_native_manual_vaoc)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError('Existing commissioning profile must not be overwritten')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, indent=2) + '\n',
                           encoding='utf-8')
    print('Full-frame commissioning profile created:', args.output)
    print('Operator native VAOC confirmation recorded; independently verify '
          'alignment before any automatic offset writes.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('CAMERA COMMISSIONING FAILED:', error, file=sys.stderr)
        sys.exit(2)
