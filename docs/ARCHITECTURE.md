# Architecture and known limitations

## Control flow

```text
RatOS START_PRINT (original slicer parameters)
  |
  +--> _USER_START_PRINT captures requested COPY/MIRROR/independent
  |    and TOTAL_TOOLSHIFTS before RatOS temporarily changes carriage state
  |
  +--> bed heatsoak / homing (RatOS)
  |
  +--> _USER_START_PRINT_AFTER_HEATING_BED
       |
       +--> AUTO_XY_GATE (Klipper extension)
            |-- if master OFF, single tool, COPY or MIRROR: skip
            |-- check homing, geometry, temperature and saved offsets
            |-- RatOS VAOC start, controlled T0/T1 sweeps and references
            |-- vision subprocess: JPEG snapshot and OpenCV nozzle-circle detection
            |-- compute offset error, bounded correction via _VAOC_SET_TOOL
            |-- two consecutive in-tolerance checks
            |-- if safely recoverable, allow ONE inline retry without VAOC exit
            |-- if success: exit VAOC; return control to original START_PRINT
            +-- if failure: stop/latched fault; manual review
  |
  +--> RatOS Beacon contact probing, bed mesh, nozzle final heating, priming, print
```

The extension executes machine movement via internal Klipper G-code, not Moonraker's queued `/printer/gcode/script` API, to avoid a self-deadlock in the print-start queue. The OpenCV worker runs in a separate Python environment and writes local capture/report artifacts. A Mainsail prompt cannot be awaited inside a synchronous Klipper G-code command; the print-start retry is automatic, immediate and strictly one use.

## Why this is not a generic plug-in yet

- The installer checks specific RatOS 2.1 macro text and ordering, including the early hook, bed heatsoak and toolshift count semantics. Other RatOS versions must be audited, not automatically assumed safe.
- Installation paths are currently `/home/pi/...` and the custom Klipper extension must be importable from `klippy/extras`.
- Nozzle detection uses `NOMINAL_CENTER=(960,540)`, radius range 115–185px, and an expected downward view. A camera with different magnification, rotation, exposure, nozzle design or field of view can fail or mis-detect.
- Camera XY offset and printed deposition axes are not necessarily identical; physical printed registration remains an independent acceptance test.
- This system can run with nozzle temperatures at ~150°C. Thermally induced nozzle offsets at printing temperature and Beacon contact probe temperature are not automatically compensated.
- A rollback of saved variables can leave the *currently applied* Klipper toolhead named offset uncertain until restored/verified.
- The measured sub-0.03mm goal is based on the camera error estimator, not a metrology-certified geometric accuracy guarantee.
- The installer intentionally refuses existing user hook collisions. A properly reviewed merge is preferable to automatically overwriting a user’s custom code.
- **No silent continue on fault.** Auto XY OFF bypasses only normal calibration, not previously latched faults.

## Source files

| File | Role |
|---|---|
| `auto_xy_gate.py` | Custom Klipper module: start-print gate, motion, correction, failure logic |
| `auto_xy_print.cfg` | Mainsail controls and RatOS user hooks, per-install camera settings |
| `auto_xy_vision_worker.py` | Separate venv worker fetching JPEG + calling detector |
| `auto_xy_detector.py` | Camera-specific circle detection and conservative low-light handling |
| `install_stage6.py` | Live idle/version prechecks, backups and file copy to Klipper |
| `disable_stage6.py` | Contingency removal of config include (not offset rollback) |
| `check_last_failure.py` | Read-only state and report inspection |
| `test_stage6_offline.py` | Simulated control and failure cases; no physical printer |

## Intended community improvements

1. Camera geometry wizard/calibration preflight that proves detection before allowing writes.
2. Configurable installer paths and safer packaging for non-`pi` users.
3. Additional tested camera resolutions/orientations and automatic quality gating.
4. Coverage of RatOS updates and hook compatibility with real version fixtures.
5. Independent physical two-colour calibration prints and quantitative test reports.
6. UI for status, last result and explicit operator-managed faults (without queue deadlock).
