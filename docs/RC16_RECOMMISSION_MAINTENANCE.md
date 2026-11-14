# RC16 Mainsail — Recommission Nozzles

**Development status:** Macro/controller and image-retention backend exist in the RC16 branch, with offline tests. Do not deploy to a live printer until the active RC16 installer and native VAOC integration are qualified.

## When it is needed

Use after changing either nozzle, noticeable wear/damage, a changed camera setup, or repeatable loss of detection reliability. No slicer postprocessor, SSH script or separate maintenance installation should be required in production.

## Expected Mainsail actions

1. With printer idle, no selected/paused file or pending protected recovery, home both carriages through the normal RatOS procedure. The camera-safe nozzle measured/target temperature limits still apply.
2. Select **AUTO_XY_RECOMMISSION_NOZZLES**. It opens native RatOS `_VAOC_START` without automatically altering saved offsets. Initial T0 loading is not repeated unnecessarily.
3. Select **AUTO_XY_RECOMMISSION_T0**, and use the ordinary native VAOC camera and controls to position T0 visually. Choose **AUTO_XY_RECOMMISSION_CAPTURE_T0**. Wait for STATUS to report three captured full-frame JPEGs.
4. Select **AUTO_XY_RECOMMISSION_T1**, position T1 manually, then **AUTO_XY_RECOMMISSION_CAPTURE_T1**. Wait for three images.
5. Check the camera view and that the correct nozzle was observed for both captures. Select **AUTO_XY_RECOMMISSION_SAVE** as the explicit operator confirmation. The background worker validates all six frames and independently commissioned T0/T1 centres/radii. No automatic guessing when multiple circles are plausible.
6. When STATUS reports PUBLISHED, select **AUTO_XY_RECOMMISSION_FINISH** to exit native VAOC. A failure permits **AUTO_XY_RECOMMISSION_CANCEL** once background work has ended; the current profile is retained.
7. Complete a supervised AutoXY measurement before trusting corrections after changed nozzle geometry. Recommissioning does NOT automatically clear a latched fault, rewrite XYZ offsets, or certify extrusion-axis alignment.

The public Mainsail controls are `AUTO_XY_RECOMMISSION_NOZZLES`, `AUTO_XY_RECOMMISSION_T0`, `AUTO_XY_RECOMMISSION_CAPTURE_T0`, `AUTO_XY_RECOMMISSION_T1`, `AUTO_XY_RECOMMISSION_CAPTURE_T1`, `AUTO_XY_RECOMMISSION_SAVE`, `AUTO_XY_RECOMMISSION_FINISH`, `AUTO_XY_RECOMMISSION_CANCEL`, and `AUTO_XY_RECOMMISSION_STATUS`.

Capture uses the **complete camera frame** (ROI auto). The existing production two-stage ROI detector still performs its *internal*, moving, precision crop. Never use a tighter fixed user camera crop to work around a double-circle error.

## Storage contract

- **Current active T0/T1 reference images:** six raw unaltered JPEGs, protected indefinitely while referenced by the active profile. They count toward total storage volume but are exempt from soft-quota eviction.
- **Replaced reference generation:** exact backup profile plus previously referenced JPEGs retained for 30 days **from successful replacement**, regardless of original creation date; then idle scheduled cleanup may retire them.
- **Failed replacement:** old active references remain active; their 30-day retirement clock does not start.
- **Cancelled/unpublished image capture sessions:** after at least 30 days of inactivity, cleanup may remove only trusted files not referenced by an active or archived profile. Unknown or corrupt archive state protects all affected files.
- **Ordinary calibration images:** independent successful/failed/diagnostic age and soft combined budget controls.
- **Generated protected G-code:** separate retention, never selected/paused/printing files.

## Known limitations (must not be concealed)

1. RC16 now derives an independent compact radial appearance signature from each tool's three reference JPEGs and checks it alongside the tuned Hough `two_stage_roi` geometry on later captures. This is not full photograph-to-photograph template matching. Original unannotated nozzle images and Pi 4 hardware tests must validate thresholds and false rejections.
2. A conservative manually positioned VAOC full-frame reticle/contrast fallback exists for multiple commissioning circles. It refuses uncertain selections. The beta tester's original unannotated images, unusually worn nozzles and alternate camera reticles are not yet hardware-qualified.
3. The Mainsail sequence has not yet been proven against actual printer homing, _VAOC native state, USB camera, offset preservation and service restarts. Do not treat green offline CI as live qualification.
4. Camera positioning is still a manual VAOC decision. Image-centre repeatability does not by itself prove the physical extrusion-axis centre on an asymmetrically worn nozzle.

## Implementation

- `config/auto_xy_recommission_rc16_macros.cfg`
- `src/auto_xy_recommission_rc16.py`
- `src/tools/rc16_recommission_worker.py`
- `src/tools/rc16_reference_retention.py`
- `src/auto_xy_commission_candidates_rc16.py`
- `src/auto_xy_reference_signature_rc16.py`
- `src/auto_xy_vision_worker_rc16.py`
- `src/auto_xy_retention_rc16.py`
- `scripts/rc16_active_migration.py`
- `scripts/rc16_stage_upgrade.py`
