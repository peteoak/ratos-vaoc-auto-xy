# Safety and prerequisites — read before installing

## High-risk operations

This project controls a real IDEX machine. It moves both carriages into a camera work zone and persists X/Y changes in RatOS `save_variables`. A failure can cause an impact, nozzle/camera damage, unsafe head travel, lost calibration or an invalid print. Supervise initial trials, maintain access to the machine's normal hardware emergency-stop controls, and **do not use on an unattended production job during beta evaluation**.

The built-in physical rules are not a substitute for verifying your machine's exact camera position, head geometry, parking clearances, VAOC macro behaviour, and print-start sequence. The default detector is intentionally tuned to one camera and must be reviewed for other hardware.

**Never** use this beta if you cannot reliably perform normal RatOS VAOC by hand or if nozzle tips are visibly wet/oozing over the camera. At 150°C some polymers can soften/ooze even if their nominal extrusion temperature is higher.

## Required before enabling AUTO XY ON

- Compatible RatOS 2.1 IDEX build; **run `install_stage6.py --check` and respect any refusal**.
- Idle, homed printer; correctly configured T0 and T1; existing user-proven VAOC camera position.
- A stable, fixed USB camera; complete JPEG snapshots through the camera URL *from the printer Pi*; reliable lighting and nozzle detection. Current software assumes image geometry near 1920×1080 / circle-radius 150px.
- Camera and carriage clearance confirmed physically for each tool's reference/sweep; safe ±0.23mm calibration moves around the VAOC position do **not** imply the entire route to the camera is safe.
- RatOS VAOC correctly configured for offset saves; manual verification of offset sign and a printed two-tool test.
- Clean, preferably non-oozing nozzles during camera visits; camera lens physically shielded as required.
- Correct slicer `TOTAL_TOOLSHIFTS` count, not guessed from tool selection.
- Saved backups of `printer.cfg`, saved RatOS variables, and existing custom hooks. Never publish these files unredacted.
- Optional custom `_USER_START_PRINT` or `_USER_START_PRINT_AFTER_HEATING_BED` macros must be reviewed and manually merged. The installer refuses conflicts rather than overwriting them.

## Fault behaviour

- Master OFF skips calibration, **not** an unresolved safety fault.
- Print-start calibration runs before Beacon contact probing at up to ~170°C on the known setup. The auto-XY camera phase itself limits actual temperature (default 155°C) and requested target (150°C).
- A recoverable camera/verification problem can get one **automatic inline retry** while VAOC remains active and print start remains in its same call; success is required before printing proceeds.
- Thermal, motion, uncertain-offset or unexpected Klipper failures must abort rather than retry.
- A fault can leave VAOC active. Do **not** blindly home, `RESTART`, force a second print, press ACKNOWLEDGE, or restore variables without reviewing the pose and runtime/saved compensation.
- Saved-offset rollback does not prove the active Klipper named offset was restored. Verify physical/toolhead state before printing.
- `AUTO_XY_ACKNOWLEDGE` is a manual acknowledgement **after** review, not an automatic repair.

## Privacy of diagnostics

The logs and JPEG images may contain local paths, machine details, firmware versions, IP addresses, camera locations, names or other metadata. Do not publish `printer.cfg`, `ratos-variables.cfg`, SSH output with secrets, Moonraker tokens or private webcam URLs. Copy only necessary redacted text into GitHub Issues. The `.gitignore` blocks common reports/images but is not a security guarantee.

## Beta policy

No warranty of correctness, hardware safety, print quality or supported model coverage. **A successful image metric is not a substitute for a physical two-tool alignment print.**
