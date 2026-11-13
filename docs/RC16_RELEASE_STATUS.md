# AutoXY RC16 — supervised integration status (November 2026)

RC16 integrates native RatOS print-start protection, camera-guided T0/T1
recommissioning, automatic image retention, and guarded native VAOC manual
recovery when automatic alignment cannot safely complete.

This is an **integrated supervised release candidate**, not a production
release approved for unattended two-tool printing.

## Hardware evidence so far

- Full active installer and initial Klipper/RatOS runtime preflight completed.
- Both nozzle references saved in native VAOC, three original full-frame
  photographs each, with an integrity check of all six JPEGs.
- One automatically gated print failed at T0 photographic appearance matching.
  RC16 held the protected print, and native VAOC manual recovery succeeded.
- The too-strict full-face appearance check was revised to a tolerant
  *outer-rim likeness check* without changing the geometric Hough alignment,
  commissioned circle limits or XY correction mathematics. Offline tests
  and GitHub CI passed.
- A subsequent supervised automatic calibration with similar T0 ooze
  completed with one offset adjustment and two independent verifications.

These observations are not proof of every error path, physical nozzle
accuracy on every printer, or unattended reliability. Established XY
correction and convergence testing from earlier candidates is not repeated
solely because the optical appearance check changed.

## Remaining release qualification

**Accepted operating limitation — not a release blocker:** The owner
accepts Klipper's existing G-code queue behaviour: standard Mainsail
Pause/Cancel may wait until synchronous calibration finishes. In an
unsafe situation, use **Emergency Stop**, which shuts down Klipper
and invalidates any active calibration. Do not attempt ordinary Resume
after an emergency stop; restart with normal homing and the guarded
calibration sequence. The [optional future asynchronous design](
RC16_PAUSE_CANCEL_INTEGRATION_DESIGN.md) is archived for reference only.

1. **Automatic idle retention:** the soft global image quota and protections
   for active runs, reference images, changed reports and generated-print
   files have offline tests. Verify that scheduled execution and its storage
   totals behave correctly on the target Pi.
2. **Difficult beta nozzle scenes:** original unannotated images, not
   screenshots containing detector overlays, are needed to establish
   correct circle choice when more than one candidate is present.
3. **Release packaging and qualification:** keep RC15's signed package
   manifest and README *unchanged*. The new RC16 release needs its own
   pinned source manifest, documentation landing page, publishable version
   and supervised operational signoff; do not merge or tag a fully
   qualified release while the above conditions remain unresolved.

## Readable generated G-code filenames

RC16 now supports a human-readable generated filename that preserves a
sanitised original part name and appends the **Pi-local processing date/time**
with six digits of fractional seconds. Example:

`__AutoXY_RC16_Dragon_Head_20261113-174530-123456.gcode`

It remains a separate generated copy; the source G-code filename and bytes
are not renamed or overwritten. COPY/MIRROR bypass remains native; protected
independent print-start still uses the verified source registry and one-use
handoff. Older `__AutoXY_RC16_<24-hex>.gcode` files remain authenticated
for reprints and age-based cleanup, and cannot be mistaken for original
unprotected files.

Already-running RC16 installations must **not** run the RC15-to-RC16
installer. The narrowly scoped
`scripts/rc16_timestamped_gcode_refresh.py check|install` performs
an exact-source, idle-only three-file transaction with rollback snapshot
and post-restart protection checks. It refuses unreviewed or mixed source
versions and active/selected prints.

## November 13 supervised vision reliability revisions (offline candidate)

A failed original automatic calibration rejected unique Hough-located T0
circles solely for photographic likeness (outer-rim scores 0.427/0.511
against the previous hard 0.380 cutoff). The protected inline retry
instead failed the **12% cross-tool camera-scale check** before offset
measurement or correction. Manual native VAOC guarded continuation worked.

Three narrow mitigations are implemented **for validation**, without
changing the known-working XY correction algorithm, nozzle alignment
tolerance, motion bounds, old commissioning reference files or reference
profile format:

1. **Geometry first.** A unique Hough circle satisfying the existing
   independent coarse annulus, commissioned location and radius is no
   longer vetoed by an appearance fingerprint. Only if **exactly two**
   geometrically eligible circles remain does photographic evidence
   enter a conservative 75% geometry / 25% photograph score. Unresolved
   pairs fail closed. The signed RC15 detector file is untouched;
   RC16 imports the separate `auto_xy_detector_rc16.py`.
2. **Camera-scale uncertainty.** When T0/T1 physical-motion-to-pixel
   scale disagrees by more than the existing 12%, only the suspect
   axis is repeated: two complete 3-point sweeps per tool, with origin,
   fitting, within-tool repeatability and cross-tool consistency checks.
   No XY offsets are written during recapture. If the new evidence is
   inconsistent the same protected print remains held and manual VAOC
   remains available.
3. **HTTP camera errors.** A snapshot request with server HTTP
   500/502/503/504 can make two additional fresh HTTP requests within
   the original capture timeout. It never reuses stale JPEGs and leaves
   detector-rejection errors untouched.

The existing installed RC16 is **not** upgraded with any full RC15/RC16
installer. The pinned script
`scripts/rc16_vision_reliability_refresh.py check` and then `install`
is an idle-only, rollback-verified update that replaces three known
RC16 modules and adds one new RC16-only detector. It refuses modified,
mixed or unknown installed source versions. Both CI and real original
frame replay are required before qualifying these revisions for
unattended printing.

## Installation and operator information

Existing RC16 must not be overwritten by the old RC15 or fresh RC16
installer. The reviewed RC16 likeness update has its own
`scripts/rc16_vision_refresh.py` read-only check and transactional
idle-only one-file refresh. No new nozzle commissioning is required for
that update. Follow the
[first supervised trial documentation](RC16_FIRST_COMPLETE_TRIAL.md)
for operator workflow and limitations.

The original RC15 README remains exactly as published and is included in
its integrity manifest. Do not rewrite it as part of an RC16 document update.
