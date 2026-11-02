# AutoXY v0.5.0-rc15-dev6 — storage retention release candidate

**Development pre-release** for RatOS 2.1 IDEX; not validated on multiple printers. This candidate adds bounded calibration-artifact retention to the previously owner-tested RC15 dev5 calibration engine.

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

Do **not** use the fresh installer as an upgrade or uninstall command. An existing dev5 installation requires a separate, verified retirement and restore plan before testing a clean GitHub reinstall. Preserve camera reference photographs and commissioning configuration outside any cleanup-managed results directory.

## Validation

Run offline tests:
```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Hardware validation remains limited to the previously reported supervised 25, 50 and 100-tool-change dev5 runs on one RatRig V-Core 4 300 IDEX. No dev6 printer-run qualification is claimed. Automatic XY runs after bed heatsoak and before later Beacon probing; fresh homing and measurement must be respected after motors disable.

## Attribution

Independent AutoXY project, unaffiliated with RatOS. See `THIRD_PARTY_NOTICES.md` for the outstanding upstream macro provenance and licence review before public redistribution.
