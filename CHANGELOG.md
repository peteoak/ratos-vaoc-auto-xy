# v0.5.0-rc15-dev5 â€” hardware-tested community preview (2026-10-29)

- Consolidated automatic XY alignment, protected-print gating, native VAOC recovery, and the optional standalone endurance diagnostics into a versioned source and guarded fresh-install package.
- Added a persistent vision worker and opt-in coarse-guided, grid-aligned ROI precision detector; preserved the original detector and experimental settling/calibration options.
- Owner's RatRig V-Core 4 300 IDEX passed supervised 25-, 50-, and 100-tool-change endurance runs; the 100-change run recorded 101 visits/202 measurements, preserved saved offsets during endurance, and exited VAOC normally.
- Includes camera commissioning, rollback, offline tests and compatibility reporting. New installations on other machines and the 150Â°C detector mode remain unqualified; this is not a stable release.
- Historical Beta 4.2 and earlier notes are preserved below. See `docs/RELEASE_NOTES.md` and `THIRD_PARTY_NOTICES.md`.
# v0.1.0-beta.4.2 â€” alignment-first response verification (owner-validated release candidate)

- Replace narrow first-write-only gain exception with **two no-write independent alignment confirmations after any unusual but bounded offset response**, including a second correction. Alignment must stay within Â±0.030 mm per axis and repeat within 0.015 mm, with tight camera/return limits and unchanged saved offsets.
- Keep gain as a **diagnostic warning**, not a fatal condition in isolation once stable alignment is established. Still hard-fail gross observed response >0.11 mm, reversed movement, out-of-spec result, inconsistency, unexpected writes, or insufficient verification slots.
- Replay owner-measured gain 2.078 on Y after the first correction and 2.372 on X after the second in offline tests. Verify no extra offset write, normal VAOC exit on confirmation, fail/rollback on unsafe cases.
- Retain the beta.4.1 nonfault unhomed-prerequisite check, beta.4 settling, beta.3 retention, installer SAVE_CONFIG fix, limited print-start retry and existing offset caps. Full service restart now explicitly required in the instructions so the new Python extra actually loads.
- **Owner hardware checks:** idle calibration and independent-IDEX print-start-to-printing completed on the RatRig; unhomed manual start displayed the expected warning without a fault. Idle report verified two settling checks, one correction, and two in-tolerance confirmations. More bypass/print report evidence is still pending, and unusual-gain remeasurement remains offline-only.
- **Release candidate:** fix `AUTO_XY_STATUS` to print a mode-appropriate ON/OFF explanation, retaining the latched-fault warning. This changes only status wording, not motion or offset behaviour.

# Beta 4.1 homing preflight refinement (hardware test build)

- Manual `AUTO_XY_RUN_NOW` on an unhomed printer reports `Printer must be homed first (G28)` without entering VAOC, moving, creating a run or latching the safety fault.
- Independent two-tool PRINT with missing homing still **stops print start** before calibration, but does not create an Auto XY fault or interrupted-run marker for this missing prerequisite.
- Existing faults remain enforced, and any loss of homing **after** calibration begins remains a latched safety failure. The gate does not issue G28 automatically.
- Added offline regression tests for missing axes, tool-mode bypass, existing fault handling, and mid-calibration homing loss.
- Still pending supervised physical verification; this is not a released community update.

# v0.1.0-beta.4.1 â€” narrowly guarded first-write response recheck (hardware-test candidate)

- Keep existing post-offset gain 0.2â€“2.0 normal bounds. For the first correction only, one axis with a small (<=0.020 mm) write and marginal gain (2.0, 2.5] can be *conditionally verified* only if already in Â±0.03 mm measured alignment.
- Perform **two complete T0/T1/T0 no-write checks**, consuming two of six existing verification attempts. Require within-tolerance, â‰¤0.015 mm between consecutive readings, â‰¤0.015 mm T0 drift/scatter, and unchanged confirmed persisted offsets before accepting. No additional offset write in this path.
- Larger, reverse, multi-axis, inconsistent, or still-out-of-tolerance responses fail closed; existing fault, rollback and live VAOC recovery rules preserved.
- Record high gain and confirmation images/measurements under `response_gain_anomalies`, `response_gain_rechecks`, `response_gain_recheck_status` and `response_gain_warning`.
- Add focused offline regression tests reproducing the owner's Y gain 2.078 alongside negative cases. Pending supervised physical trial.

# v0.1.0-beta.4 â€” pre-adjustment toolshift settling (hardware-test candidate)

- After calibrating live camera pixel-to-mm scale but **before any saved-offset write**, require at least two full, camera-measured T0 -> T1 -> T0 sequences with <=0.015 mm axis-to-axis change between consecutive readings. Allow at most four sequences; if they never settle, fail closed and leave saved offsets unchanged.
- Each reference already checks paired images, T0 return drift, the camera temperature guard, and the VAOC motion envelope. Settling measurements are recorded separately in the run's `report.json` and retain the associated images for diagnosis.
- Preserve the existing **post-adjustment** response gain (0.2â€“2.0), absolute-response limit, 10 corrections, six verifications, Â±0.03 mm target and cumulative 0.30 mm cap. Settling cannot replace verification of actual offset writes.
- Fix the read-only `check_last_failure.py` to compare against original saved offsets when confirmed rollback restored them, while continuing to warn that runtime compensation is not verified by that comparison.
- Regression simulations cover stable and unstable initial toolshifts and restored-offset diagnostics, in addition to earlier installer/retention/retry tests.
- **Not yet live-tested:** Beta.4 needs supervised physical IDEX validation before being published as a broadly recommended community release.

# v0.1.0-beta.3 â€” retention + safe clean uninstall / reinstall

- Add isolated, daily-when-idle retention worker: successful calibration images 7 days; failed images 30 days; run JSON reports 90 days. Unknown, incomplete or altered runs are preserved. Explicit `--check` preview and `--run` options.
- Add optional full software removal with `uninstall_auto_xy.py`: read-only check, backups **outside** `~/auto_xy`, removes installed Klipper extension and any stage 5/6 include **above** Klipper SAVE_CONFIG, and preserves `ratos-variables.cfg` / stored IDEX X/Y/Z offsets.
- Add explicitly confirmed `--purge-project-files` / `--purge-venv` options for local clean-room reinstall testing; NOT automatic, and they delete historical images/scripts.
- Add regression tests for image retention, symlink/unknown-file preservation, SAVE_CONFIG integrity, uninstall and install-include roundtrip. Github Actions now runs these tests.
- No changes to the XY measurement, VAOC movement sequence, correction limits, inline retry or print-mode classification.
- **Offline tested only:** the background timer and clean uninstall / reinstall still require the printer owner's supervised first live test.

# v0.1.0-beta.2 â€” SAVE_CONFIG installer safety hotfix

**Important:** This fixes a community-reported defect in beta.1's `install_stage6.py`, which could append `[include auto_xy_print.cfg]` **after** Klipper's generated `SAVE_CONFIG` section. That can invalidate the autosave data and cause later SAVE_CONFIG errors. **Do not install beta.1.**

- Install a new include **before** the SAVE_CONFIG marker, preserving all existing `#*#` lines unchanged, or replace the old Auto XY include in place.
- Repair `disable_stage6.py` so restoring the legacy include cannot append below SAVE_CONFIG.
- Add `printer_cfg_editor.py` for shared, idempotent, validated config transformations and atomic writes.
- Installer refuses malformed SAVE_CONFIG blocks, duplicate includes, and unexpected content after the marker; verifies edit before writing any files.
- Add optional `repair_save_config.py --check` / `--repair` for beta.1 users **only** when the tail contains a known misplaced Auto XY include; it backs up printer.cfg before repair.
- Add 14 regression tests, including full mocked installer execution on an existing SAVE_CONFIG config, and add the tests to GitHub Actions.
- **Calibration/print-start algorithms are unchanged** from Stage 6.3.7. No testing on physical hardware of this specific patch yet.

## Previously: Stage 6.3.7

# Stage 6.3.7 â€” Only independently printed two-tool jobs calibrate

Relative to Stage 6.3.6:

- **Single-head T0 and single-head T1 prints now bypass VAOC**, including when the selected RatOS printing mode is `IDEX_SINGLE`.
- Dual-tool independent prints (`IDEX_SINGLE` with `TOTAL_TOOLSHIFTS > 0`) still run automatic calibration.
- COPY and MIRROR bypass unchanged.
- Classification happens at the early `_USER_START_PRINT` hook, using the original carriage mode **and** the `TOTAL_TOOLSHIFTS` slicer/postprocessor parameter, before RatOS changes carriage state.
- The later after-bed-heatsoak hook consumes the one-use classification and cannot mistake COPY/MIRROR for an independent print.
- The installer additionally confirms installed RatOS `START_PRINT` uses `TOTAL_TOOLSHIFTS` to make the same one-tool vs two-tool decision. If source code differs, installation refuses.
- Existing `AUTO_XY_TOGGLE` bypass, 10 corrections, 6 verification attempts, one immediate inline PRINT retry, 150Â°C preheat, 155Â°C camera maximum, and Â±0.03 mm tolerance are unchanged.
- Zero or **missing** `TOTAL_TOOLSHIFTS` results in bypass, matching RatOS's `default(0)` behaviour. For a genuine two-tool print, ensure your RatOS slicer postprocessor supplies this count. Calibration cannot infer tool use by looking at the currently selected carriage.

**Testing:** Local offline simulations cover single T0/T1 bypass, COPY/MIRROR bypass, dual independent calibration, stale/missing mode handling, installation preflight, bounded adjustments, retries and fault handling. This release has **not been validated on the physical printer**.
