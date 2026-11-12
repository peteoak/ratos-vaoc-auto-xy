# RC16 complete integration — first supervised printer trial

**Build status:** One integrated source tree, source checksums pinned, offline tests passed. This is a **supervised test candidate**, not approval for unattended printing or a production release. It must be tested on actual RatOS/VAOC hardware before promotion.

For current observed hardware progress and remaining acceptance criteria, see [RC16 release status](RC16_RELEASE_STATUS.md). Ordinary Mainsail Pause/Cancel is **not guaranteed responsive while calibration owns the G-code queue**. The [asynchronous redesign](RC16_PAUSE_CANCEL_INTEGRATION_DESIGN.md) is specified but not yet implemented or hardware-qualified; do not use Pause/Cancel as an emergency-stop substitute.

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
RatOS operators whose sudo requires a password are supported: the installer
prompts via the interactive SSH terminal using `sudo -v` **before any printer
file, virtual environment or saved-state changes**, and the restart uses
ordinary `sudo systemctl restart klipper`, not `sudo -n`. The password is
handled by sudo itself and is not echoed or written to the installer log.
Run the installer from an actual interactive SSH terminal, not a detached
job or an unattended scheduler. If authentication fails, the installer
refuses before changing printer files. A refreshed sudo prompt can appear
during rollback if credentials expire.

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
verify the 18-file rollback and saved-state recovery from the failed
previous installation before attempting another installation. Never enable the shell's `errexit` option in the parent SSH
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

## Migration of previously latched RC15 flags

An *existing* RC15 AutoXY installation is normally enabled. The upgrade
does not ask the operator to switch off AutoXY or delete saved variables.

When the printer is confirmed idle and cool, no virtual-SD file is selected,
and there is no held/paused print or active native VAOC, an old latched
AutoXY fault, in-progress marker or retry token is treated as **stale state**.
The one-command active installer:

1. Reads the original enable/measure-only/fault/in-progress/retry flags and
   the exact existing RatOS XYZ saved offsets.
2. Archives that original state in the transaction's rollback directory
   **before changing any flag**; all printer config and installed sources
   remain backed up by the same installation transaction.
3. Installs RC16, restarts Klipper and checks that saved state and offsets
   have not changed during preparation.
4. Rechecks that no print is selected or in progress, then clears **only**
   obsolete AutoXY fault/retry/in-progress markers.
5. Enables active automatic correction through the reviewed AutoXY macro,
   checks runtime handoff readiness and preserves all saved XYZ offsets.
6. If a verification or state reset step fails, rolls back the installation
   and directly restores the original saved flag values, even if the old
   controller's normal enable macro would refuse a latched fault.

This never silently resumes or discards an uncertain held print.
Physical motion, active heating, a selected print with stale recovery markers,
or an unknown controller version still causes a safe preflight refusal.

The installer is intentionally more conservative than a simple
`SAVE_VARIABLE ... VALUE=0` script, because the latter loses the diagnostic
and cannot restore the original state if a restart or upgrade fails.

## Camera Python environment: part of the complete install

The live upgrade uses the exact `python:` interpreter configured in the
existing `[auto_xy_gate]` section rather than assuming that every RC15
installation has `/home/pi/auto-xy-venv/bin/python`.

- If that interpreter exists and imports OpenCV/NumPy, its location is preserved
  for both the calibration controller and Mainsail recommissioning.
- If only the standard isolated `/home/pi/auto-xy-venv` directory is **entirely
  absent**, read-only preflight reports a planned provision. The one-command
  installer may create it using verified, pinned, binary-only NumPy/OpenCV
  wheels before replacing printer files. If the installer fails and the
  original printer configuration is restored, the newly created environment
  is removed.
- If an existing environment is incomplete, its Python cannot import
  dependencies, or a custom configured interpreter path is missing, preflight
  refuses. It never silently overwrites existing environments or installs
  packages into the system/Klipper Python.
- Pi architecture/Python wheel availability cannot be established just from a
  GitHub runner. If compatible binary wheels are unavailable, preparation
  fails before modifying printer configuration. Do not use pip's source-build
  fallback on the Pi as a workaround.

This is a single integrated installation path, not a second manual
environment-install step.

## Klipper restart and Moonraker reconnect handshake

After `systemctl restart klipper` returns, the Klipper service may already be
running while Moonraker still reports HTTP 503 `Klippy Host not connected`.
The installer must not treat this normal brief disconnect as a terminal
installation failure, nor attempt saved-state access during that gap.

The RC16 active upgrade, fresh install and their rollback paths now wait
up to 120 seconds for a readable `printer/info` in READY state and
`save_variables` status. Temporary connection errors and 503 responses
are retried; confirmed Klipper `error` or `shutdown` fails immediately.
Timeout fails closed and triggers rollback rather than activating correction.
After rollback, the same reconnect handshake is required before restoring
archived flags. This does not bypass later runtime and offset integrity checks.


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

## Required nozzle alignment during RC16 reference-image commissioning

**While carrying out the RC16 Recommission Nozzles workflow, align/position each
nozzle accurately in the native RatOS VAOC camera window immediately before
capturing that nozzle's reference photographs.** This is part of the same
workflow, **not a separate VAOC procedure to perform beforehand**. Merely
having the nozzle visible somewhere in the window is not sufficient.

In Mainsail, with the printer idle, no held or paused print, and temperatures
within the camera-safe limits:

1. Select `AUTO_XY_RECOMMISSION_NOZZLES`. This opens native RatOS VAOC;
   use its normal controls to home and position the tools as required.
2. Select `AUTO_XY_RECOMMISSION_T0`. **Use native VAOC positioning/alignment
   controls to place T0 accurately at the correct camera reticle/crosshair,
   as for normal VAOC alignment.** Once positioned, select
   `AUTO_XY_RECOMMISSION_CAPTURE_T0`. Wait for capture to finish and
   confirm T0 shows **3/3** reference images.
3. Select `AUTO_XY_RECOMMISSION_T1`. **Using native VAOC, accurately align/
   position T1 to the same intended reference/crosshair location and confirm
   its correct XY relationship to T0 before capturing.** Then select
   `AUTO_XY_RECOMMISSION_CAPTURE_T1`. Wait for T1 to show **3/3**.
4. Only after both tools have been aligned/positioned correctly **during
   those capture steps** should you select `AUTO_XY_RECOMMISSION_SAVE`.
   Wait for confirmation that references were published; select
   `AUTO_XY_RECOMMISSION_FINISH` to close native VAOC.

The RC16 ambiguity resolver assumes the native VAOC reticle is at the
**centre of the full camera frame**. If the displayed reticle is elsewhere,
do not force an ambiguous capture. If a nozzle is not positioned or aligned
correctly, correct it **while still in the recommissioning workflow**, then
capture its images again before saving.

**Important distinction:** `AUTO_XY_RECOMMISSION_SAVE` publishes the
camera-reference photographs and profile; it does **not** write or adjust
saved XY nozzle offsets. The operator's accurate native-VAOC positioning
during the capture workflow is what makes the references meaningful.
A successful `Recommission saved` message is not, by itself, proof that
nozzle XY correction has been verified. Confirm automatic alignment
subsequently with a supervised protected two-tool print.

## Supervised sequence — one install, then test the complete solution

1. Run exactly one fully active installation on an idle/cool machine, preferably in a supervised session with SSH and Mainsail visible. Collect the installation receipt and confirm READY.
2. Verify status panel entries and macro availability: Recommission Nozzles, T0/T1 capture, Save, Finish, Cancel, temperature selector and native manual VAOC.
3. **During RC16 nozzle recommissioning**, use native VAOC to accurately position/align each tool at the camera reticle **immediately before its reference-image capture** (T0, then T1), following the procedure above. Record six reference JPEGs and the separate T0/T1 centres/radii/signatures; the previous profile remains recoverable. Saving the images is not a nozzle-offset correction.
4. Verify normal single-tool and COPY/MIRROR prints bypass nozzle XY calibration.
5. Start a small independent two-tool print. Confirm fresh homing after motor disable, normal RatOS bed heatsoak, camera measurements before Beacon and extrusion, bounded correction and verification.
6. Perform another independent print after motors disable. Confirm fresh alignment; do not reuse prior measurements even if legacy reuse opt-in persisted.
7. Deliberately create a safe **no-write** detection failure during a supervised trial. Confirm the original file is held, one retry when supported, manual VAOC fallback without postprocessing, guarded continuation and raw M24/do_resume bypass refusal.
8. Check failed-nozzle behaviour and invalidation (motor off, timeout, changed file, offsets). Do not force a post-write fault; the current safety contract correctly refuses same-print recovery after a write that cannot be reconciled.
9. Confirm automatic idle cleanup protects the currently referenced six JPEGs, retains old generations for 30 days, and never removes a selected/paused generated G-code file.
10. Review logs, actual X/Y accuracy, nozzle shape reliability, camera load on Pi 4 and recovery prompt responsiveness before accepting the build as release qualified.

## RC16 tolerant nozzle likeness update (supervised candidate)

The RC16 photographic reference is a **geometric aid**, not a requirement
that the hot nozzle reproduce the exact lighting and centre-face appearance
of its clean commissioning photograph. A supervised trial showed that
ordinary possible filament ooze can obscure the centre while the nozzle rim
is still found consistently. Rejecting solely because the six-band full-face
appearance score changed defeats the purpose of reference-assisted detection.

The updated camera verifier uses the existing `radial-v1` reference format
but compares the **outer rim's normalised radial structure** rather than
the entire centre face. The original, independent coarse annular locator,
unique full-resolution Hough circle, commissioned position/radius limits
and capture consistency checks **remain mandatory**. The full-face
difference is retained as a diagnostic rather than a hard rejection.
A missing, flat, optically incompatible or ambiguously located rim must
still be rejected. This change does not enlarge the XY calibration
tolerance, change offsets, ignore detector failures or require capturing
new commissioning references.

An existing RC16 installation must NOT be rerun through the RC15-to-RC16
release installer. Instead, `scripts/rc16_vision_refresh.py check` and
`install` perform an **exact-source, idle-only, single-file transaction**
on the installed camera signature verifier. It creates a rollback snapshot,
restarts Klipper once and verifies native protected-print guards, restoring
the original verifier and restarting on a failed transaction.
Recommissioned profile images and saved offsets are unchanged.

After a recovered manual VAOC session, the persistent AutoXY enabled flag
may still be OFF. The refresh readiness check intentionally allows this;
**the operator must explicitly enable AutoXY** using `AUTO_XY_ON` when
ready for the next supervised protected-print test, after confirming
no recovery/fault remains pending.

## Explicit limits before qualification

- Hardware accuracy, Pi 4 resource load, actual RatOS registered command ownership, native VAOC recovery behaviour and rollback after real Klipper startup failure cannot be proven by GitHub CI.
- Commissioning candidate disambiguation and photographic appearance-match thresholds have synthetic tests but **must be validated against original unannotated images**. A refused result is preferable to a false calibration.
- Native Mainsail Pause/Cancel responsiveness during synchronous gated calibration is a **known queueing limitation**; fail-close and emergency stop remain distinct. Resolve or explicitly qualify this operational limitation before calling the first full release complete.
- A recoverable pre-write failure can hold/resume the same source print. A failed calibration following an unreconciled offset write **aborts**, rather than pretending a held-file manual resume is safe.
- No unattended protected printing until every critical test passes and captured evidence confirms the correct ordering and offset integrity.
