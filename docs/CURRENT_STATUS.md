# AutoXY — current development and printer-evidence snapshot

**Updated:** 28 November 2026, Pacific/Auckland  
**Project:** Automate Nozzle Calibration · RatRig V-Core 4 300 IDEX / RatOS 2.1 / Beacon / fixed USB VAOC camera  
**Source of truth:** [peteoak/ratos-vaoc-auto-xy](https://github.com/peteoak/ratos-vaoc-auto-xy) `main`

> **Read with** [DEVELOPMENT_HANDOVER.md](DEVELOPMENT_HANDOVER.md). This file is a **dated snapshot**, not proof of the printer's installed software. On every new chat inspect the latest `main` commit, applicable PR/CI and version-pinned source. Only the owner's installed hashes and hardware observations establish printer state.

## At a glance

| Item | Status at this snapshot |
| --- | --- |
| Development line | **RC16**; controller reports `0.5.0-rc16-dev1` in repository configuration |
| Release classification | **Supervised development/release candidate**; **not** hardware-qualified for unattended protected two-tool printing |
| Latest pre-handover source merge | `b1bcf92406b63b70bfcf18066bffc88802e45b63` (PR #15, 27 Nov; legacy Trial04 interceptor retirement) |
| Previous detector merge | `7724b4eea0081da25c89f18bf51a472bccf35c90` (PR #14; strict local annular corroboration) |
| Latest known CI on source PR | PR #15, **422 tests run; 5 skipped; CI passed** (offline test suite) |
| Installed versions of PR #14/#15 updates | **Unconfirmed**; do not assume printer applied them |
| Recent owner-confirmed print observation | A subsequent print started and **automatic XY calibration succeeded without manual VAOC** (26 Nov), but installed code revision not established |
| Recent adverse observation | 27 Nov COPY print correctly captured `TOTAL_TOOLSHIFTS=0` yet legacy Trial04 still staged it as `AutoXY_...gcode` |
| New ChatGPT continuity | [Engineering handover](DEVELOPMENT_HANDOVER.md) and this status file exist in GitHub; ChatGPT Project instructions are managed separately |

## Latest completed GitHub changes

- **PR #15 — Trial04 conflict retirement**, merged as [`b1bcf924`](https://github.com/peteoak/ratos-vaoc-auto-xy/commit/b1bcf92406b63b70bfcf18066bffc88802e45b63): adds `scripts/rc16_retire_trial04_recovery.py`. This exact-source/symlink/idle/fault guard comments out *only* the old `[include auto_xy_local_recovery.cfg]` line, preserving the source, symlink, old config and all printer-specific settings, then verifies RC16 print/resume API guards after a Klipper restart. Includes rollback and regression tests. **No observed installation/verification yet.**
- **PR #14 — T0 annular false-negative fix**, merged as [`7724b4ee`](https://github.com/peteoak/ratos-vaoc-auto-xy/commit/7724b4eea0081da25c89f18bf51a472bccf35c90): normal-lit `two_stage_roi` detector may accept a unique commissioned Hough circle only if a *separate*, near-global-strength (>=96%) local annular peak corroborates it within the **existing 18px** geometric limit. Does not relax position/radius criteria, photo identity, low-light consensus or ambiguity policy. Adds `scripts/rc16_local_annular_refresh.py` and tests. **No confirmed installed hash**.
- **PR #13 — safe next-print fresh attempt after no-write failure**, merge `e3f5baeae8469940f4f920aa8f8ff3d2bd4d5028`: when an older held job was safely cancelled, unloaded and exited VAOC with **no offset write** and unchanged saved baseline, an in-memory certificate may allow the **next new protected print** to home/heatsoak/calibrate from scratch. All uncertain/written-offset failures remain latched.
- **PR #12 — persistent ON/OFF operator-owned**, merge `412f8dfa7d997e5fa2b31a3ecd10b7ada2f9ebde`: failure never switches off the user's saved AutoXY preference; clearer refusal messages and still-hard safety interlocks.
- **PR #11 — cancel/PREPARING cleanup**, merge `8563e0e740de5983909497e25faa15a65081d3dd`: avoid stale session/reset and direct `do_resume` error after cancellation. The later handoff revisions supersede previous one-off updater scripts.

Source inventory: [RC16_SOURCE_MANIFEST.json](RC16_SOURCE_MANIFEST.json). See [RC16_RELEASE_STATUS.md](RC16_RELEASE_STATUS.md) for earlier real-run report examples and [RC16_START_HERE.md](RC16_START_HERE.md) for release-specific instructions.

## Confirmed printer observations — not extrapolated

1. Multiple earlier owner-provided supervised RC16 logs and reports showed independent two-tool calibrations within **±0.030 mm X/Y**, using two verification cycles; these report the calibration stage, not necessarily full print completion.
2. A 25 November T0 preflight failed closed on two usable, normally lit frames. The nozzle circle was present; the global coarse annular matcher locked onto a residue/reflection feature and disagreed with the Hough circle. The held print was subsequently manually calibrated with **native VAOC** and guardedly continued.
3. On 26 November the user confirmed the next print both started and performed **automatic XY calibration with no manual VAOC**. This is a meaningful real-world success, but **cannot be attributed to PR #14** without matching the installed detector checksum.
4. On 27 November a **COPY** print's `START_PRINT` said `TOTAL_TOOLSHIFTS=0`, with no AutoXY calibration indicated. Nonetheless an installed **Trial04** `auto_xy_local_recovery.py` intercepted `PROCESS_GCODE_FILE`, staged the ~30 MB file as `AutoXY_...gcode` and emitted “AutoXY Recovery: print staged via RatOS”. The old extension's Klipper entry was a symlink and `printer.cfg` included its separate configuration. PR #15 is designed to retire this conflict **but is not yet printer-verified**.
5. The OrcaSlicer **2.5.2** unsupported-version warning in the same COPY console log is from **RatOS post-processing**, distinct from AutoXY calibration and the legacy Trial04 file-staging conflict.

## Immediate next action / handoff boundary

**Priority 1: supervised verify/retire legacy Trial04 duplication.** Before modifying the actual printer, ensure no active print, selected file, paused recovery, native VAOC or outstanding fault. Check the **actual installed source and handoff status**; run the exact compatible **read-only** `scripts/rc16_retire_trial04_recovery.py check` from the reviewed PR #15 commit, and proceed with its `install` only if safe and compatible. A failure/refusal is evidence to inspect, **not** permission to force deletion or disable guards. Verify after restart:

- RC16 handoff is `enabled` and `armed` with `sd_api_guarded=True` and `resume_api_guarded=True`; the Trial04 configuration is no longer loaded.
- COPY, MIRROR and independent one-tool jobs remain on **native RatOS**. No independent-camera calibration, no Trial04 `AutoXY_` copies or legacy “print staged via RatOS” recovery message.
- Independent **two-tool** print still follows guarded RC16 staging, physical rehome after motor-off, heatsoak, T0/T1 camera measurement, correction as needed and **two independent in-tolerance verifications**.
- Print cancel, recovery and protected file identity guards remain intact. Document **observed output** and installed hashes, not assumed outcomes.

**Priority 2: detector and physical repeatability.** If PR #14's exact detector source isn't installed, evaluate its version-pinned `check`/`install` only for that recognised RC16 configuration. Record real preflight images and movement accuracy; ensure T0 and T1 camera scale, correction direction/sign and post-home repeatability are reliable. Never treat geometric image recognition alone as proof of actual physical nozzle-axis accuracy.

**Priority 3: finish hardware safety matrix**, including native manual VAOC fallback, guarded cancel/resume, audited no-write fresh start, direct virtual-SD bypass, operator ON/OFF persistence, real low-light and 5xx camera errors, scale recapture, soft image quota/generation cleanup under pressure, and interrupted/failed upgrade rollback. See [RC16_SUPERVISED_QUALIFICATION.md](RC16_SUPERVISED_QUALIFICATION.md).

**Priority 4: beta images and compatibility.** Original unannotated beta tester nozzle frames and per-printer commissioned geometry are required to verify difficult two-circle detection. Review Pi-specific vision dependencies and RatOS/OrcaSlicer compatibility separately before broadening release claims.

## Installation and safety rules for the next chat

- GitHub `main` and a GitHub Actions success **do not install software** on the printer. User confirmation or on-printer read-only checks are required.
- Do not run a historical updater or a fresh RC15 installer on a running RC16 machine. Every update must pass **exact-source compatibility**, idle/unloaded/VAOC/fault gates and rollback preflight.
- The operator controls persistent ON/OFF. **FAULT/IN_PROGRESS** blocks unverified independent dual-tool prints even if AutoXY is ON. No raw `M24` or Resume after a held-failure.
- **Every** subsequent protected independent two-tool print after stepper disable requires new physical homing, heatsoak and live XY verification. Previous manual/automatic alignment is not reusable.
- For an **actual software release**, provide revision-pinned download, preflight, install, verification and rollback commands. For this **documentation-only** handover change, no printer download/install is required.
- Do not publish private printer logs, local IPs, personal/customer files, raw camera frames or saved printer-specific offsets in a public GitHub handover.

## Maintenance

After each meaningful merge or new real printer test, update this file with **dated** code commit/CI, installed-state evidence, supervised findings, outstanding risks and a single recommended next action; update [DEVELOPMENT_HANDOVER.md](DEVELOPMENT_HANDOVER.md) when a durable architecture or safety rule changes. Preserve the existing RC15 archived release/manifest.

**New-chat starting instruction:** “Continue AutoXY. Read `docs/DEVELOPMENT_HANDOVER.md` and `docs/CURRENT_STATUS.md`, inspect current `main` and relevant CI, and continue the highest-priority unresolved work without assuming any GitHub update was installed.”
