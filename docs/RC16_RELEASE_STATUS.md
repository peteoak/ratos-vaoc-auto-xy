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

## 27 November 2026 — retire active legacy Trial04 file interception

Observed a COPY-mode job (`TOTAL_TOOLSHIFTS=0`) that was staged as
`AutoXY_...gcode` and printed with the message
`AutoXY Recovery: print staged via RatOS`. The printer-supplied exact
source `/home/pi/auto_xy/klipper/auto_xy_local_recovery.py` is Trial04,
SHA256 `12ddf4100b6b61cbb71f3cc1b26d0d87e2134a8c9cbdb0385f83aba50e11c03f`.
Its Klipper extension path is a symlink to the same source, and
`printer.cfg` includes `auto_xy_local_recovery.cfg`.

Trial04 registers `PROCESS_GCODE_FILE` and stages ALL prints. The new
RC16 coordinator instead delegates COPY/MIRROR directly to the native
RatOS print processor, with no AutoXY file generation or worker.
Both extensions can register the same command: leaving Trial04 active
can shadow RC16 depending on ready-handler order, even though the
new RC16 extension itself reports `armed`.

The supervised `scripts/rc16_retire_trial04_recovery.py` migration
requires the exact reviewed legacy code/symlink, precisely one include,
RC16 active with no print, paused job, VAOC or fault, and checks
protected SD/resume API guards *after* one Klipper restart. It comments
out **only** the Trial04 include in `printer.cfg`, creates an exact
rollback snapshot and restores the original on postflight failure.
The Trial04 Python source, symlink, legacy config, user offsets,
camera references, RatOS software and G-code files are left in place.
The older `AUTOXY_RECOVERY_STATUS` command is no longer exposed
after successful retirement; `AUTOXY_RC16_HANDOFF_STATUS` is used instead.
Supervised COPY/MIRROR and independent T0/T1 tests remain necessary.

## 26 November 2026 — T0 preflight false-rejection from annular global peak

The 25 November T0 diagnostic bundle contains two 1920x1080 normally
illuminated snapshots from `20261125-090219_print`. The detector's
commissioned T0 radius is 151.25px, centre (960.625,546.875)px. Reproduction
with the production Hough and annular parameters detects unique precision
circles at approximately (984.4,511.9,r=147.1) and
(979.4,509.4,r=152.6)px. The global coarse annular matcher instead chose
(988,564) and (1052,540)px, 52.2px and 78.8px away, above the existing
18px cross-check limit. Both Hough candidates satisfy the original
commissioned centre/radius constraints; local annular correlations near
those candidates independently locate peaks within about 8.4px / 4.3px,
with correlation >=96% of the global winner.

The experimental `two_stage_roi` detector now retains its original
global cross-check as the normal path. Only on a normal-lit rejection will
it retry unique Hough circle detection without the global coarse gate,
then independently demand a local annular match within the **original
18px** tolerance, original minimum correlation, and >=96% of global
coarse strength. The precision centre is still entirely Hough-derived.
Low-light paths, commissioned centre/radius limits, two-circle ambiguity,
worker identity checks and offset-write safety are unchanged. This is
not proof of micrometre-scale physical accuracy; confirm on the printer.

An existing reviewed RC16 installation can apply this **detector-only**
change using `scripts/rc16_local_annular_refresh.py check` then
`install`, which requires no active/held print, VAOC or fault. Source
and existing RC16 controller pins, rollback snapshot, readiness and
automatic rollback are mandatory. Do not run the historical vision updater
against current main; its original source hashes remain frozen.

## 24 November 2026 — cancelled-print lifecycle fix (historical updater superseded)

An observed cancellation left a protected-print session in `PREPARING` and
the next start was refused by the stale direct-resume guard, despite Mainsail
showing Standby. The RC16 coordinator now observes native
`virtual_sdcard:reset_file` and also checks for a terminal/unloaded
previous job before new print processing. It retires the old in-memory
protected session, its retry context and generated-file ownership lock **only**
when print_stats is terminal, virtual SD is inactive and has no file loaded,
native VAOC is not running, and neither the coordinator nor gate is busy.
Paused, selected, printing or recoverable sessions remain protected. No
saved offsets or persistent variables are modified.

**Superseded:** Do not run the cancellation-only updater from current main; the combined `rc16_operator_enable_refresh.py` is the current reviewed update covering cancellation cleanup and operator-owned ON/OFF behaviour. Historically a previously installed RC16 used the **one-time**, idle-only
`scripts/rc16_cancel_cleanup_refresh.py check` then `install` upgrade;
the installer checks exact source versions, restarts Klipper and verifies
readiness with automatic rollback on failure. After installation, ordinary
cancels and subsequent prints require **no manual restart**. Actual hardware
cancellation and next-print behaviour must still be verified in a supervised
test. Do not use the fresh/RC15 installer on an existing RC16.

## 24 November 2026 — fresh calibration after safely cancelled no-write failure

A failed calibration must never release its original protected two-tool file
without verified alignment. RC16 now distinguishes a **certified no-write**
failure from an uncertain calibration/offset-write failure.

On the existing protected-print path, the gate certifies a no-write failure
ONLY after checking no corrections, no saved write intents, unchanged baseline
offsets and retained fault/interruption markers. If the operator cancels the
held print, the virtual-SD file is fully unloaded, the status is terminal,
and native VAOC has been **safely exited**, the handoff retires that session
and stores a same-process, one-use certificate. The fault markers remain
latched UNTIL the next independent two-tool start. On that request, it checks
the exact saved baseline again, confirms the file remains unloaded and VAOC
closed, and only then clears the transient fault/interruption flags for a
**full fresh print-start calibration**. The old G-code is NEVER resumed and
no saved offsets are changed by this rearm.

An attempted correction or changed offsets, invalid/used retry, open VAOC,
paused/selected file, changed operator ON/OFF preference, or lost in-memory
certificate (such as after RESTART) **cannot** auto-rearm. Such cases retain
the manual safety acknowledgement path. Single-tool, COPY and MIRROR paths
remain unmodified. Hardware validation is still required.

Use the dedicated existing-install updater
`scripts/rc16_safe_no_write_refresh.py check` then `install` on an idle,
unselected, fault-free existing **operator-enable RC16**. It is exact-hash
pinned to the previous main gate and handoff; unknown installations refuse.
The updater makes a rollback snapshot and needs one Klipper restart to load
code. Normal recoverable failures afterwards do not need a restart.

## 24 November 2026 — persistent ON/OFF user ownership

A new report showed `AUTO XY: OFF; FAULT=0; IN_PROGRESS=0` when
a reprint was attempted; the two-tool start was safely refused but only
displayed the generic `cannot authorise independent dual-tool print`.
The previous gate's failure handler and retry-dismiss macro both wrote
`auto_xy_v2_enabled=0`, unexpectedly changing the operator's choice.

The RC16 controller and macro now preserve the saved user ON/OFF setting
on every calibration failure, retry, retry-dismiss and cancellation.
The **persistent FAULT/IN_PROGRESS interlocks remain mandatory**, and a
protected two-tool file is never permitted to print while a fault remains
latched. A distinct print-start error now explains `OFF`, `FAULT` or
`BUSY` and the safe corresponding next step.

A dedicated, exact-hash existing-RC16 updater
`scripts/rc16_operator_enable_refresh.py` changes the controller, handoff
and retry-dismiss macro atomically with verified snapshots/rollback. An
actual printer run is still needed to verify post-cancellation/next-print
behaviour and retry UI interactions. **Do not confuse an ON preference with
successful calibration authorization.**

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
