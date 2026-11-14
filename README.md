# AutoXY RC16 — RatOS 2.1 IDEX nozzle XY calibration

**Current main-branch code: RC16 supervised release candidate (controller `0.5.0-rc16-dev1`).** AutoXY is a third-party, printer-specific integration for the RatRig V-Core 4 IDEX with native RatOS VAOC. It is **not yet qualified for unattended protected printing or for arbitrary camera/nozzle setups**. RC16 has completed multiple supervised calibrations on the project printer; remaining safety and cross-machine tests are tracked in the [live qualification status](docs/RC16_RELEASE_STATUS.md).

**Start here:** [RC16 installation and operation guide](docs/RC16_START_HERE.md) · [Latest release/qualification status](docs/RC16_RELEASE_STATUS.md) · [Diagnostics and reports](docs/RC16_DIAGNOSTICS.md) · [Historical documentation index](docs/HISTORICAL_DOCUMENTS.md)

## What RC16 does

- Performs fresh camera-guided T0/T1 XY alignment for **independent dual-tool IDEX jobs only**. Single-tool, COPY and MIRROR follow native RatOS, without AutoXY calibration.
- Preserves the native RatOS G-code processor and the original slicer file. Protected processing generates a separate, timestamped file; the print cannot proceed past its calibration barrier unless alignment is verified.
- Requires fresh homing, heating/heatsoak, nozzle measurement and two consecutive XY verifications after a prior print has disabled steppers. A previous successful result cannot substitute for a new calibration.
- Targets ±0.030 mm per XY axis, with bounded adjustments, verification retries and fail-closed checks.
- Uses an independent coarse geometric locator plus Hough detection. One uniquely plausible circle does **not** have to match its commissioning photograph; when **exactly two** geometry-qualified circles remain, reference appearance contributes at most 25% of candidate selection. Unresolved ambiguity fails closed.
- Can perform targeted per-axis camera-scale recapture when cross-tool scale disagrees by over 12%, and bounded **fresh** HTTP snapshot retries on transient 500/502/503/504 responses.
- Records actual photo-selection decisions, camera HTTP attempts, scale diagnostics and correction measurements in `report.json`. Image storage is managed with a **soft** aggregate target (512 MiB by current defaults), not a hard cap.
- Supports a guarded, one-use continuation using *native manual VAOC* when an eligible protected print is safely recoverable; a standalone `AUTO_XY_RUN_NOW` has **no print to resume**.

## Installation: choose the correct path

**Do not run an installer just because you cloned `main`.** AutoXY controls real nozzle movement and saved offsets. Run only on compatible RatOS 2.1 hardware with working native VAOC, under supervision, with a complete off-device backup, idle/clear printer and an understood rollback.

| Starting state | Supported path in this tree | Critical restriction |
| --- | --- | --- |
| No AutoXY installed | `scripts/rc16_release_install.py check`, then `install` only after supervised approval | A fresh device requires nozzle/camera commissioning before trusting calibration |
| Recognised installed RC15 Dev6 | Same RC16 active installer, which checks recognised source variants and snapshots rollback | Unknown edits, partially installed systems or unsafe printer state are refused |
| **Already running RC16** | **Do not run the fresh/RC15 installer.** Use a specifically version-pinned, idle-only RC16 refresh after its `check` passes | If the installed files are already current, no update is required |
| Need an exact original RC15 Dev6 package | Use the immutable [RC15 frozen branch](https://github.com/peteoak/ratos-vaoc-auto-xy/tree/release/rc15-dev6-frozen), **not current `main`** | RC15's signed baseline is preserved separately for history/rollback |

For exact examples and commissioning steps, follow the [RC16 start-here guide](docs/RC16_START_HERE.md). An installer **must refuse** an unrecognised installation; never delete installed files or bypass preflight to make the check pass.

## Typical supervised print operation

1. Confirm the camera profile and native VAOC are commissioned and AutoXY has no unresolved fault.
2. Start a normal independent **two-tool** print using RatOS/Mainsail; AutoXY's guarded file processing and calibration occur automatically when enabled.
3. After fresh homing and the thermal preflight, AutoXY measures T0/T1 and may apply bounded XY corrections. It requires two consecutive in-tolerance verifications before releasing the protected print to RatOS.
4. If automatic calibration fails, **do not bypass the hold with M24/Resume**. Use the supported guarded retry or native manual VAOC recovery and explicit continuation, or cancel safely.
5. For a new print after steppers disable, repeat fresh homing and calibration rather than reusing prior measurements.

**Accepted operating limitation:** Mainsail's ordinary Pause/Cancel can be delayed during synchronous calibration. For an unsafe physical situation use Klipper **Emergency Stop**, which invalidates calibration and requires a normal safe recovery. See [Pause/Cancel design record](docs/RC16_PAUSE_CANCEL_INTEGRATION_DESIGN.md).

## Qualification and compatibility

The main-branch source was promoted after a GitHub test run of **370 tests (5 skipped)** and successful supervised calibrations/recovery on the owner's machine. This is **not** a claim of universal physical accuracy, complete unsafe-start resistance on every RatOS build or unattended readiness. Original, unannotated images from the beta tester's difficult two-circle scene have not yet been replayed against the final guarded detector; see [qualification status](docs/RC16_RELEASE_STATUS.md).

The normal production camera path requires a properly commissioned `two_stage_roi` profile. Pi architecture/OS-specific OpenCV/NumPy compatibility must be checked; the RC16 dependency versions are pinned for the reviewed candidate but are **not certified on every Pi architecture**.

## Legacy release integrity

The archived RC15 README and `MANIFEST.json` are preserved byte-for-byte in [`docs/legacy/`](docs/legacy/) and in the **`release/rc15-dev6-frozen`** branch. The root `MANIFEST.json` remains an RC15 *compatibility input* for the RC16 installer and is not an RC16 release manifest. The separate [RC16 source inventory](docs/RC16_SOURCE_MANIFEST.json) describes current RC16 additions. Changing a landing page on `main` does **not** rewrite or qualify the historical RC15 release.

**Nozzle offsets, printer-specific camera profiles and reference photographs must never be bundled as universal defaults.**

## Attribution

See [LICENSE](LICENSE), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), and the RatOS/VAOC requirements in the installation guide.
