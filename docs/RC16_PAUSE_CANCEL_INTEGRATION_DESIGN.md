# RC16 protected calibration — responsive Pause/Cancel design

> **HISTORICAL ENGINEERING RECORD — not current installation or operational guidance.** Unimplemented optional asynchronous design; the accepted current runtime still uses synchronous calibration and may queue ordinary Mainsail Pause/Cancel. For current behaviour and remaining qualification use [RC16 Start Here](RC16_START_HERE.md) and [RC16 Release Status](RC16_RELEASE_STATUS.md).

**Status:** Optional future architecture only. **Not installed or active.**
The owner has explicitly accepted standard Klipper Pause/Cancel command queueing
as an RC16 operating limitation, with Emergency Stop for unsafe conditions.
This optional design is **not a release blocker**, is not scheduled for RC16,
and must not be portrayed as a required safety fix.
Do not describe ordinary Mainsail Pause or Cancel as immediately responsive
during the current synchronous `AUTO_XY_GATE`.

## Current, confirmed limitation

RC16 performs multi-image calibration through a synchronous Klipper G-code
command during protected RatOS print-start. It uses bounded
`reactor.pause()` waits so that Klipper services its reactor, but this
**does not release the G-code dispatch queue**. Klipper's `gcode/script`
interface explicitly queues a new script behind an ongoing command. Calling
`PAUSE`, `CANCEL_PRINT`, `M25`, a macro, or a G-code-driven popup action
is not a reliable immediate interruption while `AUTO_XY_GATE` executes.
Neither shorter `reactor.pause()` intervals nor a G-code abort flag alone
fixes the queueing problem.

Klipper's separate **emergency_stop / M112** mechanism remains available
for an immediate unsafe situation. Emergency stop shuts down Klipper; it is
**not** a resumable pause and invalidates the measurement. A held protected
print must not be resumed after shutdown without a fresh guarded start.

## Optional future asynchronous architecture for responsive Mainsail Pause/Cancel

1. After normal RatOS bed heatsoak and before Beacon and extrusion, record
   the exact protected source identity (name, hash, selection, session ID),
   original saved/runtime offsets, thermal state, homed axes and tool state.
   Persist the fail-closed, in-progress marker before changing offsets.
2. Stop the original virtual-SD stream at a deliberately inserted guarded
   barrier and return control to Klipper's G-code dispatcher **before** the
   long camera calibration starts. Keep the original file selected and
   paused, and retain the RC16 generated-file shared lock for the entire
   held lifetime.
3. Advance calibration using small, reactor-scheduled **nonblocking stages**:
   homing/VAOC positioning at safe boundaries, independent camera snapshots
   in subprocesses, detector results, correction and verification. Never
   perform long `run_script_from_command` blocks or block the G-code queue
   on camera I/O. Any motion/tool/offset operation must still be serialized
   with the native Klipper motion/G-code and RatOS state machines.
4. A normal Mainsail Pause during automatic calibration must acknowledge
   promptly and enter `PAUSE_REQUESTED`. Complete only a safely committed
   motion boundary, then freeze measurement, disable further correction
   writes and report `CALIBRATION_PAUSED`. **Do not resume the original
   file** or mark camera alignment verified merely because the request was
   acknowledged. Revalidate nozzle temperatures, carriage positioning and
   tool state before any explicit resume; after stepper disable/rehome
   invalidate and restart calibration from fresh measurements.
5. Mainsail Cancel must promptly enter `CANCEL_REQUESTED`, suppress any
   queued worker responses, cancel or terminate workers safely, restore or
   reconcile offset writes under the existing write-intent invariant, exit
   native VAOC if motion is safe, release protected file ownership only
   after confirming the virtual-SD file is actually closed, and leave no
   route to original-file extrusion. Preserve fault markers on unresolved
   reconciliation.
6. Every worker result is bound to a unique generation/session token and
   post-movement position/temperature identity; stale replies after
   pause/cancel/rehome/shutdown are refused. Reference images and failure
   reports remain eligible for ordinary safe retention.
7. Guard `M24`, direct virtual-SD resume APIs, file selections/seeks and
   external RatOS print-start routes **during every** phase. Only a
   verified calibration or an explicitly accepted *fresh* native manual
   VAOC may issue a one-use guarded release. An ordinary UI Resume must
   not release an unverified original file.
8. Preserve separate standalone `AUTO_XY_RUN_NOW` behaviour: it has no
   held source G-code or continuation to release. Stopping it must never
   construct a synthetic protected print or present a Resume action.

## Fail-closed invariants (non-negotiable)

- No new extrusion until the protected original file is explicitly released.
- No partial/uncertain offset write can be treated as a clean paused state.
- A timeout, lost camera, changed source hash, or inconsistent saved/runtime
  offset state must disable automatic continuation.
- Ordinary Pause/Cancel must not depend on a second G-code command executing
  *while the calibrator still owns* the synchronous G-code call.
- No G-code or printer configuration changes as part of a cosmetic UI fix.
- Do not roll this architecture into the installed candidate before isolated
  Klipper simulator tests and supervised RatOS hardware qualification.

## Acceptance tests if this optional architecture is ever implemented

1. While T0/T1 images are being captured, issue ordinary Mainsail Pause;
   assert responsive acknowledgement, no new offset writes, file still held.
2. While worker I/O is delayed, issue Cancel; assert prompt cancellation,
   no post-cancel motion/write and original file never released.
3. Force a stale successful worker response after Cancel; reject it.
4. Pause after verified correction but before file release; disallow unguarded
   M24/do_resume and check the explicit release token.
5. Inject a failure immediately before and after the first offset write;
   confirm only pre-write recovery permits native VAOC manual continuation.
6. Disable a stepper/rehome mid-pause; ensure camera verification is stale
   and the next protected print freshly homes/calibrates.
7. Test standalone `AUTO_XY_RUN_NOW`, normal single-tool, COPY and MIRROR.
8. Test klippy shutdown, Moonraker disconnect, hotend safety, and selected
   generated-file retention across every possible transient state.
9. Verify interface responsiveness and no extra Pi 4 load under heavy
   camera processing, plus manual emergency-stop operation.

**Release classification:** The existing Klipper queueing limitation is
accepted for RC16. Any broader unattended-print qualification depends on
other RC16 release checks, not on implementing this optional redesign.
