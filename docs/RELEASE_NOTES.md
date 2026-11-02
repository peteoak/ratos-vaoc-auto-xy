# AutoXY v0.5.0-rc15-dev6 — release candidate

## Changes since dev5

- Bounded retention for successful calibration images (7 days/256 MiB), failed images (30 days/512 MiB), diagnostic images (7 days/512 MiB), exports (30 days/128 MiB) and reports (90 days/128 MiB). Cleanup is conservative around incomplete, unknown, symlinked and modified data.
- Removed the redundant explicit `_VAOC_LOAD_TOOL T=0` immediately after native `_VAOC_START`, which already selects T0. The controller still records and verifies the initial T0 pose; subsequent necessary T0/T1 load operations remain.
- Updated default configuration: `two_stage_roi` detector, persistent camera worker, original settling path, 0.80-second pair gap and settling delay, commissioned detector guard enabled, protected-print gate enabled. Fresh-install calibration enablement still requires operator commissioning and activation.

## Verification and deployment

- Source merged into GitHub `main` through PR #6. Package manifest integrity verified against the corrected GitHub source and on the printer.
- GitHub Actions passed Python compilation and unit tests on corrected source commit `d4e0b20b8f1bbe018653462e69baecc9db7e0f28`.
- Owner's RatRig V-Core 4 300 IDEX: successful backed-up fresh install, subsequent reviewed controller/config update, Klipper Ready, working USB camera snapshot, compatible restored commissioning file and identical saved offsets before/after the controller update.
- Local unit tests: 39 run, 34 passed, five skipped. Historical dev5 endurance runs of 25/50/100 tool changes are not dev6 hardware tests.

## Remaining qualification

The first physical calibration with the T0 fix, live scheduled retention expiry/deletion, and end-to-end failed-calibration/manual VAOC/held-file recovery remain unverified. Protected printing needs a properly prepared two-phase print file; its default configuration does not automatically transform ordinary slicer output. No general in-place upgrade/uninstaller, multi-printer certification or fixed absolute-nozzle GUI is provided. Upstream RatOS macro licensing and attribution remain open; see `THIRD_PARTY_NOTICES.md`.
