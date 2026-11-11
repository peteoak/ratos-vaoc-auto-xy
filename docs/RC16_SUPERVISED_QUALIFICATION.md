# RC16 supervised RatOS printer qualification

**Status:** Development qualification checklist, not installation approval.
**Target:** RatRig V-Core 4 300 IDEX, RatOS 2.1, Beacon, fixed VAOC camera, physical homing switches.
**Installation contract:** One supported active-only upgrade, automatic print-file processor and storage cleanup, protected gate enabled, automatic rollback on startup failure. No slicer postprocessor and no disabled-staging workflow.

## Before physical motion (read-only / controlled upgrade)

- Verify the installed RC15 controller matches a reviewed variant and preserve the exact pre-upgrade files, saved offsets, camera commissioning, Klipper registration and config.
- Confirm idle/cool state, native VAOC not active, required vision interpreter and camera, sufficient backup space.
- Upgrade once under active installer; require Klipper Ready, `protected_print_enabled=True`, handoff `enabled=True`, `armed=True`, `sd_api_guarded=True`, `resume_api_guarded=True`, generated retention active and saved `auto_xy_v2_enabled=1`, `measure_only=0`. If verification fails, restore RC15 and restart automatically.
- Confirm source-identical manual VAOC and exact nozzle offsets after successful upgrade. Native RatOS originals remain untouched.

## Supervised print matrix

1. **Single tool:** Normal upload/start, native processing and print. No camera calibration and no unnecessary generated AutoXY recovery gate.
2. **COPY:** Same bypass, no independent XY calibration or AutoXY offsets changed.
3. **MIRROR:** Same bypass, no independent XY calibration or AutoXY offsets changed.
4. **Independent dual tool (success):** Home from disabled motors, heatsoak, capture/measure/correct/verify T0+T1 before Beacon/probing/extrusion. Original file stays source-of-truth and is protected by generated-file identity.
5. **Second independent print:** Disable motors after first completion; require NEW homing and fresh camera verification, never reuse prior verified session, even if old `auto_xy_v2_reuse_enabled=1`.
6. **Automatic calibration failure:** Hold original prepared file after heatsoak, maintain heaters and recoverable SD state; offer one guarded retry or native manual VAOC fallback. No unguarded ordinary start, no postprocessor.
7. **Manual VAOC recovery:** Native positioning/adjustment, explicit acceptance, proof of unchanged home/motors/tool/file/offset state, release only via `AUTOXY_PRINT_CONTINUE`, single-use guard consumed before printing resumes.
8. **Bypass resistance during held recovery:** Raw M24, direct `do_resume()`, M23, M26, direct BASE and direct virtual-SD start must not cross calibration gate. Native M24 must remain usable for unrelated normal pauses.
9. **Recovery invalidation:** Motor disable, rehoming, file substitution, elapsed hold timeout, fault flag or active VAOC must block continuation without corrupting saved offsets.
10. **Print end and cleanup:** Motors disable as configured; image cleanup automatically runs only when idle, counts raw/annotated/unknown image bytes against soft combined quota, deletes only eligible known images; generated G-code cleanup must not delete selected/paused/printing files.
11. **Rollback drill:** In a dedicated supervised safe-idle window, inject a non-motion startup verification failure offline or through a reviewed harmless test fault and confirm exact previous file registration, offsets, settings and service readiness. No unknown live motion faults for this drill.

## Exit criteria

- All non-hardware offline tests pass and no safety-critical skips remain.
- Every matrix case has timestamped observed evidence and a pass/fail result.
- No unsupervised use before successful physical qualification.
- A failure stops release; do not alter the user's printer by hand to force the path. Resolve root cause and rerun only the affected matrix and its dependencies.
- Keep the final installation single-command and the normal user-facing runtime automatic. Do not ask users to reconfigure RatOS or run multiple scripts per print.

## Evidence to collect in a single supervised session

Timestamped Klipper/Moonraker logs, source and prepared-file identity hashes (not G-code contents), guarded phase transitions, native VAOC status, homing/motor state, saved and runtime XY/Z offsets before/after, image/retention totals, and the active-install receipt. Redact private paths and unrelated filenames before sharing.
