# AutoXY — persistent development handover for new ChatGPT Project conversations

**Last curated:** 28 November 2026 (NZ time)  
**ChatGPT Project:** Automate Nozzle Calibration  
**Authoritative code:** [peteoak/ratos-vaoc-auto-xy](https://github.com/peteoak/ratos-vaoc-auto-xy), default branch `main`  
**Current snapshot:** [CURRENT_STATUS.md](CURRENT_STATUS.md) · [RC16 release status](RC16_RELEASE_STATUS.md) · [RC16 start guide](RC16_START_HERE.md) · [supervised qualification](RC16_SUPERVISED_QUALIFICATION.md)

> **Purpose:** Carry the engineering decisions, verified evidence, warnings and next steps between ChatGPT conversations. This is a curated **technical handover**, not an export of private chats, raw printer logs or camera images. It does not override current code, fresh diagnostics, or a confirmed printer state.

## How to resume work in a new ChatGPT conversation

A new conversation inside the **Automate Nozzle Calibration** ChatGPT Project should:

1. Treat AutoXY as an ongoing project. Read **this file** and [CURRENT_STATUS.md](CURRENT_STATUS.md) first; follow their links to the current release guide, status and test matrix.
2. Check `main`, the latest relevant GitHub commits, open pull requests, CI results, source manifests and any changed code **before implementing**. Treat GitHub's live source as authoritative. A handover's date/commit is a snapshot, not proof that `main` has not moved.
3. Recover relevant earlier conversations in the same ChatGPT Project when a historical decision is unclear. If a chat recollection conflicts with current source, investigate, do not silently invent a reconciliation.
4. Never infer what is **installed on the printer** from GitHub, a merge, CI or a previous installation command. Ask for/collect installed hashes and status only when necessary. Keep **source-tested**, **printer-observed** and **not verified** separate.
5. Preserve non-negotiable safety and RatOS compatibility requirements below; never weaken them to make a test green or a print start.
6. Work in substantial, coherent, automated-test-backed iterations. Avoid repetitive progress prompts; involve the owner when real hardware access, missing evidence or an important decision is required. Update both handover files after significant changes.
7. For every applicable printer release provide **exact revision-pinned download**, **read-only compatibility/preflight**, **install**, **post-install verification**, and **rollback/recovery** instructions. Do not recycle a historical updater against current installed code.
8. Never claim actual printer testing when only offline unit tests, CI or image replay ran. Never suggest that an unattended protected print is qualified unless the hardware matrix actually supports it.

**Minimal new-chat prompt:** “Continue AutoXY development. Read `docs/DEVELOPMENT_HANDOVER.md` and `docs/CURRENT_STATUS.md` in `peteoak/ratos-vaoc-auto-xy`, check the latest GitHub code/CI, preserve all safety requirements and installed-state uncertainty, and continue from the outstanding work.”

These **GitHub documents are separate from ChatGPT Project instructions**. The user should keep the startup workflow in the Project instructions so fresh chats know to fetch the documents. GitHub does not automatically inject files into ChatGPT conversations.

## Printer and integration

- Hardware: **RatRig V-Core 4 300 IDEX**, RatOS **2.1**, **Beacon** probe, fixed USB/native-VAOC camera, **physical endstop homing** (not sensorless). Camera, mechanical setup and commissioned image geometry are printer-specific.
- RatOS's **native VAOC** remains functional and independently usable. Avoid modifying stock RatOS files unless a narrowly approved and rollback-safe change is required.
- After each print the owner has configured **steppers to turn off**. Previous XY alignment is invalid after motors disable and must not be carried into the next protected print.
- The system reads the machine's current saved/runtime `idex_xoffset`, `idex_yoffset` and `idex_zoffset` rather than bundling universal offsets. Preserve them across install/rollback unless an explicitly verified calibration correction authorizes a write.
- Installers can require interactive sudo for a single Klipper restart. No credentials or private camera frames should be committed to GitHub.

## Non-negotiable print behaviour

**Mode selection and processing**
- **Independent IDEX using both tools** is the **only** automatic print-start XY calibration target. It must use fresh physical homing, heatsoak, camera T0/T1 measurement, bounded correction and **two consecutive XY verifications within ±0.030 mm per axis** before protected print continuation.
- **One-tool independent**, **COPY** and **MIRROR** jobs must use the native RatOS processing/printing path **without AutoXY calibration or AutoXY generated-file staging**. No slicer-side AutoXY postprocessor should be necessary. Normal RatOS post-processing remains enabled.
- Detect mode **before RatOS resets carriage state**. Distinguish a slicer's “used tool(s)”/reported selection count from a real `START_PRINT TOTAL_TOOLSHIFTS` count and the physical IDEX mode.
- Preserve the **original** source G-code; any RC16 protected generated file must have verified identity/provenance, be eligible only for the appropriate independent two-tool print, and be cleared/retained safely after use.

**Safety and recovery**
- Automatic camera failure or ambiguous geometry must hold/refuse an **unverified protected print**. Never bypass with raw `RESUME`, `M24`, direct virtual-SD selection, `do_resume()` or an untrusted generated file.
- Allow **one guarded retry** where safe, or **native manual VAOC** with explicit validated one-use continuation of the **same original print**. Do not require a slicer postprocessor for recovery.
- A failure must **never change the operator's persistent AutoXY ON/OFF preference**. `ON` does not override a latched `FAULT` or `IN_PROGRESS` interlock. `AUTO_XY_RUN_NOW` is a standalone calibration command, not a print to resume.
- For a **proven no-offset-write** failure, only after the old file is terminal, completely unloaded, native VAOC exited and saved offset baseline unchanged may the next independent protected print receive a **fresh** audited run. An offset write/uncertainty, stale paused file, unavailable in-memory certificate or changed operator preference remains a hard interlock.
- Camera/nozzle recognition is **geometry-first**. Unique commissioned Hough circle has no photographic likeness veto; an exactly two-circle ambiguity may use limited photographic evidence (at most **25%** tie-break), but unresolved ambiguity must fail closed. The annular coarse-vs-Hough check must agree within the existing **18 px** tolerance, including the narrowly guarded local annular corroboration introduced in PR #14. Do not blindly loosen thresholds or accept false circles.
- Maintain bounds on motion, temperature, camera capture, X/Y correction, coordinate direction, scale consistency, home/motor state, offset writes and timeout. A scale mismatch >12% has a targeted recapture path, **not** permission to guess a correction.
- Preserve nozzle thermal/Beacon order and the RatOS startup/priming path. No stale camera snapshots or previous print calibrations may authorize the next print.

**Retention and status**
- Automatically reclaim eligible image files only when safe, targeting a **soft ~512 MiB combined image budget**; temporary exceedance is acceptable. Protect commissioned originals, in-progress/unknown run state and referenced images. Apply separate safe generated-G-code retention and never delete selected, paused or printing files.
- Report meaningful recovery prompts, clear reason-specific refusal messages, per-capture camera evidence and comprehensive `report.json` diagnostics. Processed protected G-code names should carry a useful original-name and date/time identifier.
- Ordinary Mainsail Pause/Cancel can be delayed while synchronous calibration is running; the owner accepted this limitation for the supervised candidate. **Emergency Stop** is for real physical danger; never resume an unverified print after it.

## Key architecture and files (check live repository before editing)

| Component | Responsibility |
| --- | --- |
| `src/auto_xy_gate_rc16.py` | Print-mode capture, calibration state machine, safety interlocks, camera measurement/correction, guarded manual fallback |
| `src/auto_xy_ratos_handoff_rc16.py` | RC16 `PROCESS_GCODE_FILE` ownership, RatOS processing, protected-file identity, virtual-SD and resume guards, cancellation lifecycle |
| `src/tools/rc16_prepare_worker.py` | Offline source-stage/prepare/verify/registry lifecycle and guarded source identity |
| `src/auto_xy_detector_rc16.py`, `src/auto_xy_vision_worker_rc16.py` | Commissioned geometric camera detection, measured centres, capture/retry evidence |
| `src/auto_xy_reference_signature_rc16.py`, `src/auto_xy_commission_candidates_rc16.py` | Appearance evidence/two-circle disambiguation and nozzle commissioning support |
| `src/auto_xy_retention_rc16.py`, `src/tools/rc16_generated_retention.py` | Safe closed-run image and generated G-code cleanup |
| `config/auto_xy_macros.cfg` and RC16 configs | User controls, RatOS hooks, correction/recommission/thermal configuration |
| `scripts/rc16_release_install.py` | New or exact recognised RC15-to-RC16 supervised active install |
| `scripts/rc16_*_refresh.py` and `scripts/rc16_retire_trial04_recovery.py` | **Different exact-hash** scoped updates; never assume compatibility between revisions |
| `docs/RC16_SOURCE_MANIFEST.json` | Version-pinned source inventory; root `MANIFEST.json` is **RC15** compatibility history |

Read [RC16 source manifest](RC16_SOURCE_MANIFEST.json), [start guide](RC16_START_HERE.md), [diagnostics](RC16_DIAGNOSTICS.md), and [qualification matrix](RC16_SUPERVISED_QUALIFICATION.md) for precise current contracts. The immutable historical RC15 Dev6 is in `release/rc15-dev6-frozen` and `docs/legacy/`; do not rewrite archived integrity records.

## Development history worth preserving

- **RC15 Dev6, 2 November:** storage/retention rollback and compatible frozen baseline. Protect installed offsets/config/refs. Earlier automatic calibration and manual VAOC paths had supervised successes; do not mistake that for the latest RC16 being installed.
- **RC16, early/mid-November:** initial installer/rollback difficulties and a successful later supervised set of calibration runs. Reports show verified XY within tolerance and other fail-closed T0 appearance or T0/T1 camera-scale refusals. Native manual VAOC continuation was demonstrated. Exact evidence is in [RC16 release status](RC16_RELEASE_STATUS.md).
- **24 November, cancellation/next-print safeguards:** PR #11 addressed stale `PREPARING`/cancelled-file state. PR #12 made ON/OFF operator-owned with clearer fault/enable/busy refusal messages. PR #13 added strictly audited no-write cancellation → next fresh protected-run eligibility; **never** release the old unverified file.
- **25 November diagnostic incident:** a T0 preflight had two normally illuminated images but rejected the nozzle because a global coarse annular match picked residue/reflections far from the valid Hough rim; the original protected print was held. Native VAOC was used and the same print was guardedly continued. **26 November PR #14**, merged as `7724b4eea0081da25c89f18bf51a472bccf35c90`, implemented tightly bounded local independent annular corroboration. This is code/CI evidence, **not proof that PR #14 was installed on the printer**.
- **Subsequent owner confirmation, 26 November:** a later print started and completed automatic XY calibration without manual VAOC. Treat this as hardware-observed automatic success; the **installed revision of the PR #14 fix was not confirmed** from that statement.
- **27 November COPY incident:** `START_PRINT` showed `COPY; TOTAL_TOOLSHIFTS=0`, yet the print was staged as `AutoXY_...gcode`. The owner provided the exact installed **legacy Trial04** `auto_xy_local_recovery.py`, a Klipper extension symlink and `printer.cfg` include; Trial04 had intercepted `PROCESS_GCODE_FILE` and staged **all** modes. **PR #15**, merged as `b1bcf92406b63b70bfcf18066bffc88802e45b63`, added a tightly pinned, rollback-safe migration script that retires **only Trial04's config include**, leaving native RatOS processing and RC16 guards intact. **No confirmed installation or physical regression test of this migration yet.**
- **27 November PR #15 CI:** **422 tests executed, 5 skipped** and the suite passed. This is offline GitHub evidence, not printer qualification. After any new code commit, get its own CI result rather than carrying this count forward.
- **28 November:** this persistent handover and status snapshot was introduced to avoid repeatedly reconstructing requirements after a long ChatGPT conversation reaches its limits.
- **28 November — printer housekeeping audit:** after legacy Trial04 was found intercepting COPY mode, the owner requested a complete audit of installed printer code and old AutoXY files. First collect only a **read-only inventory** of active config/extension hooks, symlinks, installed hashes and scoped startup references using [`rc16_printer_integrity_audit.py`](../scripts/rc16_printer_integrity_audit.py). Inspect the real Pi report before classifying anything for retirement. Never mass-delete backups, source files, reference JPEGs or potentially active code. Follow [audit and cleanup protocol](RC16_PRINTER_INTEGRITY_AUDIT.md).

- **28 November owner printer audit:** reviewed an actual private read-only inventory ZIP. RC16 safeguards were armed, all 10 checked RC16 installed source blobs match GitHub, the old Trial04 *configuration is inactive*, and no other AutoXY hook/startup candidates were flagged. Its dormant import symlink remains present. A scoped exact-source/safe-idle/rollback-receipted removal script is in `scripts/rc16_retire_inactive_trial04_extension.py`; **installation not confirmed**. Historical 4.40 GB backup sets must be archived and verified off-Pi before any deletion. [Full review](RC16_PRINTER_CLEANUP_REVIEW_20261128.md). RatOS/timelapse warnings in the inventory reflect the scanner's confined directory, not evidence of missing printer includes.

**28 November on-printer confirmation:** the owner executed the exact dormant Trial04 extension retirement script. `check` and `retire` both passed; the Klipper `extras/auto_xy_local_recovery.py` symlink was unlinked, the historical source preserved, rollback receipt created at `/home/pi/auto_xy/rollbacks/retired-inactive-trial04-h1_8r5f6/receipt.json`, and two independent shell existence checks passed. **This installed-state change is confirmed**; no restart and no config/offset/G-code modification reported. This is *not* a physical COPY/MIRROR/two-tool print regression test. Do not offer the already-completed retirement as a pending install. Next: archive 4.40 GB of historical backups **off the Pi and verify integrity before discussing deletion**. See [owner cleanup review](RC16_PRINTER_CLEANUP_REVIEW_20261128.md).

## Open priorities (recheck against live code and printer evidence)

1. **Confirm installed source/version** only when needed; the latest GitHub PR #14 and PR #15 updates are **not confirmed installed** by the conversation. Do not issue a non-matching old updater.
2. **Supervised Trial04 retirement**: verify read-only check, installation, RC16 handoff SD+resume guard status, COPY/MIRROR/one-tool native pass-through with no `AutoXY_` staging, and a protected independent T0/T1 print with fresh homing and two verifications. Ensure the actual previous duplicate `PROCESS_GCODE_FILE` ownership is gone.
3. **Revalidate automatic camera T0/T1 accuracy**, correction sign/direction, repeatability across new homing cycles, and under nozzle ooze; distinguish recognition success from true physical XY axis accuracy.
4. Complete **negative-path protected print** tests: cancel/held state, no-write rearm, attempted offset write, native manual VAOC continue, fault/ON ownership, reprint, direct virtual-SD API/M23/M24/M26, motor disable or rehome during recovery and rollback.
5. Obtain the beta tester's original **unannotated** two-circle frames with relevant commissioning geometry for an actual replay. Do not rely on overlays or assume the owner printer's scenes prove all other cameras.
6. Gather actual Pi evidence for soft image quota cleanup, generated-file exclusion/cleanup, storage pressure, camera 5xx fresh retries, cross-tool camera-scale recapture and installation rollback on the real platform.
7. Audit OrcaSlicer newer versions separately. OrcaSlicer **2.5.2** showed RatOS's warning that supported versions are `2.1.1`, `2.2.0`, `2.3.0` and `2.3.1` at the time. Do not confuse that warning with AutoXY's own print classification.
8. Release RC16 for unattended protected prints **only after** all relevant hardware and safety tests pass. Do not force a release tag based on a green CI run.

## Cross-chat memory maintenance protocol

- At the end of every **significant development iteration**, update [CURRENT_STATUS.md](CURRENT_STATUS.md) first with current code changes, test evidence, installed-state confidence, blockers and next action.
- Update **this handover** whenever requirements, architecture, negative-path protections, installer policy, or durable troubleshooting conclusions change. Keep historical claims labelled as observations or repository evidence.
- Keep a concise trail to merge commit(s), PR(s), release notes and relevant tests. Do not flood these files with a raw message-by-message transcript.
- **Do not commit** passwords, tokens, sensitive printer logs, camera reference frames, personally identifying filenames, local network information, unredacted customer files, printer-specific saved offsets or full chat transcripts. Store raw diagnostics privately as needed and summarize only reusable technical findings here.
- A documentation update alone neither changes nor verifies the printer, and the ChatGPT Project's private instructions are not automatically published to GitHub.
