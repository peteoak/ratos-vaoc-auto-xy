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
| Installed version / legacy import | 10 checked installed RC16 files matched `main` at 28 Nov audit; Trial04 import symlink removal **confirmed on Pi**; other update installation history inferred only where directly verified |
| Recent owner-confirmed print observation | A subsequent print started and **automatic XY calibration succeeded without manual VAOC** (26 Nov), but installed code revision not established |
| Recent adverse observation | 27 Nov COPY print correctly captured `TOTAL_TOOLSHIFTS=0` yet legacy Trial04 still staged it as `AutoXY_...gcode` |
| New ChatGPT continuity | [Engineering handover](DEVELOPMENT_HANDOVER.md) and this status file exist in GitHub; ChatGPT Project instructions are managed separately |

## 28 November 2026 — confirmed duplicate historical config ZIP cleanup

The owner ran an on-Pi SHA256 comparison of three **separate-inode, byte-identical** historical archive pairs in `/home/pi/printer_data/config/` and `/home/pi/autoxy-pre-uninstall-20261102-040312/config/`. Exact sizes: 1,281,689,218 B, 640,865,812 B and 320,491,183 B. The owner then ran a bounded, exact-name/expected-size/SHA256-verified removal script and supplied output confirming `PREFLIGHT PASS: 3 exact duplicates verified`, three `REMOVED` paths under the **pre-uninstall backup directory only**, and `DONE: Current config copies remain untouched`.

**Confirmed outcome:** 2,243,046,213 bytes (~2.24 GB / 2.09 GiB) of duplicate archive file data unlinked from the historical folder, with all three copies retained in the current config folder. Exact live filesystem free-space increase was not measured. No active AutoXY code, settings, references or firmware were modified by the script. **No separate off-Pi backup was confirmed**; do not delete the three retained config ZIPs until external copies are verified. Other historical folder/ZIP cleanup remains unapproved and requires a scoped review.

## 28 November 2026 — Trial04 dormant import symlink **retired on printer (owner verified)**

The owner ran the exact reviewed `scripts/rc16_retire_inactive_trial04_extension.py` from a pinned GitHub source at `/home/pi/AutoXY_RC16_Cleanup`. The actual Pi output confirms:
- `check`: **PRECHECK PASS**; legacy Trial04 was unloaded, RC16 guarded, exact expected symlink/source matched.
- `retire`: **RETIRED INACTIVE SYMLINK** at `/home/pi/klipper/klippy/extras/auto_xy_local_recovery.py`; original source at `/home/pi/auto_xy/klipper/auto_xy_local_recovery.py` retained; receipt stored at `/home/pi/auto_xy/rollbacks/retired-inactive-trial04-h1_8r5f6/receipt.json`.
- Owner's independent `test ! -e ... && test ! -L ...` and historical `test -f ...`: **both PASS**.
- Utility reports no Klipper restart and no changes to config, offsets or G-code. This is a **real printer-side filesystem confirmation** of the dormant symlink removal, not a new demonstration of print-start modes or dual-tool motion safety.

**Do not rerun the retirement** or restore the dormant link except for a justified, supervised rollback. Keep the receipt and original source. Next storage task: off-Pi, independently validated backups of three specific historical root-level directories totalling **4,400,944,978 bytes** at the audit snapshot. They have NOT been deleted and may contain unique recovery/commissioning data. [Reviewed cleanup classes and archive procedure](RC16_PRINTER_CLEANUP_REVIEW_20261128.md).

## 28 November 2026 — read-only installed-printer code and hook inventory

A legacy Trial04 print interceptor was shown to stage an unexpected COPY job on 27 November. Although the reviewed PR #15 retirement migration was merged, its **installation has not been confirmed**. The owner has requested a broader review of the Pi's installed AutoXY and RatOS print-start code and elimination of any similar intrusive duplicates.

A new **read-only** script, `scripts/rc16_printer_integrity_audit.py`, inventories active configuration includes, Klipper extension registrations and symlinks, source hashes against the RC16 source manifest, static print-hook candidates, selected startup references, and sanitised Moonraker state. It produces an inventory ZIP for private review; does not collect printer configuration values, G-code, camera frames, reference images, saved offsets or credentials and does not change any printer files. It is only the **inventory phase**: no installed printer report has yet been received or assessed.

Review instructions: [RC16 printer integrity audit](RC16_PRINTER_INTEGRITY_AUDIT.md). Next action: run this script on the Pi, review returned inventory, classify all suspected conflicts before proposing **separate**, reversible and narrowly scoped cleanup. Do not automatically remove all old files, alter Klipper source ownership or delete backups.

## 28 November 2026 — owner printer audit reviewed; dormant Trial04 import retirement prepared

Reviewed the owner-uploaded `AutoXY_Printer_Integrity_Audit_20261128_091706.zip` **privately** (SHA256 `690ea2b03b72d83f443a0b00a682d73abcea621450511cb5d21d76b7f563ae09`). No private inventory contents or printer config values were committed.

**Observed:** Klipper Ready; no active/selected print or native VAOC; RC16 handoff enabled/armed, SD/resume guards true and retention enabled. All **10** checked installed RC16 source blobs match the repository. The legacy Trial04 `auto_xy_local_recovery.py` source and Klipper import symlink remain on disk, but its configuration is not loaded. No additional AutoXY processor-hook or startup-service candidate was reported. This is a bounded static inventory, **not complete dynamic handler/security certification**.

**Action:** an exact-hash, safe-idle, rollback-receipted utility at `scripts/rc16_retire_inactive_trial04_extension.py` removes **only the dormant Klipper `extras` symlink** (not the source, native RatOS, config includes, offsets, G-code or camera refs). It performs pre/post checks and supports restore. **It has not been run on the printer.**

Historical backups occupying **4,400,944,978 bytes** across `autoxy-pre-uninstall-20261102-040312`, `autoxy-factory-backups` and `autoxy-quarantine-rc15-dev5-20261102` need off-Pi verified archives and exact reference review before deletion. The audit's `unreadable/outside include` warnings for RatOS/timelapse are scanner-boundary limitations, not grounds to change the native configuration. See [owner-specific cleanup review](RC16_PRINTER_CLEANUP_REVIEW_20261128.md).

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
