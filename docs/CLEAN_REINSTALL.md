# Safe clean uninstall and reinstall on the original printer

**Experimental beta; supervised only.** This document gives the owner a true **clean software installation** test without wiping their existing RatOS X/Y/Z nozzle offset calibration.

**What "all files" means here:** the optional purge deletes the entire `~/auto_xy` tree (including previous stages, images and diagnostic history) and `~/auto-xy-venv`. The uninstaller **never deletes or edits** `/home/pi/printer_data/config/ratos-variables.cfg`, because that file contains RatOS's live saved IDEX offsets and other settings, not just Auto XY. The preserved variables are explicitly backed up outside the deleted project tree.

## 0. Preconditions

- A completed print; printer safely idle and ready, no print paused.
- Camera/VAOC **not active**; no unresolved Auto XY fault or interrupted-run marker.
- Nozzle and bed movements stopped, printer in a safe state.
- Mainsail: run `AUTO_XY_OFF`, `AUTO_XY_STATUS`, and `AUTO_XY_HEALTH` before uninstall.
- Have a separate backup of the Pi printer config (including `printer.cfg` and `ratos-variables.cfg`). Never blindly restore stale offsets during a print.
- Ensure new beta.3 files are extracted **outside `~/auto_xy`**, e.g. `/home/pi/autoxy_beta3_release/`.

If Klipper is halted, or a fault/active VAOC prevents the safety preflight from passing, **do not bypass the checks**. Recover safely first; an expert may need to review the configuration offline.

## 1. Upload beta.3 to a safe staging location

Using WinSCP upload the **contents of the extracted installer ZIP** to:

```text
/home/pi/autoxy_beta3_release/
```

Ensure `uninstall_auto_xy.py`, `printer_cfg_editor.py`, `install_stage6.py`, `auto_xy_gate.py`, `auto_xy_retention.py` and `requirements-vision.txt` appear together.

## 2. Preview everything that the full reset will remove

SSH:

```bash
cd /home/pi/autoxy_beta3_release
python3 uninstall_auto_xy.py --check --purge-project-files --purge-venv
```

This is **read-only**. Review the planned deletion of `/home/pi/auto_xy` and `/home/pi/auto-xy-venv`. The first contains ALL old images, previous stages and existing diagnostic history. Export anything you want to retain before proceeding. Backups and saved-variable file are outside this tree.

## 3. Uninstall and fully purge the old Auto XY runtime

**This step permanently deletes your old images/history and virtual environment.** Only perform it if you have made your own backups and the printer is confirmed idle, with AUTO XY OFF, no VAOC session and no unresolved faults.

```bash
python3 uninstall_auto_xy.py --uninstall --purge-project-files --purge-venv --confirm-purge DELETE_AUTO_XY_DATA
```

What this does:

1. Asks live Moonraker/Klipper whether the printer is idle and Auto XY OFF, with VAOC inactive and no outstanding Auto XY safety fault.
2. Creates a timestamped directory under `/home/pi/auto_xy_uninstall_backups/`, copying `printer.cfg`, `ratos-variables.cfg` (if present), existing Auto XY config and Klipper extension **before any deletion**.
3. Removes the known Auto XY include(s) **above** Klipper's `SAVE_CONFIG` block, preserving all `#*#` lines byte-for-byte.
4. Removes the known Auto XY Klipper extension and configuration files.
5. Deletes `/home/pi/auto_xy` and `/home/pi/auto-xy-venv`, including historical diagnostic photos.

**It does not alter or reset saved `idex_xoffset`, `idex_yoffset`, `idex_zoffset`.** Existing values stay in RatOS's save-variables file. If the CLI refuses, stop and investigate; don't bypass its safeguards.

## 4. Restart Klipper and verify base RatOS

Immediately restart **Klipper** in Mainsail while the printer remains idle. It should start **without** Auto XY macro buttons because the integration is now removed. Check that normal RatOS and VAOC macros still load; do not invoke VAOC motion just to check.

Confirm backups still exist:

```bash
ls -ld ~/auto_xy_uninstall_backups/*
```

Do not delete these until you have validated the new installation.

## 5. Fresh dependency setup

Still in `/home/pi/autoxy_beta3_release/`:

```bash
python3 -m venv ~/auto-xy-venv
~/auto-xy-venv/bin/python -m pip install --upgrade pip
~/auto-xy-venv/bin/python -m pip install -r requirements-vision.txt
~/auto-xy-venv/bin/python -c 'import cv2,numpy; print(cv2.__version__,numpy.__version__)'
```

If required Python wheels are unavailable on your Pi architecture, stop and document the issue. Don't modify Klipper's Python environment.

## 6. Run offline tests and check the fresh installer

```bash
python3 test_config_editing.py
python3 test_retention_uninstall.py
python3 test_stage6_offline.py
python3 install_stage6.py --check
```

`--check` must report PASS, including the correct RatOS macro order and idle/OFF status. If it fails, **do not install**.

## 7. Install the clean beta.3 release

```bash
python3 install_stage6.py --install
```

Restart Klipper from Mainsail. Confirm:

```gcode
AUTO_XY_HEALTH
AUTO_XY_STATUS
AUTO_XY_OFF
```

The package must report the correct limits (10 corrections, 6 verifications) and allow the `AUTO_XY_TOGGLE` Mainsail button. Verify the `printer.cfg` include remains **above SAVE_CONFIG**. Preview retention:

```bash
python3 ~/auto_xy/stage6/auto_xy_retention.py --check
```

## 8. Supervised validation sequence

- With AUTO XY OFF, run a disposable test print and confirm the usual RatOS startup with no VAOC movements.
- With AUTO XY ON, confirm COPY (and then one-tool jobs) skip Auto XY, with saved offsets unchanged.
- Finally run an independent two-tool test print, supervising nozzle clearance, heatsoak, 150 C preheat camera calibration, VAOC exit, Beacon contact probing and normal printing.
- Compare resulting camera-reported alignment against a physical print if needed.
- Do not deliberately force a mechanical/camera failure solely to test the retry.

Report hardware results and any installer/uninstall refusal in GitHub Issues, removing IP addresses and private details.

## If anything goes wrong

- **Stop further prints.** Retain the backup folder at `~/auto_xy_uninstall_backups/<timestamp>/`.
- If the new extension prevents Klipper starting, use the safe disable/repair instructions in `docs/INSTALL.md` or restore the backed-up `printer.cfg` plus Klipper extension appropriate to that saved configuration. Do not restore a config include whose referenced extension is missing.
- Do not overwrite `ratos-variables.cfg` on a live printer or restore old nozzle offsets unintentionally.
- A clean reinstall is not a factory reset of RatOS calibration. It changes software files, not your established XY alignment values.
