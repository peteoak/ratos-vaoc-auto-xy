# AutoXY v0.5.0-rc15-dev6 — storage retention release candidate

**Development pre-release** for RatOS 2.1 IDEX, validated on one RatRig V-Core 4 300 IDEX but not across multiple printers. RC15 dev6 adds bounded artifact retention, eliminates the redundant T0 reload immediately after native `_VAOC_START`, and promotes the owner's selected detector and timing defaults. Protected printing remains opt-in until automatic integration is validated.

## Getting started — fresh installation

**First installation only.** New to SSH, Klipper extensions or camera commissioning? Start with the **[step-by-step fresh installation guide](docs/NEW_INSTALL.md)**. It covers prerequisites, SSH login, backups, camera checks, Python dependencies, preflight, installation, commissioning, first supervised measurement, troubleshooting and rollback.

**Three distinct milestones:** (1) installed and Klipper Ready, (2) camera commissioned after native manual VAOC, and (3) supervised measurement passed and automatic calibration deliberately enabled. Fresh installs start **OFF and uncommissioned**.

Quick reference for an **idle, cool, fresh RatOS 2.1 IDEX printer** with working stock native VAOC and saved tool offsets:

~~~bash
ssh pi@ratos.local
cd /home/pi
git clone https://github.com/peteoak/ratos-vaoc-auto-xy.git
cd /home/pi/ratos-vaoc-auto-xy
python3 -m venv /home/pi/auto-xy-venv
/home/pi/auto-xy-venv/bin/python -m pip install -r requirements-vision.txt
python3 scripts/verify_stock_camera.py
python3 scripts/install_autoxy.py --check --mode fresh
~~~

**Only if preflight passes**, the install command for **RC15 dev6** is:

~~~bash
python3 scripts/install_autoxy.py --install --mode fresh --confirm INSTALL_RC15_DEV6_FRESH
sudo systemctl restart klipper
python3 scripts/post_install_check.py
~~~

**Do not stop here:** follow the [commissioning and measure-only steps](docs/NEW_INSTALL.md#step-9--commission-the-nozzle-camera-using-your-printer) before enabling AutoXY. **Do not use this installer over an existing AutoXY version.** No general-purpose in-place upgrade tool is supported. The public protected-print option remains **disabled** and does not transparently transform ordinary slicer files.

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
