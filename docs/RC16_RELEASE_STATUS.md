# AutoXY RC16 — current release and hardware-qualification status

**Last reviewed: 14 November 2026.** RC16 code was merged into repository default `main` on 14 November 2026 (promotion merge `d0c9c286563c7b3ec3768a3c27f6dfa69b7dc21a`). The latest owner-tested vision/telemetry source was `941464a6c85ab1c0fe2c97b2aadf74b4d11e5f1c` before that merge.

**Release classification: supervised RC16 integration candidate, not yet approved for unattended dual-tool protected printing.** A GitHub main merge is a source-control milestone, **not** a published hardware-qualified release, installer run or demonstration of success on every camera/nozzle.

[Start here](RC16_START_HERE.md) · [Diagnostics](RC16_DIAGNOSTICS.md) · [Outstanding hardware tests](RC16_SUPERVISED_QUALIFICATION.md) · [Historical documents](HISTORICAL_DOCUMENTS.md)

## What has been implemented

- Protected print-start handling for genuinely independent IDEX jobs using **both** tools; single-tool, COPY and MIRROR bypass AutoXY. Original slicer files remain intact; generated protected files include a sanitised original name and Pi-local timestamp, e.g. `__AutoXY_RC16_Dragon_Head_20261113-174530-123456.gcode`.
- Fresh physical homing and heatsoak before calibration for each new eligible print after the motors disable; new camera XY measurement and two consecutive in-tolerance verification cycles (±0.030 mm/axis).
- Held-file failure handling with one-use guarded release after a verified automatic alignment or eligible supervised native VAOC recovery; no slicer postprocessor needed. `AUTO_XY_RUN_NOW` remains a standalone action with no held print to resume.
- RC16-only geometry-first Hough detector. A **unique**, independently geometry-qualified circle is accepted without photo-reference veto; for **exactly two** eligible circles, photographic likeness is a ≤25% tie-break. An unresolved candidate pair fails closed.
- Targeted X/Y scale recapture on >12% T0/T1 disagreement, without changing established XY correction mathematics; original commissioned reference images need not be rewritten solely for this update.
- Fresh bounded HTTP snapshot retries for 500/502/503/504 under the existing timeout, **never** recycling previous JPEGs.
- Per-capture `appearance_evidence`, `camera_http_attempts`, `camera_http_retries`, retry status history, and aggregate `vision_reliability_summary` in `report.json`.
- Automatic idle retention of calibration JPEGs using a **soft** combined default target of about 512 MiB; active, referenced, incomplete and unknown files remain protected. Separate generated-file retention checks preserve selected/paused files.
- Transactional supervised fresh/RC15 installer and narrowly scoped, version-pinned existing-RC16 refresh scripts, with refusal for unknown states and rollback support.

## Verified evidence from real printer reports

The owner supplied six recent AutoXY reports, four with `CALIBRATED_AND_VAOC_EXITED` status and two earlier `FAILED_DO_NOT_PRINT` runs. These are **per-run calibration outcomes**, not proof that each entire print completed. Report times below follow the Pi's own stored timestamps.

| AutoXY run | Automatic calibration outcome | Evidence |
| --- | --- | --- |
| `20261112-112413_print` | Passed | Final residual X −0.01420 mm, Y −0.00099 mm |
| `20261113-071103_print` | Passed | Final X −0.00437 mm, Y −0.00219 mm |
| `20261113-073940_print` | Failed closed | T0 photo likeness 0.427/0.511 was above then-hard 0.380; held print and native manual recovery path exercised |
| `20261113-074348_print` | Failed closed | T0/T1 X camera-scale disagreement 12.74% exceeded 12%; no unsafe correction authorised; manual native VAOC continuation subsequently observed |
| `20261113-092317_print` | Passed | Two adjustments; final X +0.02260 mm, Y −0.01364 mm; two independent verification checks |
| `20261113-231235_print` | Passed | One adjustment; final X +0.00761 mm, Y −0.00736 mm; 43/43 accepted frames geometry-only, **zero photo tie-breaks and zero HTTP retries**, two verification checks |

This is concrete evidence of successful guarded calibrations, a fail-closed camera detection path, a scale disagreement refusal and an operator-recovered manual VAOC continuation on the owner's specific printer. It does **not** establish that targeted scale recapture works on real hardware (it was not needed in the most recent accepted run), that the camera HTTP retry path has been exercised physically, or that the beta tester's unusually ambiguous scene is handled correctly.

## Automated checks

The latest pre-merge RC16 source CI completed **370 tests, 5 skipped**. CI runs without the target physical MCU, printer endstops, camera geometry, Beacon and RatOS workload; it does not replace live verification. The additional documentation/manifest checks are tracked separately from camera and motion qualification.

## Known limitations and work still required

1. **Beta tester's original nozzle scenes:** obtain and replay the **unannotated** source images and a true commissioned geometry profile. Confirm the actual nozzle is selected and the false circle refused. An overlay or owner's successful scenes are not a substitute.
2. **Safe recovery and bypass matrix:** complete negative-path hardware testing, including direct virtual-SD entry points, wrong file/reprint, resume during hold, abort/rehome, offset invalidation and repeated two-tool prints after steppers disable. The successful native manual VAOC recovery is valuable but not an exhaustive matrix.
3. **Storage/retention hardware evidence:** verify scheduled cleanup, soft-quota pressure, selected-file exclusions, disk-full and recovery behaviour on the actual Pi while protecting original reference JPEGs and user G-code.
4. **Per-machine dependencies and physical commissioning:** confirm OpenCV/NumPy wheels on supported Pi architecture/Python/OS combinations, actual native VAOC capture framing, reticle position, nozzle wear and measurement repeatability.
5. **Release packaging:** maintain an RC16-specific source inventory and release docs; verify all required source hashes/links and compatibility with the archived RC15 manifest. No blanket unattended-use release tag or certification is implied.
6. **Accepted Pause/Cancel limitation:** ordinary Mainsail Pause/Cancel may queue during synchronous camera calibration. The owner accepts this as a known limitation, not a blocker for supervised RC16. Use Emergency Stop for unsafe motion; do not attempt a raw Resume afterward. The [asynchronous alternative](RC16_PAUSE_CANCEL_INTEGRATION_DESIGN.md) is optional future work.

### Current installation rules

- A new AutoXY-free or exactly recognised RC15 installation may be assessed with `scripts/rc16_release_install.py check`, followed by `install` **only in a supervised, safe-idle review**. Do not bypass recognition failures.
- An already active RC16 installation must **not** rerun the RC15/fresh installer. The latest narrow telemetry updater is `scripts/rc16_vision_telemetry_refresh.py`; only run its `check` and then `install` if exact source/version and idle-state checks justify it.
- Historical `rc16_vision_refresh.py` and `rc16_vision_reliability_refresh.py` record intermediate revisions; do not apply them on top of the current telemetry revision.
- A main-branch documentation change is not a reason to reinstall or disturb a working supervised printer.

### RC15 and RC16 release records

The exact RC15 Dev6 release remains available on [`release/rc15-dev6-frozen`](https://github.com/peteoak/ratos-vaoc-auto-xy/tree/release/rc15-dev6-frozen). Its original README and manifest are also copied to `docs/legacy/` for provenance. The **root `MANIFEST.json` exists for RC15 compatibility checks**, not as a claim that RC16's current files are in the historical RC15 archive. See [current source inventory](RC16_SOURCE_MANIFEST.json) and [historical index](HISTORICAL_DOCUMENTS.md).
