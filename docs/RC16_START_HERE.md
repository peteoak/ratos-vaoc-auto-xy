# AutoXY RC16 — supervised installation and operation

**Scope:** RC16 on the repository's `main` branch, RatOS 2.1 / native VAOC / IDEX. This is a **supervised release candidate**, not blanket approval for unattended two-tool printing. Exact source versions, printer suitability, rollback and camera commissioning must be verified before any offset writes.

[Current release status](RC16_RELEASE_STATUS.md) · [Diagnostics](RC16_DIAGNOSTICS.md) · [Recommission Nozzles](RC16_RECOMMISSION_MAINTENANCE.md) · [Qualification matrix](RC16_SUPERVISED_QUALIFICATION.md)

## Prerequisites and non-negotiable safeguards

- Printer is physically accessible, idle, not holding/printing a file, and safe for homing and nozzle movements; use the native RatOS VAOC path and correct physical endstops.
- Have a verified **off-Pi backup** of configuration, current `idex_xoffset`, `idex_yoffset`, `idex_zoffset`, existing AutoXY files and commissioned references.
- Confirm the correct RatOS version, camera URL, suitable OpenCV/NumPy binary wheels, clearance and heating/no-ooze behaviour for **this printer**. Installed firmware, printer settings and camera profile must not be silently overwritten.
- Use a real **interactive SSH session** for installs needing `sudo`; do not store credentials in a script or run as an unattended task. Failed status checks are stop conditions.
- Do **not** use the RC15 fresh-only installer from the archived RC15 instructions to upgrade an RC16 installation.

## Choose the installation scenario

| State | What to do |
| --- | --- |
| AutoXY absent | Supervised full RC16 installation with `scripts/rc16_release_install.py`. The initial camera is uncommissioned; commissioning is a separate *physical* prerequisite to correction. |
| Exact reviewed RC15 Dev6 | Same full active RC16 installer, which makes a rollback snapshot and refuses unrecognised variants. |
| RC16 already active | Do **not** rerun the fresh/RC15 upgrade. Check installed source hashes and status first; a recognised, pinned RC16-only refresh is appropriate only when an actual update is needed. |
| Old RC15 package required | Check out [`release/rc15-dev6-frozen`](https://github.com/peteoak/ratos-vaoc-auto-xy/tree/release/rc15-dev6-frozen); its README, source and original manifest remain together. |

### Fresh/RC15-to-RC16 review command

For a **reviewed, pinned RC16 commit** on the correct machine, download/extract the repository in a temporary directory or check out that exact revision. Do not rely on an unpinned moving branch during an installation:

```bash
# Example after an engineer has selected a reviewed revision:
# git clone https://github.com/peteoak/ratos-vaoc-auto-xy.git
# cd ratos-vaoc-auto-xy
# git checkout <reviewed-rc16-commit>
python3 -B scripts/rc16_release_install.py check
```

If the above **read-only** check succeeds and the physical prerequisites have been independently reviewed, the corresponding supervised installation command is:

```bash
python3 -B scripts/rc16_release_install.py install
```

The installer handles saved-state migration, backups, Klipper restart, readiness checks and attempted automatic rollback. It must never be forced through unknown source variants, a pending recovery, a selected file, or an unsafe thermal/motion state. A reported rollback failure means **do not print** until the system has been inspected.

For the complete first-trial details, see [first supervised trial](RC16_FIRST_COMPLETE_TRIAL.md). That document is a dated qualification record, not an invitation to install old vision patches.

### Existing RC16: vision evidence telemetry

The latest revised worker records per-image photo-selection evidence and HTTP 5xx retry history in `report.json`. The existing-system refresh is strictly opt-in and **idle-only**:

```bash
python3 -B scripts/rc16_vision_telemetry_refresh.py check
# Only on a recognised older RC16 camera source, after passing preflight:
python3 -B scripts/rc16_vision_telemetry_refresh.py install
```

**Do not run this as a generic repair operation**, and do not run the older `rc16_vision_refresh.py` or `rc16_vision_reliability_refresh.py` afterward to downgrade current telemetry. If the system is already running the same revision, no update is necessary. If the source hashes are unfamiliar, stop and preserve the evidence.

## Commissioning or recommissioning

A camera calibration is **printer-specific**: the user's camera geometry, both nozzle rim circles and source JPEGs cannot be imported from another printer.

1. After normal safe homing and native VAOC preparation, open `AUTO_XY_RECOMMISSION_NOZZLES`.
2. Position T0 using native VAOC; select `AUTO_XY_RECOMMISSION_CAPTURE_T0` and confirm **three** original JPEG captures.
3. Position T1 in native VAOC; select `AUTO_XY_RECOMMISSION_CAPTURE_T1` and confirm another **three** original JPEG captures.
4. Visually verify the true nozzle at each reference position. Select `AUTO_XY_RECOMMISSION_SAVE`; wait for `AUTO_XY_RECOMMISSION_STATUS` to report **PUBLISHED**.
5. Select `AUTO_XY_RECOMMISSION_FINISH` to leave native VAOC; then perform a supervised measure/print verification before trusting new automatic corrections.

Never accept a false circular feature, infer a nozzle centre from an annotated screenshot, or tighten the user's fixed ROI merely to silence ambiguity. See [maintenance/recommissioning](RC16_RECOMMISSION_MAINTENANCE.md) for the exact Mainsail controls and reference retention rules.

## Normal print behaviour

- **Independent IDEX using both toolheads:** normal RatOS print start is guarded. After physical homing and bed heatsoak, AutoXY measures live camera scale and nozzle XY, applies bounded adjustments, verifies twice within ±0.030 mm on both axes and only then grants a one-use continuation to RatOS startup.
- **One tool only, COPY or MIRROR:** AutoXY calibration is bypassed; follow ordinary RatOS.
- **New print after motors disable:** prior alignment is invalid; fresh home, heatsoak and calibration are mandatory for the next eligible protected print.
- **Stand-alone `AUTO_XY_RUN_NOW`:** no selected/held print is associated with the command, therefore nothing is resumed automatically.
- **Failure:** protected start remains blocked. Follow the specific recovery prompt, one permitted guarded retry where available, or native manual VAOC and explicit `AUTOXY_PRINT_CONTINUE` after positive verification. Do **not** use raw `M24` or generic Resume to bypass a hold; do not clear faults before investigation.

## Known safety limitation

Normal Mainsail Pause/Cancel may be queued while the synchronous AutoXY calibration G-code runs. In a real hazardous situation use Klipper **Emergency Stop**, not an unverified Resume. The [optional asynchronous design](RC16_PAUSE_CANCEL_INTEGRATION_DESIGN.md) is a future proposal, not part of RC16's installed runtime.

## Diagnostics, storage and troubleshooting

Capture the latest `/home/pi/auto_xy/print_gate_runs/*/report.json`, associated original and annotated JPEGs, relevant `~/printer_data/logs/klippy.log` entries, and the installed component hashes when investigating a fault. See [diagnostics](RC16_DIAGNOSTICS.md) for field meanings and a read-only collection example.

The image retention policy has age thresholds and a **soft combined image budget** (512 MiB by current defaults). Briefly exceeding the target is allowed; eligible older, closed-run images are reclaimed when safe. Active reference images, incomplete/unknown run state and selected protected files are not deleted merely to meet a number. Protected generated G-code has separate retention.

## Support and validation

Hardware tests on one printer do **not** establish exact-circle correctness on the beta tester's unusually ambiguous original unannotated images. Missing qualification items appear in [release status](RC16_RELEASE_STATUS.md) and the [supervised test matrix](RC16_SUPERVISED_QUALIFICATION.md).
