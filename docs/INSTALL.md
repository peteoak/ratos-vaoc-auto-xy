# Installation, upgrade, rollback

**Read [SAFETY.md](SAFETY.md) first.** This is a beta for experienced RatOS IDEX owners; installations must be supervised and reversible.

## A. Preparation (Pi terminal / SSH)

Do not install while printing or while VAOC is active. Verify there is no outstanding Auto XY fault. Back up your current settings *before* proceeding (adjust the variable filename only if your RatOS installation uses a different path):

```bash
mkdir -p ~/auto_xy/community_manual_backups
cp -p ~/printer_data/config/printer.cfg ~/auto_xy/community_manual_backups/printer.cfg.before-community-beta
cp -p ~/printer_data/config/ratos-variables.cfg ~/auto_xy/community_manual_backups/ratos-variables.cfg.before-community-beta
```

**Important:** if a file does not exist, stop and identify your actual RatOS configuration paths first. This beta's installer supports `/home/pi` only. Backups are for emergency recovery, not instructions to blindly restore offsets while printing.

Verify camera HTTP capture is available from the Pi:

```bash
python3 - <<'PY'
from urllib.request import urlopen
u='http://127.0.0.1:8080/?action=snapshot'
with urlopen(u,timeout=5) as response:
    data=response.read(12_000_001)
print('Snapshot bytes:',len(data),'JPEG:',data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'))
PY
```

If this fails, configure your VAOC Crowsnest/USB camera first. A webcam visible in your browser is **not** sufficient evidence that the Pi's loopback URL works.

## B. Get the code and vision dependencies

Option 1: clone the project (after the repository owner has published it):

```bash
cd ~
git clone https://github.com/OWNER/ratos-vaoc-auto-xy.git
cd ratos-vaoc-auto-xy
```

Replace `OWNER` with the real repository owner. Option 2: download the GitHub Source ZIP and upload its extracted contents to the Pi. The **installer must run from the extracted source directory**, where `install_stage6.py` and `auto_xy_gate.py` are together.

Install image dependencies in a **separate virtual environment** (not Klipper's Python):

```bash
python3 -m venv ~/auto-xy-venv
~/auto-xy-venv/bin/python -m pip install --upgrade pip
~/auto-xy-venv/bin/python -m pip install -r requirements-vision.txt
~/auto-xy-venv/bin/python -c 'import cv2,numpy; print("OpenCV",cv2.__version__,"NumPy",numpy.__version__)'
```

**Platform caveat:** wheels and installation steps vary with Raspberry Pi OS architecture. If a pinned OpenCV or NumPy wheel is unavailable, do not overwrite Klipper's environment or force an untested system upgrade; open a compatibility issue. Existing `~/auto-xy-venv` can be reused after checking versions.

The default `auto_xy_print.cfg` references `/home/pi/auto-xy-venv/bin/python` and `~/auto_xy/stage6/auto_xy_vision_worker.py` (copied by the installer). This release's hard-coded paths are **not portable** to an arbitrary username without review.

## C. Review camera calibration and printer assumptions

- `auto_xy_print.cfg` default `roi: 680,275,590,555`, snapshot `http://127.0.0.1:8080/?action=snapshot`.
- `auto_xy_detector.py` expects a nearly centred ~150px nozzle radius in a 1920×1080 image (nominal centre 960,540; accepted radius 115–185px). **Changing the ROI alone cannot adapt arbitrary hardware geometry.**
- The X/Y motion-sweep scale is recalibrated during runs, but it cannot correct an incorrectly detected nozzle or a geometry mismatch.
- At the print-start hook, RatOS will pass `TOTAL_TOOLSHIFTS`; do not enable if the slicer omits or misreports that count.

For a first public test on a new camera geometry, **do not proceed to a live calibration** until you have inspected sample stills and the detector with the camera/nozzle correctly positioned. The current project does not include a general-purpose camera wizard. Community contributions are welcomed.

## D. Tests and installer dry run

Turn the system OFF using Mainsail (`AUTO_XY_OFF`) where that macro already exists; otherwise inspect the current `auto_xy_enabled` saved value and ensure your old system is OFF. Klipper should be Ready, not Printing/Paused, and VAOC inactive.

```bash
cd ~/ratos-vaoc-auto-xy
python3 -m py_compile auto_xy_gate.py auto_xy_detector.py auto_xy_vision_worker.py install_stage6.py test_stage6_offline.py
python3 test_stage6_offline.py
python3 install_stage6.py --check
```

The test suite simulates faults and sometimes prints expected tracebacks; it should **exit zero** and end with PASS messages. The `--check` action contacts the local Moonraker, validates that Auto XY is OFF and the printer is idle, checks conflicts, expected RatOS macro order and the `TOTAL_TOOLSHIFTS` condition. It does not change files. If it fails, *stop*, collect redacted output and report a compatibility issue. **Do not bypass the guard just to install.**

## E. Install and reboot Klipper (not while printing)

```bash
python3 install_stage6.py --install
```

The installer:

- Backs up `printer.cfg`, prior Auto XY config and prior custom Klipper extension under `/home/pi/auto_xy/stage6_backups/<timestamp>/`.
- Copies `auto_xy_gate.py` to `/home/pi/klipper/klippy/extras/`.
- Copies the camera worker/detector and recovery scripts to `/home/pi/auto_xy/stage6/`.
- Installs `auto_xy_print.cfg` under `/home/pi/printer_data/config/` and adds a single `[include auto_xy_print.cfg]` to `printer.cfg` (replacing a known old include when present).
- Does **not** edit RatOS-managed macros or saved IDEX toolhead offsets.

Restart Klipper from Mainsail while idle, then in the Mainsail console:

```gcode
AUTO_XY_HEALTH
AUTO_XY_STATUS
AUTO_XY_OFF
```

Confirm the version/limits. The Mainsail button `AUTO_XY_TOGGLE` is the master bypass; `AUTO_XY_STATUS` shows actual saved state. For first commissioning, leave OFF until you have finished the validation checklist.

## F. First supervised tests

Follow [TESTING.md](TESTING.md) exactly. Start with Auto XY OFF, then with ON verify single-tool/COPY/MIRROR bypass; only then test a small independent two-tool print at your own risk. Print-start should occur **after bed heatsoak and before high-temperature Beacon contact probing**.

## G. Diagnose a failed calibration

With the machine in a safe state, on the Pi:

```bash
python3 ~/auto_xy/stage6/check_last_failure.py
```

This is read-only; it does not clear faults. Compare the reported `idex_xoffset`, `idex_yoffset`, `idex_zoffset` with recent saved confirmed writes and your backup. Carefully check VAOC state, homing, carriage selection and current compensation. If the camera/VAOC failed, do not force the print through. For support attach a **redacted** `report.json` (not your entire printer config or user token).

## H. Disable / rollback

If it is unsafe or the Klipper config is broken, stop printing. With the printer idle, run:

```bash
python3 ~/auto_xy/stage6/disable_stage6.py
```

(If the version is not yet installed, run the same file from your extracted project folder.) This backs up and removes `[include auto_xy_print.cfg]` from `printer.cfg`, then restores the legacy include when applicable. Restart Klipper while idle. **Disabling the include does not automatically delete, roll back or validate any saved XY offsets**; manually verify the printer's resulting configuration and active compensation before printing.

If a new version fails during Klipper startup, you may need to restore the timestamped `printer.cfg` and previous `auto_xy_gate.py` from `/home/pi/auto_xy/stage6_backups` before restarting. Never blindly replace `ratos-variables.cfg` during printing. Seek help if restoring active compensation is unclear.
