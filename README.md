# RatOS VAOC Auto XY — Community Beta

**Experimental automatic T0/T1 XY nozzle alignment for RatOS 2.1 IDEX printers using a fixed VAOC camera.**

**Version:** `v0.1.0-beta.1` (code baseline: Stage 6.3.7) · **Status:** Community testing; **not an official RatOS/RatRig project**.

> **Safety / compatibility:** This software moves toolheads near a camera and **writes persistent RatOS IDEX offsets**. A bad image, incompatible printer macro, incorrect camera framing or bad installation can cause a collision or spoil prints. Do not install during a print. Back up first. Observe every commissioning run. This beta is **not universal or guaranteed safe for unattended use**. See [Safety](docs/SAFETY.md).

## What does it do?

After RatOS bed heatsoak — **before Beacon's high-temperature contact probing** — it uses a fixed camera to measure the nozzle rims, calibrate pixels/mm from controlled carriage movements, adjust the secondary toolhead's saved X/Y offsets through native RatOS VAOC commands, and verify alignment before allowing an independent two-tool print to continue.

The camera processing runs in a separate Python environment; a custom Klipper gate keeps print-start execution in one controlled sequence. If a recoverable camera/verification issue occurs, one **automatic, in-place** retry is permitted; it is **not** an endless loop. Hard faults stop the print and latch a warning. No manual click is required to make a retry inside a running print.

### When it runs

| Job | Auto XY ON | Auto XY OFF |
|---|---|---|
| Independent IDEX, both tools actually used (`TOTAL_TOOLSHIFTS > 0`) | **Calibrate** | Skip |
| Single T0 or single T1 (`TOTAL_TOOLSHIFTS = 0`) | Skip | Skip |
| COPY | Skip | Skip |
| MIRROR | Skip | Skip |

The master `AUTO_XY_TOGGLE` and `AUTO_XY_STATUS` controls appear in Mainsail's Macros panel. Bypassing does **not** discard your saved offsets. **A latched safety fault must still be addressed; turning calibration OFF is not a fault override.**

The slicer must send accurate `TOTAL_TOOLSHIFTS` to RatOS. A missing parameter is treated as zero and can wrongly skip a real two-tool job. Do not assume carriage mode at the later camera hook reflects the original job mode: RatOS temporarily changes it during startup.

### Operating limits (current defaults)

| Parameter | Limit |
|---|---|
| Alignment target | ±0.030 mm per axis (camera-measured) |
| Success criterion | Two consecutive in-tolerance checks |
| Adjustments | Up to 10 **per attempt** |
| Verification-only checks | Up to 6 **per attempt** |
| Correction per axis per adjustment | 0.050 mm |
| Total X or Y offset change across both attempts | 0.300 mm |
| Automatic print-start retry | **Once**, only for recoverable errors |
| Camera-time measured nozzle temperature cap | 155°C |
| Camera-time heater target cap | 150°C |

These are **software constraints**, not a guarantee of accuracy or mechanical clearance.

## Supported / untested hardware

**Known tested setup:** RatRig V-Core 4 300 IDEX; RatOS 2.1; Beacon; fixed downward-looking USB VAOC camera served via Crowsnest MJPEG snapshot at port 8080; Mainsail/Moonraker; Raspberry Pi account `/home/pi`; Python with NumPy/OpenCV.

**Assumptions in this beta:**

- Installed RatOS macro order and toolshift semantics must match the installer guard. Installer fails closed if it doesn't.
- Klipper path: `/home/pi/klipper/klippy/extras/`; config: `/home/pi/printer_data/config/`.
- The camera must provide `http://127.0.0.1:8080/?action=snapshot` from the Pi itself, or the `camera_url` option must be changed.
- **Current detector is tuned to a 1920 × 1080 image and an approximately 150 px-radius nozzle rim near (960, 540)**. The configured ROI and detector geometry are not automatically transferable to arbitrary cameras or mounts. Do not allow motion/offset writes until your camera view is validated.
- Existing, manually usable RatOS VAOC and safe nozzle-camera clearance are essential; this does not install or calibrate VAOC itself.
- This installer presently targets user `pi`. Other usernames/filesystem layouts require a reviewed port; don't use blind search-and-replace on a production printer.

## Start here

1. Read **[Before you install: safety and requirements](docs/SAFETY.md)**.
2. Follow the full **[Installation and rollback guide](docs/INSTALL.md)**. It includes Python environment setup, backups, camera preflight, checks, installation, and recovery.
3. Run the **[Community testing checklist](docs/TESTING.md)** starting with Auto XY OFF, then single/COPY/MIRROR skips, and finally a **supervised** two-tool calibration.
4. Submit results using [GitHub Issues → Test report](.github/ISSUE_TEMPLATE/02-test-report.yml) or report problems using [Bug report](.github/ISSUE_TEMPLATE/01-bug-report.yml). **Remove IP addresses, private configuration and identifiers from uploads.**

For integrators, see [Architecture and limitations](docs/ARCHITECTURE.md). For maintainers, see [Contributing](CONTRIBUTING.md), [Publishing releases](docs/PUBLISHING.md), and [Changelog](CHANGELOG.md).

## Quick install overview (read detailed instructions first)

Run these **on your printer Pi only**, after confirming the prerequisites and backing up the printer:

```bash
cd ~/ratos-vaoc-auto-xy   # directory where you cloned or uploaded THIS repository
python3 test_stage6_offline.py
python3 install_stage6.py --check   # read-only check; must PASS
python3 install_stage6.py --install # only if check PASS and printer safely idle
```

Do **not** run the installer on GitHub's CI host or a random Linux PC. The installer copies files into Klipper and adds one local include to `printer.cfg`; it does not modify RatOS-managed macros. You must separately prepare `~/auto-xy-venv` for the image worker as described in [INSTALL](docs/INSTALL.md).

After restarting Klipper, use Mainsail:

```gcode
AUTO_XY_HEALTH
AUTO_XY_STATUS
AUTO_XY_OFF
```

## Current community test status

- **Confirmed on one owner's printer:** prior Stage 6.3.3 pre-Beacon **dual-tool print-start calibration**, including completion and continuation to printing; the CAMERA measured approx X −0.0048 mm, Y −0.0013 mm in that run.
- **Confirmed on one owner's printer:** COPY mode bypass in Stage 6.3.7.
- **Not yet individually proven on hardware for this exact revision:** single T0/T1 bypass, MIRROR bypass, and in-place retry under a real recoverable failure. They have **offline simulation coverage**.
- Multi-printer compatibility and long-run reliability **not established**. Printed dimensional accuracy should be independently checked.

The offline test suite exercises normal execution and failure paths but **does not simulate actual Klipper scheduling, electrical failure, camera focus, collision clearance, or printed alignment**.

## License and relationship

This repository includes a proposed **GPL-3.0 license** suitable for a Klipper-oriented community project. The repository owner should verify the license decision and contributor rights before publishing. Dependencies and upstream RatOS/Klipper retain their own licenses. **Not affiliated with or endorsed by RatRig, RatOS, Klipper, Beacon, or Mainsail.**
