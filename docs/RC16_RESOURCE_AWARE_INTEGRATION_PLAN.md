# AutoXY RC16 Dev1 — resource-aware protected-print integration design

> **HISTORICAL ENGINEERING RECORD — not current installation or operational guidance.** Architecture plan written before integration; proposals about missing print-start interception/retention no longer describe the current RC16 implementation. For current behaviour and remaining qualification use [RC16 Start Here](RC16_START_HERE.md) and [RC16 Release Status](RC16_RELEASE_STATUS.md).

**Status:** DESIGN ONLY — not installed, not hardware-validated, not a release.  
**Base:** public RC15 Dev6 on `main`.  
**Purpose:** a cohesive, publishable development revision that integrates automatic IDEX protected-print preparation without adding unacceptable load to Klipper or restricting RatOS.

## 1. Core rules

- **Do not modify, throttle, downgrade, or replace RatOS post-processing.** It must keep its existing native IDEX file transformation, validation, scheduling, and CPU/I/O access.
- Make AutoXY's added work lightweight and cooperative. Defer AutoXY CPU- and I/O-intensive work to a lower-priority *external* worker; do not apply priority or resource limits to Klipper, RatOS, Moonraker, or their child processes.
- Default to process scheduling priority (e.g. `nice` and I/O priority where supported), **not an arbitrary hard CPU cap**. Measure actual impact before choosing limits.
- Preserve unmodified source G-code. A generated file must never be printed until its provenance, RatOS acceptance, and AutoXY protection have been positively verified.
- No unprotected independent dual-tool print may slip through while protected-print mode is enabled. Ordinary single-tool, COPY, MIRROR, and unrelated console starts must retain the validated native RatOS path.
- Every new protected independent two-tool print must perform fresh homing, heatsoak and camera alignment after steppers have disabled. Never reuse a previous print's alignment across motor disable, rehome, shutdown, or restart.
- Manual native RatOS VAOC must remain accessible as a supervised recovery option. `AUTO_XY_RUN_NOW` is a standalone command and has **no held print** to resume.
- Do not overwrite saved nozzle offsets during installation or use printer-specific offsets/camera images as distributed defaults.
- Keep the integration **opt-in** until the complete supervised print-mode and failure-recovery matrix passes.

## 2. Architecture to implement

### 2.1 Lightweight Klipper coordinator

The Klipper-facing integration may accept a print request, validate mode and small metadata, start or monitor the worker, and gate virtual-SD handoff. **It must not** run whole-file `read_text`, `write_text`, `shutil.copyfile`, `hashlib` passes, G-code transformation, or large synchronous JSON/log parsing on the Klipper reactor thread.

Before choosing a command hook, confirm ownership and call flow for **exactly the supported RatOS 2.1 revision**. Do not silently replace `PROCESS_GCODE_FILE` or assume a private trial wrapper is safe to ship. Prove that the coordinator's polling/callback architecture keeps the Klipper reactor servicing MCU communications. A temporary preparation-state machine must hold print release until the complete output is verified; do not block synchronously on subprocess completion in the reactor.

### 2.2 Dedicated AutoXY file worker

Build an external `auto_xy_prepare_worker` with one-job-at-a-time admission, modest CPU/I/O scheduling priority, bounded logs, explicit cancellation, timeout/error reporting, and process cleanup. Worker work must be split into ordered phases so its heavy operations do not compete unnecessarily with native RatOS post-processing.

Worker responsibilities (after security review and as architecture allows):
- Resolve only allowed virtual-SD paths; reject traversal, symlinks and unsafe files; verify regular files.
- Inspect minimal G-code metadata with streaming reads, early exit and bounded buffers.
- Stage files safely without altering the original; use streaming copy + incremental digest where needed, not an extra whole-file digest pass.
- Validate mode/toolshift semantics and run only the AutoXY transformation required by the approved native RatOS pipeline.
- When RatOS has completed its native post-processing, validate the *final* file, including exact protected start commands, marker consistency, file identity and integrity.
- Create prepared files transactionally (unique temporary file + close/fsync as appropriate + atomic publish). Never hand a partially written file to virtual SD.
- Produce a small, bounded, machine-readable result containing status, source/output identity, expected digest, format/version, validated mode and human-readable *sanitised* error codes. Avoid private file contents in logs.

Sequence is a design requirement, not a claim that the existing private trial's current order is release-ready. Test whether protection must be injected before or after RatOS transformation; **never double-postprocess a RatOS file**. Prefer a single additional streaming pass only where technically necessary.

### 2.3 Release handoff and recovery

Use explicit states, e.g. `IDLE → PREPARING → RATOS_PROCESSING → VERIFYING → READY_TO_START → CALIBRATING → VERIFIED → PRINTING`, plus `REJECTED / CANCELED / RECOVERY_PENDING`. Implement idempotent transitions and generation identifiers so that stale worker completions cannot start a new/different print.

No virtual-SD handoff until all prerequisites pass. Recheck worker result and final file identity at the handoff boundary; fail closed if the file changed or state is uncertain. On abort, preserve the original source and emit an actionable error without automatically retrying endlessly.

If camera calibration fails: maintain the hold; permit at most the explicitly authorised bounded retry; otherwise present supervised **native manual VAOC** recovery. Only a positively verified current homing/VAOC session can authorise continuation; a motor disable, rehome, restart or shutdown invalidates the prior alignment. `AUTO_XY_RUN_NOW` must not create a held-file resume.

### 2.4 Keep stock RatOS independent

RatOS remains the authoritative processor for its normal slicer/IDEX features. Do not introduce a RatOS source patch, replacement CLI, edited installed RatOS files, or CPU cap on its worker.

Any integration technique must be compatible with the supported RatOS command lifecycle and reversible by installer rollback. If no safe automatic interception exists for a RatOS version, the installer/preflight must refuse automatic integration and leave ordinary RatOS functioning.

## 3. Performance acceptance and observability

The identified shutdown was `MCU 'mcu' shutdown: Timer too close` during an early print start (`sd_pos=0`). Klipper reporting paused for about six seconds, and its reported host CPU-time delta was substantial. This is evidence that synchronous host work needs investigation, **not proof of a single guilty AutoXY function**.

For each worker phase, record elapsed time, worker CPU time, peak memory, bytes read/written, and whether/when the native RatOS processor was active. Keep a compact per-run diagnostic report with bounded retention, with no copied G-code, personal names, paths or nozzle photos in public fixtures. Baseline normal RatOS alone and RatOS + AutoXY on identical offline files.

Required acceptance:
- Under idle and loaded-host conditions, Klipper's reactor remains responsive; no multi-second reactor stalls attributable to AutoXY work.
- AutoXY's memory use scales with bounded buffers, not source G-code file length.
- AutoXY never changes RatOS's scheduling priority, CPU caps, I/O priority or process flags.
- RatOS output and single-tool/COPY/MIRROR native behaviour match baseline, aside from approved AutoXY processing.
- Worker kill, disk-full, OOM, timeout, duplicate request, file mutation, interrupted atomic publish and restart **never** release an unverified print.
- Source G-code is unchanged after success or failure.

Set numerical resource ceilings only after representative measurements on actual supported hardware; no invented percentage target is a release guarantee.

## 4. Regression and compatibility matrix

1. Single-tool T0 and T1 starts, with AutoXY protection enabled: bypass calibration and continue native RatOS.
2. COPY and MIRROR, including malformed mixed-toolshift cases: no accidental protected transform; reject unsafe inconsistencies.
3. Independent dual-tool with nonzero toolchanges: protected pre-Beacon sequence, fresh home/heatsoak/measurement every print after motors disable.
4. Unprotected independent dual-tool or unknown print mode with protection enabled: fail closed.
5. Calibration success, measure-only mismatch, bounded retry, manual VAOC recovery, canceled recovery, invalidated homing, and standalone `AUTO_XY_RUN_NOW`.
6. Already RatOS-processed files, unknown metadata, repeat print, changed source and changed generated file: no double processing, no stale release.
7. Small/large G-code, hostile filenames, symlinks, permissions, partial files, disk full, concurrent requests, subprocess crash, process timeout and delayed results.
8. Run with the complete vision-controller, retention and protected-print regression suite; verify saved offsets remain unchanged unless calibration writes are explicitly authorised.
9. CI: Python 3.9 and Python 3.11 paths where supported; tested, **mutually compatible** OpenCV/NumPy binary combinations for actual target architectures. Do not treat broad version ranges as a verified multi-architecture lockfile.
10. Supervised hardware run on the owner's RatOS 2.1 IDEX **after offline tests pass**, with an archived rollback and known-good native VAOC available. A passing simulation does not certify MCU timing on real hardware.

## 5. Release engineering gates

- Complete implementation, source manifests, version bump, release notes and rollback instructions **as one cohesive tagged development candidate**. Do not deploy a stack of untracked live hotfixes.
- Provide a tested migration strategy for existing RC15 installs; the RC15 fresh-only installer must not be represented as a supported upgrade.
- Preflight checks supported RatOS version/hook ownership, idle/cool printer, trusted environment, camera commissioning and package integrity. Opt-in stays OFF until explicitly enabled.
- Third-party macro provenance/attribution is OPEN in `THIRD_PARTY_NOTICES.md`. Resolve before distributing new adapted RatOS material. Prefer invoking native APIs/macros over redistributing upstream macro bodies.
- Sanitize the GitHub contribution: **no private integration code pasted verbatim without review; no live printer configuration, saved variables, original G-code, logs, credentials, photos or backup paths.**
- Pass CI, negative safety tests, manual rollback rehearsal, end-to-end independent IDEX tests, and a clean-print-mode compatibility review before public release or merging to `main`.

## 6. Implementation milestones

- **M1 / analysis:** inventory the private trial's call graph locally and measure every operation occurring inside Klipper; confirm RatOS version-specific entrypoint and source-provenance issues.
- **M2 / worker:** implement streaming file-preparation worker and transactional outputs, priority controls and bounded result format with offline tests.
- **M3 / bridge:** build Klipper-safe asynchronous coordinator around native RatOS handling; test cancellation, double processing and fail-closed behaviour.
- **M4 / integration:** combine protected print state machine, manual VAOC recovery, reuse invalidation and print-mode bypass with reproducible tests.
- **M5 / packaging:** pin verified dependency sets per supported architecture, add installer/upgrade/rollback and docs, resolve upstream notices.
- **M6 / acceptance:** offline stress + supervised machine validation; only then update release metadata and consider a development tag.

**Explicit non-goals:** modifying stock RatOS; degrading native RatOS throughput; applying CPU limits to Klipper; automatically launching an unverified print; changing owner calibration offsets; or publishing private diagnostic data.


## 7. 2026-11-09 eligibility clarification and implementation boundary

**AutoXY calibration is required only for a genuine independent IDEX print using both nozzles/toolheads.** A single-tool T0 or T1 print, and a COPY or MIRROR print, must follow ordinary RatOS without invoking automatic XY calibration. This distinction applies equally to initial prints and reprints. Mode and tool-use classification must be positively established; a contradictory or unknown independent two-tool job fails closed.

The branch now includes experimental `src/tools/rc16_prepare_worker.py` and `src/auto_xy_ratos_handoff_rc16.py` with simulated tests, **not an installed or qualified release**. The worker performs expensive G-code staging, transformation and hashing outside Klipper at reduced scheduling priority; the coordinator leaves native `ratos.process_gcode_file` unchanged. The audited RatOS implementation already yields to the reactor while its native CLI runs.

The coordinator guards the known `SDCARD_PRINT_FILE_BASE` bypass when enabled. It preserves normal `SDCARD_PRINT_FILE → PROCESS_GCODE_FILE` behaviour for ordinary print modes, but **other Klipper/Moonraker virtual-SD entrypoints remain a live qualification requirement**. No blanket assertion that all possible bypass paths are covered is justified.

The prototype also deliberately rejects direct reprints of generated RC16 files until a verified source registry is implemented. Installer activation is not wired; no changes to `main` or the owner's installed RC15 are permitted until end-to-end validation.
