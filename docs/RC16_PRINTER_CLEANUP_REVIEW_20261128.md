# RC16 printer integrity review — 28 November 2026

**Status:** Analysis of the owner-supplied **read-only** AutoXY printer audit; not a claim that any cleanup below has been installed or performed. This is **not** a hardware or security certification.

**Evidence:** `AutoXY_Printer_Integrity_Audit_20261128_091706.zip`, SHA256 `690ea2b03b72d83f443a0b00a682d73abcea621450511cb5d21d76b7f563ae09`. The ZIP was reviewed privately, **not** committed to GitHub. Generated on the RatOS Pi. The audit lists metadata and static hook locations, not complete live Python command ownership or contents of all included RatOS configs.

## Confirmed on the RatOS Pi after the inventory (28 November)

The owner has now run the scoped retirement tool successfully. It printed `PRECHECK PASS` for both `check` and `retire`, confirmed RC16's guarded state and the exact reviewed symlink/target, removed **only** `/home/pi/klipper/klippy/extras/auto_xy_local_recovery.py`, and retained the historical source. The rollback receipt is `/home/pi/auto_xy/rollbacks/retired-inactive-trial04-h1_8r5f6/receipt.json`. Two separate shell checks proved the symlink is absent and the source exists. No restart or offsets/config/G-code edit was reported. **The dormant import retirement is COMPLETE on the printer.** The pre-retirement descriptions below describe the initial audit and should not be read as its current symlink state.

A **post-retirement COPY/MIRROR/native single-tool print test** and a protected independent dual-tool print test remain distinct, supervised hardware verification tasks. The static inventory and symlink removal do not certify unattended operation.

## Verified from inventory

- Moonraker connected; prior print state `complete`, SD inactive/unselected, native VAOC closed. Saved flags: AutoXY ON, no FAULT, no IN_PROGRESS, not measure-only, no retry available.
- RC16 handoff: `enabled=True`, `armed=True`, `busy=False`, `sd_api_guarded=True`, `resume_api_guarded=True`, automatic retention enabled.
- All **10 installed RC16 source files checked** match current `main` source Git blob hashes: gate, handoff, detector, worker, reference/signature helpers, retention, recommission and preparation/cleanup workers. This confirms those files at audit time; it does not prove the calibration hardware has passed every test.
- Active AutoXY config: `auto_xy.cfg`, `auto_xy_macros.cfg`, `auto_xy_rc16.cfg`, RC16 recommissioning and thermal macros. `auto_xy_local_recovery.cfg` is **not** in the active include graph, and no `auto_xy_local_recovery` section appears among the active sections.
- The known **Trial04** source is still present with reviewed SHA256 `12ddf4100b6b61cbb71f3cc1b26d0d87e2134a8c9cbdb0385f83aba50e11c03f`; `/home/pi/klipper/klippy/extras/auto_xy_local_recovery.py` is still a symlink to it. The source is **not active**, but the dormant symlink can be retired to avoid accidental reactivation.
- No **other** unexpected processor-hook paths or AutoXY startup-service candidates were flagged by this static audit. This is **not** a blanket statement that no dynamic Python hook is possible.
- The audit reports nine `unreadable/outside include` warnings for `RatOS/...` and `timelapse.cfg`. These are **scanner traversal limitations** (outside its confined config root / nonfollowed symlinks), **not proof that the actual RatOS includes are missing**. Klipper reported Ready/connected. Do not delete or alter them based on this report.
- Stock or normal companion Klipper extensions such as RatOS, Beacon and `palette2.py` may contain SD/print strings. Their presence alone is not evidence of AutoXY interference. Do not delete stock Klipper/RatOS/Beacon files.

## Cleanup classes

| Class | Paths / items | Decision |
| --- | --- | --- |
| **Active core — protect** | `auto_xy/klipper/auto_xy_gate.py`, `auto_xy/klipper/auto_xy_ratos_handoff_rc16.py`, `auto_xy/klipper/auto_xy_recommission_rc16.py`, corresponding `klipper/klippy/extras` symlinks, active `auto_xy*.cfg`/macros | Keep; source hashes match GitHub. |
| **Active data/rollback — protect** | `auto_xy/vision/`, `auto_xy/workers/`, `auto_xy/print_gate_runs/` (~140 MB), `auto_xy/rollbacks/`, `auto_xy/rc16_registry/`, `autoxy_camera_refs*` | No blanket deletion. Workers and commissioned geometry are essential; image retention policy decides eligible closed-run cleanup; keep rollback receipts. |
| **RatOS/user data — protect** | RatOS includes, Beacon modules, `printer_data/gcodes/` (~3.64 GB), printer configuration, native VAOC and saved offsets | No AutoXY cleanup of this data. |
| **Dormant extension — narrowly retire** | **Only** `klipper/klippy/extras/auto_xy_local_recovery.py` symlink to the reviewed Trial04 source | First safe modification: use exact-hash/idle/RC16 guard-scoped `scripts/rc16_retire_inactive_trial04_extension.py` to unlink only this symlink, preserving source and a reversible receipt. No restart, no config edit, no automatic unlink of anything else. |
| **Historical test/install copies — archive candidates** | Root-level `AutoXY_RC13*`, `AutoXY_RC14*`, `AutoXY_RC15*`, `AutoXY_RC16_*` download/extraction directories, `AutoXY_Trial04*`, `AutoXY_True_Fresh*`, `autoxy_b43*` and `autoxy_b44*` | Not included in active config graph or startup flags, but cannot be declared safe for deletion without checking *actual cross-references*, contents and having a recoverable copy. Small total saving relative to backup sets. |
| **Large historical backups — preserve pending export** | `autoxy-pre-uninstall-20261102-040312` **3,032,712,203 B**, `autoxy-factory-backups` **715,990,122 B**, `autoxy-quarantine-rc15-dev5-20261102` **652,242,653 B** | **4,400,944,978 B combined (~4.40 GB decimal)**. Copy off the Pi and verify before proposing any deletion. Potential unique camera references, saved variables or rollback material; not automatically redundant. |
| **Other historical camera/backups — preserve pending review** | `autoxy_camera_refs_*`, `auto_xy_uninstall_backups`, `autoxy-*backup*`, other rollback and commissioning snapshots | No deletions until dependencies and uniqueness are assessed. |

**Avoid double-counting:** `auto_xy` (~143 MB) includes nested `auto_xy/print_gate_runs` (~140 MB); the nested directories are not extra space on top of their parent. `printer_data/gcodes` is separate and not in scope.

## Recommended staged retirement of dormant Trial04 *import symlink only*

Source: [`../scripts/rc16_retire_inactive_trial04_extension.py`](../scripts/rc16_retire_inactive_trial04_extension.py). Preconditions at execution time: exact installed legacy SHA256, expected symlink target, no active Trial04 section or include, RC16 loaded and **both** virtual-SD/resume API guards armed, idle/unloaded, native VAOC closed, no fault, and a safe existing AutoXY rollback directory.

1. Download a **pinned reviewed main revision** to an isolated `/home/pi/AutoXY_RC16_Cleanup` folder (see user-facing release instructions). This step does **not** install or update active AutoXY code.
2. Run `python3 -B scripts/rc16_retire_inactive_trial04_extension.py check` from the downloaded copy; if it refuses, leave the printer unchanged and review the exact message.
3. On safe, unloaded/idle printer run `python3 -B scripts/rc16_retire_inactive_trial04_extension.py retire`. It removes **only** the dormant symlink, creates `auto_xy/rollbacks/retired-inactive-trial04-*/receipt.json`, then rechecks RC16 status. It does **not** disable native RatOS processing, remove the old source, change an include, restart Klipper, or touch printer offsets/data.
4. Verify symlink absent, legacy section not loaded and RC16 `sd_api_guarded`/`resume_api_guarded` still true. For rollback: `python3 -B scripts/rc16_retire_inactive_trial04_extension.py restore --receipt <exact receipt path>`. Restore recreates only a **dormant** symlink; the old config include remains disabled.
5. Only after a separate supervised maintenance window, consider a *non-destructive* inventory of cross-references within historical directories, then off-device archive and independently checked cleanup of exact inactive backup trees. Do not give broad shell `rm -rf` or wildcards.

## Limits and next evidence

This was **one metadata-only audit**, not a forensic examination of every installed service or source-code execution path. It does not independently prove which live Python handler owns `PROCESS_GCODE_FILE`, nor inspect all outside-root RatOS includes. Keep protected independent-dual-tool negative-path testing in the supervised matrix. Confirm whether the dormant symlink retirement succeeded from its printed receipt and postcheck; then prepare a **separate archive-before-delete** plan for the large historical backups if the owner wishes to reclaim disk space.