# AutoXY v0.5.0-rc15-dev5 — RatOS 2.1 IDEX automatic camera XY alignment

**Hardware-tested development pre-release**, not a stable or universally verified RatOS add-on. Independently maintained; not affiliated with RatRig/RatOS.

This repository snapshot consolidates the current **tested dev5 controller** rather than changing its calibration or movement code. It is intended to replace the old beta README/installer on a review branch, after GitHub publication and licence review. The code automatically aligns the two toolheads using a fixed VAOC camera after bed heatsoak and before later print-start probing, with a native RatOS VAOC fallback. A completed endurance test never authorises printing.

## What has been tested

- RatRig V-Core 4 300 IDEX, RatOS 2.1, fixed USB VAOC camera, Beacon, physical endstop homing.
- Supervised **25, 50 and 100 actual tool changes** with two images per measured visit; 100-change run completed 101 visits, 202 images, zero endurance offset writes, no reported MCU fault.
- Automatic correction and independent verification are reported working on the owner's printer; protected-print and native manual VAOC recovery were also manually tested previously, but not independently reproduced on a second machine.
- Vision candidate benchmark: 384 archived images; dev5 ROI mode matched dev4 for 380 accepted images with **zero changed precision centres**. Four were rejected by both. The new ROI detector resolved the earlier 150°C ambiguous photograph offline; the successful 100-change run was around 100°C, so no hotter endurance qualification is claimed.
- An earlier transient Klipper T1 MCU `Timer too close` shutdown was not reproduced in completed dev5 tests; its root cause is not proven.

## Installation: new RatOS 2.1 systems only

**Never copy source files over an active printer.** Fresh installer **refuses an existing AutoXY installation** and requires an idle/cooled printer, native VAOC already available, no active print/retry/fault, stock VAOC camera and a working isolated Python venv. It never overwrites `ratos-variables.cfg` or saved XY/Z offsets.

1. Make a full RatOS backup. Perform native manual VAOC and verify offsets exist. Confirm physical X/Y homing, camera alias and stock VAOC frontend work.
2. Set up `/home/pi/auto-xy-venv` with `requirements-vision.txt` *outside Klipper's Python environment* (see `docs/NEW_INSTALL.md`).
3. Transfer or clone this package onto the Pi and **read** the example configuration. In the package directory run:

```bash
python3 scripts/install_autoxy.py --check --mode fresh
```

4. Only after `CHECK PASSED` and physical verification of idle, cool, safe printer state:

```bash
python3 scripts/install_autoxy.py --install --mode fresh --confirm INSTALL_RC15_DEV5_FRESH
sudo systemctl restart klipper
```

5. Rehome and complete camera commissioning via `scripts/commission_camera.py` and a supervised, first no-write test before enabling automatic corrections or protected printing. **Do not copy nozzle offsets, camera profiles, or config files from another machine.** See `docs/NEW_INSTALL.md`.

*This fresh installer has offline tests, but has NOT been proven in a fresh RatOS installation on a second printer. Publication should label the release as a prerelease.*

## Already running RC15 dev5 (owner's printer)

**Do not reinstall.** Your already proven controller is the release baseline. To compare its runtime sources with this publication snapshot (read-only):

```bash
cd ~/AutoXY_v0.5.0-rc15-dev5_GitHub_Review
python3 scripts/check_installed_dev5.py
```

No restart, homing, offset changes or printer motion are needed to run this verification. If it differs, stop and inspect; this package intentionally has no force-install onto an existing RC15 dev5 system.

## Key configuration (in `[auto_xy_gate]`)

| Setting | Options / meaning |
|---|---|
| `camera_detector_mode` | `legacy` (fresh default), `two_stage`, `two_stage_roi` (tested crop) |
| `camera_worker_mode` | `legacy` or `persistent` |
| `settling_path` | `original` (default), experimental `diagonal` / `dogleg` |
| `thermal_mode` | `ratos` (default existing RatOS heating) or `slicer_derived` |
| `thermal_reduction_t0`, `_t1` | Per-tool reductions from slicer layer temperatures, default 50°C; capped by camera temperature limits |
| `max_camera_nozzle_temp`, `max_camera_target_temp` | Camera measurement temperature safety limits |
| `protected_print_enabled` | Opt-in guarded held-file workflow |
| `allow_experimental_alignment_writes` | Disabled by default |
| `commissioned_detector_guard` | Needed for `two_stage` and `two_stage_roi` |

**Temperature options in this build are configuration keys, not a Mainsail dropdown.** A fixed absolute-camera-temperature strategy and GUI dropdown are future work; they are not silently implemented in this publication snapshot. The slicer-derived option needs explicit per-tool slicer temperatures and independently validated caps. For existing users, leave the proven RatOS heatsoak temperature settings unchanged until reviewed.

Measurement-only mode does not write offsets and serves diagnostics; if its existing print-start path is used with qualifying in-tolerance readings, that behavior is intentionally preserved for now. The diagnostic endurance command never authorises printing.

## Safety and compatibility

- Heating/Beacon order, original tool-change path, existing verification tolerances and native VAOC recovery semantics are unchanged from RC15 dev5.
- Every protected new print must have valid homing; after disabling motors, the homing flag is lost and the controller refuses motion until rehomed. No stale alignment should be inferred from a previous print.
- A 1 mm diagnostic drift ceiling is **not** a production alignment tolerance.
- For `AUTO_XY_ENDURANCE_RUN TOOL_CHANGES=100 DRIFT_LIMIT=1.0`, printer must be prepared and under supervision; it does not print.
- Individual camera references and metadata are machine-specific. Never ship them in public releases.
- Licence/provenance of adapted upstream RatOS macros needs final checking before **public** GitHub redistribution; see `THIRD_PARTY_NOTICES.md`.

See `docs/GITHUB_RELEASE.md` for the review-branch publication process. **No live printer was changed and no GitHub commit was made by generating this package.**
