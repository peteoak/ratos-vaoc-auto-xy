# AutoXY v0.5.0 RC16 Dev1 — release/qualification record

**Development only. Not a released, printer-qualified package.**

This is the integrated RC16 development line, in [draft PR #8](https://github.com/peteoak/ratos-vaoc-auto-xy/pull/8). It is deliberately separate from the published RC15 Dev6 main branch. **No change to stock RatOS is required.**

## What is implemented

- Existing RC15 camera/calibration controller retained and versioned as `src/auto_xy_gate_rc16.py`; the held-print 45-minute recovery window starts **when M25 actually holds the print**, rather than when bed heatsoak begins. Wrong file, motor-off, rehome, timed-out session, missing VAOC completion, and unverified offsets still fail closed.
- Native RatOS 2.1 postprocessor remains unrestricted, using its own reactor-cooperative subprocess. AutoXY-owned copy, scan, hash, transform and reprint registry work run in an external `nice +10` Python worker; Klipper polls process status cooperatively.
- Only **independent two-tool** IDEX G-code with positive tool shifts gets protected AutoXY two-phase startup. Independent single-tool receives normal `START_PRINT`, but is still staged/verified. COPY/MIRROR bypass **all AutoXY worker processing** and call the original RatOS handler.
- Generated-print reprints are verified, resolved to their original source and freshly staged, never postprocessed twice. Missing/altered source registry or generated-file tampering prevents a reprint.
- Known direct G-code bypass routes `SDCARD_PRINT_FILE_BASE`, `M23`, and `M26` are blocked while the coordinator is armed; public `SDCARD_PRINT_FILE` still routes normally through RatOS. `M24` stays available for the held-file recovery controller, whose next-file command guards continuation.
- RC16 fresh-venv compatibility utility has pinned, binary-only trial requirements (`numpy==1.26.4`, `opencv-python-headless==4.11.0.86`), with version/import/function smoke checks and refusal to overwrite an existing environment.
- `scripts/rc16_stage_upgrade.py` stages the **exactly recognised** installed RC15 Dev6 gate, new worker, handoff, and automatically scheduled generated-G-code retention worker. The new config stays **enabled: False**, the existing RC15 config remains unchanged, and every modified/added file is recorded for exact rollback.
- **Automatic generated-file retention is enabled by default when the RC16 handoff is armed.** A reactor timer checks eligibility after an initial 120-second delay and subsequently about every 24 hours; a separate `nice +10` cleanup subprocess performs bounded batches of up to 10 files per pass, with checks for idle/unselected virtual SD, a nonblocking exclusive cleanup lock, at least 30 days of age for both generated output and its authenticated registry, a checksum match, and repeated printer-idle checks. Originals, recent files, unknown or altered files, symlinks and unregistered failed stages are never removed. The standalone CLI remains dry-run by default unless `--execute --confirm ...` or the internal `--auto` flag is specified. Results are recorded in `retention-last-run.json` without listing filenames. **An RC16 installation staged with `enabled: False` does not run the scheduler until the handoff is explicitly qualified and armed.**

## Fresh installation versus upgrade

**Existing RC15 Dev6 installation:** the RC16 staging utility only accepts an exact, unmodified RC15 Dev6 controller and a disabled protected-print configuration. It refuses unknown local patches, active VAOC, prints, hot heaters, faults and uncertain registration, rather than guessing whether a migration is safe.

**First-time tester:** `scripts/rc16_fresh_install.py` now provides a transactional, **default-OFF** fresh installer by reusing the vetted RC15 Dev6 fresh installation process, then staging the pinned RC16 extras before restarting Klipper. Its rollback receipt ties together BOTH snapshots. It requires a separately prepared, verified NumPy/OpenCV vision environment and has only passed offline simulation; it is **not yet a supported public quick-start installer**.

Read-only fresh-host preflight for a reviewed, cool and idle, genuinely clean RatOS 2.1 IDEX machine:

```bash
python3 scripts/vision_env.py --check --venv /home/pi/auto-xy-venv
python3 scripts/rc16_fresh_install.py --check
```

The fresh installer requires explicit `FRESH_RC16_DEV1_DISABLED` approval for installation and `ROLLBACK_FRESH_RC16_DEV1` plus its own snapshot for rollback. **Do not run those commands on a production printer before supervised qualification.**

No command in this document enables automatic file interception. Activation requires a reviewed printer-specific qualification plan and a known-working native VAOC/manual recovery route.

## Safe read-only acceptance checks for a previously commissioned RC15 machine

Commands are **examples for a supervised engineering test only**. They are not a recommendation for ordinary users to install an unqualified release.

```bash
# On a separately reviewed RC16 branch checkout, not on main:
python3 scripts/rc16_stage_upgrade.py --check
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The staging and rollback operations are intentionally separate from `--check`. Staging requires `STAGE_RC16_DEV1_DISABLED` and remains disabled. Rollback requires the exact snapshot and explicit `ROLLBACK_RC16_DEV1`; it refuses unexpected changes to managed files or printer state. Neither changes RatOS-saved offsets or rewrites the existing camera commissioning file.

## Required real-printer qualification matrix (NOT YET DONE)

1. **Read-only preflight:** verified full off-Pi backup, unchanged saved offsets, exact RatOS 2.1 build/hook provenance, unmodified stock VAOC, exact staging paths and symlinks, no active print, cool heaters. Preserve the previous installed gate, configuration and registration.
2. **Idle smoke:** stage disabled; restart Klipper, verify Ready, unchanged RatOS G-code handlers and plain native T0/T1/COPY/MIRROR printing. Check all original AutoXY macros and the commissioned camera still work.
3. **Worker-only performance:** with no print armed, run synthetic large-file stress and capture Klipper `Stats` reporting cadence, Klippy CPU time, total load, process memory and host scheduling. Repeat with native RatOS processing. Do not cap RatOS or force its priorities.
4. **Print path:** after deliberate opt-in activation in supervised conditions, verify independent two-tool AUTOXY calibration occurs after **fresh physical endstop homing and bed heatsoak** and before Beacon probing; no reliance on prior offsets after steppers disable. Single-tool, COPY and MIRROR bypass calibration. The original slicer file remains unmodified for the protected path.
5. **Failure handling:** inject a clearly controlled, no-offset-write camera failure; keep the original file held; exercise bounded retry; exercise native manual VAOC, return popup, explicit acceptance and continuation; confirm unverified writes/rehome/motor-off/restart/timeout all **block** continuation. Never use a live motor or nozzle as a simulated mock.
6. **Virtual-SD bypass matrix:** normal Mainsail upload/start, API print start, direct console `SDCARD_PRINT_FILE`, `SDCARD_PRINT_FILE_BASE`, `M23`, `M26`, M24 while held, unknown generator, unknown mode, malformed G-code, second start, reprint, stale registry, stale generated file, cancelled print.
7. **Long-running work:** at least two representative large slicer files; compare RatOS-only baseline with RC16 full path while host is busy. Record any Klipper stats gap, Klippy CPU usage, peak RSS, and `Timer too close` or transport errors. Stop if MCU timing becomes unreliable.
8. **Resource/retention failure cases:** disk near full, worker timeout/termination, interrupted staging, output mutation, stale registry, and cleanup run while idle vs active. No unknown file deletion. Test rollback after Klipper restart.
9. **Cross-host beta testers:** pinned wheel availability on the actual Raspberry Pi OS/Python/architecture combinations; compile/import OpenCV plus a real capture through the stock VAOC camera. The GitHub x86 CI matrix does not test ARM wheels.
10. **Release packaging and legal:** qualify the newly staged RC16 fresh installer on real hardware, capture RC16 manifest/checksums for final package, reconcile upstream RatOS macro provenance and copyright notices, and only then propose a public development release/tag.

### Release decision

**Current status: OFFLINE DEVELOPMENT CANDIDATE. No public tag, main merge or routine install.** The old RC15 Dev6 remains the stable public baseline.

The tests demonstrate code paths using mocks and synthetic files only; they **do not** establish actual camera calibration, device endstop behaviour, Beacon ordering, MCU scheduling safety, or reliable native VAOC recovery on a physical printer. Any failed preflight or unverified path means keep RC16 off and retain the known-good installation.


## Automatic storage policy (RC16 development update)

The RC16 handoff's installed configuration defaults to `retention_enabled: True`, `retention_days: 30`, and `retention_interval_hours: 24`. This does not turn the unqualified handoff itself on; `enabled: False` remains the safe staging default. After a qualified handoff is activated, automatic cleanup is scheduled internally without requiring a separate cron entry, operator confirmation or manual commands. The separate process never throttles stock RatOS.

Only complete, checksum-verified, registry-owned `__AutoXY_RC16_<token>.gcode` files are eligible. Deletion requires both file and registry to be at least 30 days old and requires the printer to be idle with no selected virtual-SD file. At most ten files are removed per pass. Unknown/unregistered files, interrupted/incomplete staging artifacts and source G-code are preserved, so a later release must also address orphan cleanup in an equally safe fashion. Camera-image retention continues to follow its separate existing policy.

## Soft total-image storage limit (2026-11-10)

**Important: quota is a cleanup trigger, not a capture gate.** RC16's separately scheduled legacy camera-image retention worker now uses `auto_xy_retention_rc16.py` with `retention_max_total_images_mib: 512` by default. This cap is for **all images created under the AutoXY results root combined**, including the original JPEGs and matching annotated `detected_*.jpg` output, across successful, failed and diagnostic runs. Unknown image files also count toward usage but are not deleted without verified run ownership.

Normal age-based cleanup remains (successful 7 days, failed 30 days, diagnostic 7 days, reports 90 days by default). Whenever total images exceed the configured soft target, the worker deletes older eligible *closed-run* images until below target where safe, regardless of their otherwise longer image retention period. It keeps reports and other non-image files under their separate policies. Current/in-progress/unknown/symlinked runs are never deleted merely to meet the target.

If unknown/active images prevent achieving the target, the worker reports a warning and **calibration and new image capture continue normally**. Usage may temporarily exceed the target during active sessions. Cleanup runs only outside ongoing calibration/print/recovery through the existing controller's nonblocking background retention mechanism; stock RatOS is not throttled.

This is distinct from the 30-day generated-G-code retention worker, which manages generated print files rather than calibration images. Both policies are configured for RC16 but are not active on the user's installed RC15 until the RC16 development build is qualified and installed.
