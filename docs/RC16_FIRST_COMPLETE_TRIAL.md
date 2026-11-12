# RC16 complete integration — first supervised printer trial

**Build status:** One integrated source tree, source checksums pinned, offline tests passed. This is a **supervised test candidate**, not approval for unattended printing or a production release. It must be tested on actual RatOS/VAOC hardware before promotion.

## One user-visible installation transaction

The installer is `scripts/rc16_release_install.py install`; it supports either an AutoXY-free RatOS installation or a **recognised** existing RC15 installation. It performs one complete fully ACTIVE install, not a temporary disabled stage or a series of printer-side patches.

A reproducible source download must be pinned to the commit SHA selected for the supervised candidate; do not install an unpinned moving development branch. After pinning a candidate, a single terminal paste can fetch the source to a temporary directory and invoke that installer. No slicer postprocessor, RatOS manual source edit or separate activation command is part of the normal workflow.

The installer performs source verification, known-install recognition, Klipper/RatOS preflight, exact snapshots and planned config migration, installs all printer-side components before one restart, verifies active protected-print handoff, native VAOC recommission UI, temperature menu, persistent AutoXY enabled/correction state and runtime guard ownership. It automatically restores the previous files/registration and restarts if a stage fails, subject to the rollback safety invariant.

**Important:** If the printer has an unknown partially installed AutoXY or an ongoing print/VAOC session, a refusal is a safety condition, not a request to manually bypass safeguards. The installer cannot guarantee compatibility with an unreviewed vendor change.

## SSH safety and installer logging

**Do not enter `set -euo pipefail` directly in an interactive SSH session.**
A failed download, failed preflight or unsuccessful restart verification can then
end the user's SSH shell immediately, appearing as a printer disconnection.
The installer itself requests a **Klipper service restart only**, not an OS reboot.

For a future trial, launch the entire installer in a **child Bash process**
and tee its output to a persistent log. The parent SSH shell must remain
interactive when the child exits unsuccessfully. The log must capture curl,
archive extraction, source verification, preflight, installation and rollback:

```bash
bash -c '
set -euo pipefail
src="$(mktemp -d /tmp/autoxy-rc16.XXXXXX)"
trap '\''rm -rf "$src"'\'' EXIT
curl -fsSL --retry 3 "https://api.github.com/repos/peteoak/ratos-vaoc-auto-xy/tarball/REPLACE_WITH_FROZEN_COMMIT" |
  tar -xz -C "$src" --strip-components=1
python3 "$src/scripts/rc16_release_install.py" install
' 2>&1 | tee "$HOME/autoxy-rc16-install.log"
printf 'Installer child exit code: %d\n' "${PIPESTATUS[0]}"
```

The command above is **a template only**, not a printer-ready installation
instruction: replace the placeholder with the reviewed frozen commit and
qualify recovery from the observed SSH failure before attempting another
installation. Never enable the shell's `errexit` option in the parent SSH
terminal. Do not immediately rerun the installation to diagnose an unexplained
disconnect.

After reconnecting, collect read-only diagnostics:
```bash
printf '\nBOOT\n'; uptime; who -b
printf '\nSERVICES\n'; systemctl is-active ssh klipper
printf '\nRC16 INCLUDE\n'; grep -nF 'auto_xy_rc16.cfg' "$HOME/printer_data/config/printer.cfg" || true
printf '\nKLIPPER LOG\n'; tail -n 40 "$HOME/printer_data/logs/klippy.log" 2>/dev/null || true
printf '\nROLLBACK SNAPSHOTS\n'; ls -lt "$HOME/auto_xy/rollbacks" 2>/dev/null | head -n 10
```

## No piecemeal installation

One package includes:
- G-code file classifier and generated protected-print processor
- active native RatOS handoff interceptors including direct virtual-SD guards
- base AutoXY calibration controller and both VAOC recovery paths
- persistent camera worker, tuned `two_stage_roi`, photo-signature validation, manual VAOC commissioning disambiguation
- Mainsail Start/Position/Capture/Save/Finish/Cancel nozzle-recommissioning macros and extension
- Mainsail RatOS / slicer-derived / fixed camera-temperature selection
- automatic soft-budget cleanup for routine camera and generated-print images
- protected active T0/T1 reference photographs; superseded reference generation and abandoned-session 30-day cleanup
- exact transactional rollback snapshots and status verification

## Supervised sequence — one install, then test the complete solution

1. Run exactly one fully active installation on an idle/cool machine, preferably in a supervised session with SSH and Mainsail visible. Collect the installation receipt and confirm READY.
2. Verify status panel entries and macro availability: Recommission Nozzles, T0/T1 capture, Save, Finish, Cancel, temperature selector and native manual VAOC.
3. Recommission both nozzles once using real full-frame images. Record that six reference JPEGs and independent T0/T1 centres/radii/signatures are present; original or previous profile remains recoverable.
4. Verify normal single-tool and COPY/MIRROR prints bypass nozzle XY calibration.
5. Start a small independent two-tool print. Confirm fresh homing after motor disable, normal RatOS bed heatsoak, camera measurements before Beacon and extrusion, bounded correction and verification.
6. Perform another independent print after motors disable. Confirm fresh alignment; do not reuse prior measurements even if legacy reuse opt-in persisted.
7. Deliberately create a safe **no-write** detection failure during a supervised trial. Confirm the original file is held, one retry when supported, manual VAOC fallback without postprocessing, guarded continuation and raw M24/do_resume bypass refusal.
8. Check failed-nozzle behaviour and invalidation (motor off, timeout, changed file, offsets). Do not force a post-write fault; the current safety contract correctly refuses same-print recovery after a write that cannot be reconciled.
9. Confirm automatic idle cleanup protects the currently referenced six JPEGs, retains old generations for 30 days, and never removes a selected/paused generated G-code file.
10. Review logs, actual X/Y accuracy, nozzle shape reliability, camera load on Pi 4 and recovery prompt responsiveness before accepting the build as release qualified.

## Explicit limits before qualification

- Hardware accuracy, Pi 4 resource load, actual RatOS registered command ownership, native VAOC recovery behaviour and rollback after real Klipper startup failure cannot be proven by GitHub CI.
- Commissioning candidate disambiguation and photographic appearance-match thresholds have synthetic tests but **must be validated against original unannotated images**. A refused result is preferable to a false calibration.
- Native Mainsail Pause/Cancel responsiveness during synchronous gated calibration is a **known queueing limitation**; fail-close and emergency stop remain distinct. Resolve or explicitly qualify this operational limitation before calling the first full release complete.
- A recoverable pre-write failure can hold/resume the same source print. A failed calibration following an unreconciled offset write **aborts**, rather than pretending a held-file manual resume is safe.
- No unattended protected printing until every critical test passes and captured evidence confirms the correct ordering and offset integrity.
