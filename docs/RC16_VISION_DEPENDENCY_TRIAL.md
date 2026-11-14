# RC16 Dev1: vision dependency compatibility work (experimental)

> **HISTORICAL ENGINEERING RECORD — not current installation or operational guidance.** Initial NumPy/OpenCV ABI trial only; its declaration that RC16 is not on main and no active installer exists is no longer true. For current behaviour and remaining qualification use [RC16 Start Here](RC16_START_HERE.md) and [RC16 Release Status](RC16_RELEASE_STATUS.md).

This document applies **only to** `dev/rc16-resource-aware-integration`. The public RC15 Dev6 installer and `main` are unchanged. The RC16 print-preparation integration, in-place upgrade and validated installer are **not complete**.

## Why this exists

A tester reported `AttributeError: _ARRAY_API not found` / `ImportError: numpy.core.multiarray failed to import` after installing NumPy 2.0.2 with an OpenCV binary that expected the NumPy 1.x ABI. The original RC15 `requirements-vision.txt` permits incompatible combinations.

RC16 trials use this **exact** pairing, pending architecture testing:

- `numpy==1.26.4`
- `opencv-python-headless==4.11.0.86`

This is a trial compatibility pair, **not** a claim that every Raspberry Pi/Python/OS combination has been validated. Raspberry Pi OS 32-bit may use piwheels, where a compatible prebuilt wheel exists for some Python versions; the tool fails instead of silently compiling packages where there is no suitable binary wheel.

## Read-only diagnosis (existing isolated venv)

Run this command **from the RC16 branch checkout**:

```bash
python3 scripts/vision_env.py --check --venv /home/pi/auto-xy-venv
```

A failure means **stop**. Do not delete, overwrite, repair or move the existing venv while a printer installation relies on it. This checker is deliberately stricter than RC15: if installed versions differ from the trial pins, it refuses even when imports succeed.

## New venv on a development/test host ONLY

These commands are **not** an installation or upgrade of AutoXY. Only perform on a known-compatible test host, not on an active printer:

```bash
python3 scripts/vision_env.py --install --venv /home/pi/auto-xy-venv-rc16-test
python3 scripts/vision_env.py --check --venv /home/pi/auto-xy-venv-rc16-test
```

The utility:
- Refuses to modify any existing destination, including a broken one.
- Requires Linux and supported *candidate* interpreter/architecture combinations.
- Downloads prebuilt wheels only; never builds NumPy or OpenCV from source.
- Installs into a newly created isolated venv; smoke-tests the ABI via an actual `cv2.cvtColor` operation, then checks exact package versions.
- Cleans up an incomplete new environment if installation fails. It does not edit stock RatOS, Klipper, Moonraker, system Python or printer configuration.

Known caveats: the available wheel matrix depends on the OS, Python version and index configuration, and the supported-candidate host matrix is **not** an assertion of wheel availability. Existing broken venvs still require a separately backed-up and reviewed migration, not an automated overwrite.

## Offline regression tests

```bash
python3 -m unittest discover -s tests -p 'test_rc16_vision_setup.py' -v
```

RC16's GitHub Actions workflow also runs the existing project tests and a live NumPy/OpenCV ABI smoke test for three supported x86-64 Python versions. It does **not** emulate Raspberry Pi ARM, USB cameras, RatOS post-processing or MCU scheduling.

## Not yet implemented

- A verified migration from installed RC15 Dev6.
- Automatic RatOS-to-AutoXY protected print handoff.
- Resource-aware background file worker integrated into the Klipper flow.
- Automated manual VAOC recovery and resumed-print hardware tests.
- Final RC16 manifests, release notes and end-to-end qualification.

**Do not install the RC16 branch as a printer release or merge it to `main` on the strength of these dependency changes alone.**
