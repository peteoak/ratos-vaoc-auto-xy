# RatOS VAOC Auto XY — Community Beta

**Experimental automatic T0/T1 XY nozzle alignment for RatOS 2.1 IDEX printers using a fixed VAOC camera.**

**Version:** `v0.1.0-beta.4.2` (code baseline: Stage 6.3.7 + settling and alignment-first response verification) · **Status:** Owner-validated release candidate; community testing requires supervised commissioning; **not an official RatOS/RatRig project**.

> **Installer safety fix (beta.2 and later):** A community tester identified that beta.1 could append an `[include]` after Klipper's generated `SAVE_CONFIG` block, risking loss of auto-saved settings and future `SAVE_CONFIG` errors. **Do not use beta.1 installer.** This release inserts includes above `SAVE_CONFIG`, patches the disable script, and provides an explicit read-only check/repair tool. If you already installed beta.1, read [Existing-install repair](docs/INSTALL.md#b1-fixing-a-previous-beta1-install) before upgrading.

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

### Pre-adjustment toolshift settling (new in beta.4)

After measuring live camera scale, **before the first offset write**, the gate runs complete camera-observed `T0 -> T1 -> T0` tool-change sequences. Two consecutive sequences must agree within **0.015 mm on each axis**. If not, it tries up to **four** sequences; a persistent mismatch fails the calibration closed, without changing saved offsets. This check is logged in `report.json` as `settling_checks` and `settling_status`, independently of the ten correction and six verification budgets.

Post-correction response is still recorded and checked for gross/reverse movement, but **verified final XY alignment is the success criterion**. A stable initial tool change does not prove a subsequent saved offset write was applied correctly. Settling takes additional camera captures and time.

### Alignment-first response verification (new in beta.4.2 test build)

The controller **does not fail solely because a response-gain ratio exceeds 2.0** when the resulting measured X **and** Y errors are within **±0.030 mm**. This applies after **any correction**, not just the first. An unusual response is recorded in `response_gain_anomalies` and triggers **two additional complete T0 → T1 → T0 camera measurements without writing further offsets**. Both repeat measurements must independently be within ±0.030 mm, agree within **0.015 mm** with the preceding result, have T0 return drift and camera scatter ≤0.015 mm, and show the exact expected persisted offset values. They use two of the normal six verification slots. Only then can the run complete and exit VAOC, with a prominent diagnostic `response_gain_warning`.

**The safety guards remain:** an observed response exceeding **0.11 mm**, reverse direction (non-positive gain), anomalous result still outside alignment tolerance, unstable repeat measurements, changed saved offsets, camera/temperature/position failures, or insufficient verification budget all fail closed and require safe VAOC recovery. Up to ten offset adjustments, a 0.05 mm maximum step, a 0.30 mm maximum cumulative adjustment, and the existing rollback and print gating remain unchanged. An in-tolerance camera result is not proof of mechanical accuracy; inspect warnings and confirm printed registration before wider use. The owner has now completed one successful supervised idle calibration and one successful automatic independent-IDEX print-start-to-printing run on the RatRig. The unusual-gain warning/remeasurement branch has **offline simulation coverage only**, and printed XY registration and mode bypasses require additional validation.

This build also includes the friendly **“Printer must be homed first (G28)”** prerequisite check from the Beta 4.1 homing test build. It does not home automatically or bypass faults after VAOC begins.

### Automatic image and report retention (new in beta.3)

The Klipper extension periodically launches a separate **standard-library-only** housekeeping process while the printer is **idle** (first check about five minutes after Klipper starts, then roughly daily). It does not interrupt calibration and never changes printer configuration or saved offsets.

- Successful calibration JPEGs: keep **7 days**.
- Failed calibration JPEGs: keep **30 days**.
- `report.json` and its run folder: keep **90 days**.
- Incomplete/unrecognised reports and folders containing unexpected files are **preserved** for manual inspection.

Retention days are configurable in `auto_xy_print.cfg`. Preview what would be removed at any time:

```bash
python3 ~/auto_xy/stage6/auto_xy_retention.py --check
```

Manual cleanup (`--run`) is available but normally unnecessary. Old images are **not overwritten** before their retention period. More in [Retention and diagnostics](docs/RETENTION.md).

### Clean uninstall and fresh-install validation (new in beta.3)

Use `uninstall_auto_xy.py --check` to preview a safe software uninstall. The `--uninstall` option removes the printer include and installed Klipper extension and makes timestamped backups **outside** `~/auto_xy`. A separate, explicitly confirmed full-purge option can delete all earlier Auto XY scripts/images and the Auto XY Python venv for a true clean software reinstall. **RatOS's saved IDEX offsets are never reset or erased.** See [Clean reinstall walkthrough](docs/CLEAN_REINSTALL.md).

### Operating limits (current defaults)

| Parameter | Limit |
|---|---|
| Alignment target | ±0.030 mm per axis (camera-measured) |
| Pre-adjustment settling | Two agreeing full T0/T1/T0 measurements ≤0.015 mm difference, at most four checks |
| Success criterion | Two consecutive in-tolerance checks; anomalous but bounded responses need two independent no-write confirmations |
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
python3 test_response_gain_recheck.py
python3 test_homing_preflight.py
python3 install_stage6.py --check   # read-only check; must PASS
python3 install_stage6.py --install # only if check PASS and printer safely idle
```

Do **not** run the installer on GitHub's CI host or a random Linux PC. The installer copies files into Klipper and adds one local include to `printer.cfg`; it does not modify RatOS-managed macros. You must separately prepare `~/auto-xy-venv` for the image worker as described in [INSTALL](docs/INSTALL.md).

After installation, while idle and with VAOC fully exited, perform a **full Klipper service restart** (`sudo systemctl restart klipper`). The G-code `RESTART` alone may re-read config without reloading the Python extra. Then use Mainsail:

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

### Calibration requested before homing (Beta 4.1 test update)

If `AUTO_XY_RUN_NOW` is called with any XYZ axis unhomed, it now prints `Printer must be homed first (G28)` and returns without a calibration fault, motion, or offset changes. Home normally using your printer's safe RatOS homing procedure, then retry manually. An unhomed dual-tool print start still **stops the print**, but no persistent Auto XY fault is latched for this preflight-only condition. Loss of homing during calibration is still a hard fault.
