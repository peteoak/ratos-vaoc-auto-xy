# AutoXY v0.5.0-rc15-dev6 — storage retention release candidate

**Development pre-release** for RatOS 2.1 IDEX, validated on one RatRig V-Core 4 300 IDEX but not across multiple printers. RC15 dev6 adds bounded artifact retention, eliminates the redundant T0 reload immediately after native `_VAOC_START`, and promotes the owner's selected detector and timing defaults. Protected printing remains opt-in until automatic integration is validated.

## Storage retention

Default retention (age measured from the closed run's report modification time):
- Successful calibration photographs: 7 days; 256 MiB category cap.
- Failed calibration photographs: 30 days; 512 MiB category cap.
- Diagnostic photographs: 7 days; 512 MiB category cap.
- Endurance exports: 30 days; 128 MiB category cap.
- `report.json`: 90 days; 128 MiB category cap.

A cleanup worker runs independently of calibration. Failed or incomplete runs, unknown files, symlinks and unexpectedly modified files are protected by conservative ownership and status checks. Latest failure runs are protected from quota pressure, but not age expiry. Quotas are best-effort when protected data cannot be removed. Cleanup errors never authorise printing or alter saved offsets.

Preview files that *would* be deleted, without deleting any files:
```bash
python3 src/auto_xy_retention.py --results /home/pi/auto_xy/print_gate_runs
```
Destructive cleanup is opt-in on CLI with `--run`; it is also scheduled by the extension under its safety gates. Inspect preview before using CLI deletion. Defaults can be configured using the `retention_*` settings in `config/auto_xy.cfg.example`.

## Installing (fresh systems only)

**Do not install over an existing AutoXY.** Back up full RatOS configuration, camera commissioning and offsets; verify native VAOC, power and homing, heaters idle and printer cool. A guarded fresh installer performs preflight and refuses existing AutoXY.

```bash
python3 scripts/install_autoxy.py --check --mode fresh
```

After review and satisfactory preflight:
```bash
python3 scripts/install_autoxy.py --install --mode fresh --confirm INSTALL_RC15_DEV6_FRESH
sudo systemctl restart klipper
```

Follow `docs/NEW_INSTALL.md` for camera commissioning and supervised tests. This installer does not overwrite `ratos-variables.cfg` or saved offsets and uses a single `[include auto_xy.cfg]` in `printer.cfg`.

## Existing installations

Do **not** use the fresh installer as an upgrade or uninstall command. The owner successfully completed a backed-up quarantine, fresh GitHub reinstall and subsequent reviewed controller/config update. There is still **no general-purpose supported in-place upgrade/uninstall tool**; existing installations require a machine-specific reviewed procedure. Preserve camera reference photographs and commissioning configuration outside any cleanup-managed results directory.

## Validation

Run offline tests:
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Dev5 completed supervised 25-, 50- and 100-tool-change endurance tests on one RatRig V-Core 4 300 IDEX. RC15 dev6 was successfully installed from GitHub on that printer; Klipper reached Ready, the camera stream and restored commissioning profile were verified, and the corrected controller matched the source checksum. The owner ran 39 offline tests (34 passed, 5 skipped), and GitHub Actions passed on the corrected source commit. **The first physical calibration after removing the duplicate T0 load, live retention expiry/deletion, and complete failure/recovery-to-print continuation have not been verified in this release.** Automatic XY runs after bed heatsoak and before later Beacon probing; fresh homing and measurement must be respected after motors disable.

## Validated machine defaults

`config/auto_xy.cfg.example` now defaults to `camera_detector_mode: two_stage_roi`, persistent worker, original settling path, 0.80-second camera pair gap and settling time, commissioned detector guard enabled, and `protected_print_enabled: False`. AutoXY's saved enable state is still deliberately OFF on a clean installation until operator commissioning and enablement. Ordinary RatOS START_PRINT remains available with AutoXY enabled. Experimental protected-print support requires the separately prepared two-phase print-file path; enabling its config flag alone does not automatically convert ordinary slicer files. Do not turn it on for ordinary unmodified print files.

## Attribution

Independent AutoXY project, unaffiliated with RatOS. See `THIRD_PARTY_NOTICES.md` for the outstanding upstream macro provenance and licence review before public redistribution.
