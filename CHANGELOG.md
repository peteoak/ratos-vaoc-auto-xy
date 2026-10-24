# v0.1.0-beta.3 — retention + safe clean uninstall / reinstall

- Add isolated, daily-when-idle retention worker: successful calibration images 7 days; failed images 30 days; run JSON reports 90 days. Unknown, incomplete or altered runs are preserved. Explicit `--check` preview and `--run` options.
- Add optional full software removal with `uninstall_auto_xy.py`: read-only check, backups **outside** `~/auto_xy`, removes installed Klipper extension and any stage 5/6 include **above** Klipper SAVE_CONFIG, and preserves `ratos-variables.cfg` / stored IDEX X/Y/Z offsets.
- Add explicitly confirmed `--purge-project-files` / `--purge-venv` options for local clean-room reinstall testing; NOT automatic, and they delete historical images/scripts.
- Add regression tests for image retention, symlink/unknown-file preservation, SAVE_CONFIG integrity, uninstall and install-include roundtrip. Github Actions now runs these tests.
- No changes to the XY measurement, VAOC movement sequence, correction limits, inline retry or print-mode classification.
- **Offline tested only:** the background timer and clean uninstall / reinstall still require the printer owner's supervised first live test.

# v0.1.0-beta.2 — SAVE_CONFIG installer safety hotfix

**Important:** This fixes a community-reported defect in beta.1's `install_stage6.py`, which could append `[include auto_xy_print.cfg]` **after** Klipper's generated `SAVE_CONFIG` section. That can invalidate the autosave data and cause later SAVE_CONFIG errors. **Do not install beta.1.**

- Install a new include **before** the SAVE_CONFIG marker, preserving all existing `#*#` lines unchanged, or replace the old Auto XY include in place.
- Repair `disable_stage6.py` so restoring the legacy include cannot append below SAVE_CONFIG.
- Add `printer_cfg_editor.py` for shared, idempotent, validated config transformations and atomic writes.
- Installer refuses malformed SAVE_CONFIG blocks, duplicate includes, and unexpected content after the marker; verifies edit before writing any files.
- Add optional `repair_save_config.py --check` / `--repair` for beta.1 users **only** when the tail contains a known misplaced Auto XY include; it backs up printer.cfg before repair.
- Add 14 regression tests, including full mocked installer execution on an existing SAVE_CONFIG config, and add the tests to GitHub Actions.
- **Calibration/print-start algorithms are unchanged** from Stage 6.3.7. No testing on physical hardware of this specific patch yet.

## Previously: Stage 6.3.7

# Stage 6.3.7 — Only independently printed two-tool jobs calibrate

Relative to Stage 6.3.6:

- **Single-head T0 and single-head T1 prints now bypass VAOC**, including when the selected RatOS printing mode is `IDEX_SINGLE`.
- Dual-tool independent prints (`IDEX_SINGLE` with `TOTAL_TOOLSHIFTS > 0`) still run automatic calibration.
- COPY and MIRROR bypass unchanged.
- Classification happens at the early `_USER_START_PRINT` hook, using the original carriage mode **and** the `TOTAL_TOOLSHIFTS` slicer/postprocessor parameter, before RatOS changes carriage state.
- The later after-bed-heatsoak hook consumes the one-use classification and cannot mistake COPY/MIRROR for an independent print.
- The installer additionally confirms installed RatOS `START_PRINT` uses `TOTAL_TOOLSHIFTS` to make the same one-tool vs two-tool decision. If source code differs, installation refuses.
- Existing `AUTO_XY_TOGGLE` bypass, 10 corrections, 6 verification attempts, one immediate inline PRINT retry, 150°C preheat, 155°C camera maximum, and ±0.03 mm tolerance are unchanged.
- Zero or **missing** `TOTAL_TOOLSHIFTS` results in bypass, matching RatOS's `default(0)` behaviour. For a genuine two-tool print, ensure your RatOS slicer postprocessor supplies this count. Calibration cannot infer tool use by looking at the currently selected carriage.

**Testing:** Local offline simulations cover single T0/T1 bypass, COPY/MIRROR bypass, dual independent calibration, stale/missing mode handling, installation preflight, bounded adjustments, retries and fault handling. This release has **not been validated on the physical printer**.
