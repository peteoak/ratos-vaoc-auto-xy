#!/usr/bin/env python3
"""RC16 development: safe, wheel-only AutoXY vision-venv setup.

This is an opt-in, standalone utility. It never edits Klipper, RatOS or an
existing venv. Existing venv problems are diagnosed, never auto-repaired.
"""
import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / 'requirements-vision-rc16.txt'
SUPPORTED_PYTHONS = {(3, 9), (3, 10), (3, 11), (3, 12)}
SUPPORTED_MACHINES = {'aarch64', 'armv7l', 'x86_64'}
EXPECTED = {'numpy': '1.26.4', 'opencv-python-headless': '4.11.0.86'}


class VisionSetupError(RuntimeError):
    pass


def requirement_versions(path=REQUIREMENTS):
    versions = {}
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        name, sep, version = line.partition('==')
        if not sep or not name or not version or name in versions:
            raise VisionSetupError('Vision dependencies must be exact, unique package==version pins')
        versions[name] = version
    if versions != EXPECTED:
        raise VisionSetupError('Unreviewed dependency combination; expected %s' % EXPECTED)
    return versions


def host_check(version=None, machine=None, system=None):
    version = version or sys.version_info[:2]
    machine = machine or platform.machine().lower()
    system = system or platform.system()
    if system != 'Linux' or tuple(version) not in SUPPORTED_PYTHONS or machine not in SUPPORTED_MACHINES:
        raise VisionSetupError('Unsupported vision host: %s Python %s %s; inspect architecture and request a validated wheel pair' %
                               (system, '.'.join(map(str, version)), machine))
    return {'system': system, 'python': '.'.join(map(str, version)), 'architecture': machine}


# Run in the venv being tested, not in Klipper's Python or the system Python.
SMOKE = (
    'import json,numpy as n,cv2; '
    'a=n.zeros((4,4,3),dtype=n.uint8); '
    'b=cv2.cvtColor(a,cv2.COLOR_BGR2GRAY); '
    'assert b.shape==(4,4); '
    'print(json.dumps({"numpy":n.__version__,"opencv-python-headless":cv2.__version__}))'
)


def run_checked(command, timeout=120):
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        detail = getattr(exc, 'stderr', None)
        if isinstance(detail, bytes):
            detail = detail.decode('utf-8', 'replace')
        raise VisionSetupError('%s failed: %s' % (command[0], (detail or str(exc))[-1600:])) from exc


def verify_vision_env(venv, requirements=REQUIREMENTS):
    require = requirement_versions(requirements)
    venv = Path(venv)
    if venv.is_symlink() or not (venv / 'pyvenv.cfg').is_file():
        raise VisionSetupError('Missing or unsafe AutoXY isolated vision venv: %s' % venv)
    python = venv / 'bin/python'
    if not python.is_file():
        raise VisionSetupError('Vision venv has no Python executable: %s' % python)
    details = run_checked([str(python), '-c', SMOKE], timeout=20)
    try:
        actual = json.loads(details.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError, KeyError) as exc:
        raise VisionSetupError('Vision smoke test produced invalid output') from exc
    if actual != require:
        raise VisionSetupError('NumPy/OpenCV mismatch in vision venv: installed %s; required %s' %
                               (actual, require))
    return actual


def install_new_venv(venv, requirements=REQUIREMENTS):
    host = host_check()
    requirement_versions(requirements)
    venv = Path(venv).expanduser().absolute()
    if venv.exists() or venv.is_symlink():
        raise VisionSetupError('Existing vision venv will NOT be changed: %s. Run --check; plan a backed-up repair separately.' % venv)
    if not venv.parent.is_dir() or venv.parent.is_symlink():
        raise VisionSetupError('Vision venv parent must be an existing, non-symlink directory')
    # Separate temporary environment avoids exposing a partially installed final path.
    stage = Path(tempfile.mkdtemp(prefix='.auto-xy-vision-download-', dir=str(venv.parent)))
    created = False
    try:
        # Never relocate a venv: pip entrypoints contain absolute shebang paths.
        wheels = stage / 'wheelhouse'
        wheels.mkdir()
        # Use a temporary download environment so the system Python need not have pip.
        downloader = stage / 'downloader'
        run_checked([sys.executable, '-m', 'venv', str(downloader)], timeout=90)
        downloader_python = downloader / 'bin/python'
        # Download binary wheels first. Never let pip silently compile OpenCV on a Pi.
        run_checked([str(downloader_python), '-m', 'pip', '--disable-pip-version-check', 'download',
                     '--only-binary=:all:', '--dest', str(wheels),
                     '-r', str(requirements)], timeout=300)
        if venv.exists() or venv.is_symlink():
            raise VisionSetupError('Target venv appeared while downloading; refusing replacement')
        run_checked([sys.executable, '-m', 'venv', str(venv)], timeout=90)
        created = True
        python = venv / 'bin/python'
        run_checked([str(python), '-m', 'pip', '--disable-pip-version-check', 'install',
                     '--no-index', '--only-binary=:all:', '--find-links', str(wheels),
                     '-r', str(requirements)], timeout=300)
        verified = verify_vision_env(venv, requirements)
        created = False  # Installation is complete: leave the new environment in place.
        return {'host': host, 'versions': verified}
    finally:
        if created and venv.is_dir() and not venv.is_symlink():
            shutil.rmtree(venv)
        if stage.exists():
            shutil.rmtree(stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Read-only compatibility and venv smoke test')
    mode.add_argument('--install', action='store_true', help='Create a NEW isolated venv from binary wheels')
    parser.add_argument('--venv', type=Path, default=Path.home() / 'auto-xy-venv')
    args = parser.parse_args(argv)
    try:
        host = host_check()
        if args.check:
            output = {'host': host, 'versions': verify_vision_env(args.venv)}
        else:
            output = install_new_venv(args.venv)
        print('AUTOXY VISION %s PASS: %s' % ('CHECK' if args.check else 'INSTALL',
                                              json.dumps(output, sort_keys=True)))
        return 0
    except VisionSetupError as exc:
        print('AUTOXY VISION REFUSED: %s' % exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
