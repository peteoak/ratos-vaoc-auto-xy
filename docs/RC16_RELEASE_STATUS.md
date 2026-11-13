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
