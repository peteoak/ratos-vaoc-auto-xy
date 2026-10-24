# Community beta — supervised test plan

**Every new installation is unverified until *that machine* has passed its own tests.** Do not simulate hard faults by unplugging a running camera or power-cutting a printing machine. Use the bundled offline suite for fault injection instead.

## Record before testing

- Printer model/build volume/IDEX configuration:
- RatOS version and Klipper build:
- Pi OS, Python architecture/version, Klipper home path:
- VAOC USB camera model, port, resolution and image orientation:
- ROI and current nozzle-rim size (px), lighting:
- Tool(s), filament and preheat target:
- Slicer and postprocessor version; `TOTAL_TOOLSHIFTS` supplied?:
- Does `python3 test_stage6_offline.py` pass?:
- Does `python3 install_stage6.py --check` pass?:
- Are `AUTO_XY_HEALTH` and `AUTO_XY_STATUS` correct?:

Never paste private data or unredacted config files into an issue.

## Test sequence

| Test | AUTO XY | Job | Expected | Evidence |
|---|---|---|---|---|
| 1 — OFF bypass | OFF | Any safe small job | RatOS normal startup, no VAOC, no offset change | Console log, job started |
| 2 — single T0 | ON | T0-only / toolshifts=0 | `SINGLE_TOOL` skip, no VAOC, Auto XY remains ON | Redacted console / state |
| 3 — single T1 | ON | T1-only / toolshifts=0 | Same skip | Redacted console / state |
| 4 — COPY | ON | Copy-mode test | Skip, print proceeds without VAOC | Redacted console |
| 5 — MIRROR | ON | Mirror-mode test | Skip, print proceeds without VAOC | Redacted console |
| 6 — independent two-tool | ON | Small two-tool print with real toolshift(s) | Calibration after bed heatsoak, **before** Beacon hot probe; two good verifications; VAOC exits; print continues | Redacted report JSON and normal continuation |
| 7 — printed alignment | ON | Dual-material registration test | Physical alignment agrees reasonably with camera | Measurements, method, conditions |

If a mode is physically unsafe or unsupported on your printer, mark **NOT TESTED**. Never modify printer mode merely to fill a test matrix.

**Important:** The single-tool detection trusts `TOTAL_TOOLSHIFTS`; if your slicer gives an incorrect count, the skip/calibration decision could be wrong. Inspect the generated `START_PRINT` line before testing.

## Error-handling criteria

For a naturally occurring recoverable image or convergence failure during a dual-tool print-start, expected behaviour is: one in-place attempt retaining the VAOC session; a second failure **aborts print-start**. Thermal, unexpected pose/motion and offset-write failures must abort immediately. These paths have **offline simulation coverage** but not all are hardware-validated. Don't intentionally cause collisions, power loss, webcam outages or heater runaway to test them.

## Logs and results

On the printer Pi, find the latest report:

```bash
cd ~/auto_xy
LATEST=$(ls -dt print_gate_runs/* 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then cat "$LATEST/report.json"; fi
```

Reports and camera JPEGs remain **local** unless you choose to share them. Redact IPs, private paths, passwords, tokens and identifying information before opening an issue. Include the calibration status, offset *changes* (with values rounded if preferred), counts, measured camera error, and mode. Do not include full G-code print files if they contain confidential models.

## Acceptance for a broader beta

Proposed minimum: independent successful print-start calibration and physical two-tool alignment on at least several different compatible machines; reproducible single/COPY/MIRROR skip; no incorrect position/offset save; camera failure handled safely; no failure to clear print-start gate; reliable installer checks and rollback. **One owner’s working printer is not sufficient to claim compatibility across the RatRig community.**


## Beta 4.2 owner validation snapshot (25 October 2026, NZ time)

**Observed / reported on the owner's RatRig:**

- Unhomed manual invocation correctly prompted for homing; no persistent fault reported.
- After homing, idle calibration reported `CALIBRATED_AND_VAOC_EXITED`, settling `STABLE` after two checks, one offset write, two verification attempts, final camera errors X `+0.028226 mm`, Y `+0.000779 mm`.
- Auto XY status afterward: `ON; FAULT=0; IN_PROGRESS=0`.
- The owner reported an independent IDEX print-start calibration continued through to printing normally.

**Evidence still needed:** the print-start run's own `report.json` has not been inspected; actual printed registration is not measured; physical single-tool T0/T1, COPY and MIRROR bypasses have not been confirmed for beta.4.2; the unusual gain warning path is tested offline but has not been seen on hardware; retention expiry and an actual full purge/reinstall are not hardware-verified.

Do not imply that a print-start report was examined just because the owner observed printing begin. Mark untested cases as **NOT TESTED**, never invent passing results.
