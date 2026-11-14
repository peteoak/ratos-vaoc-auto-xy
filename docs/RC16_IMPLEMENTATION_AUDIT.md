# AutoXY RC16 — requirements-to-code audit (11 November 2026)

> **HISTORICAL ENGINEERING RECORD — not current installation or operational guidance.** Requirements audit dated 11 November, before the current main merge and later printer validation; status labels are historical. For current behaviour and remaining qualification use [RC16 Start Here](RC16_START_HERE.md) and [RC16 Release Status](RC16_RELEASE_STATUS.md).

**Review scope.** Recovered project-conversation decisions, earlier AutoXY documents available in the user's project/Library, RC15/RC16 source and current automated tests. Earlier chat retrieval is not a complete verbatim export of every conversation: this audit must not be described as a line-by-line reading of every historic chat.

**Release state:** RC16 has a unified one-command active installer, but remains a development branch, not qualified for unattended hardware printing. A green offline suite does not prove compatibility with this RatOS printer. All code-status claims below refer to branch `dev/rc16-resource-aware-integration`.

Status key: **CODE** = implemented and covered by automated tests; **PARTIAL** = implementation exists but requirement not complete; **HARDWARE** = source-level path exists but target-printer validation outstanding; **MISSING** = not implemented; **SUPERSEDED** = an older proposal replaced by a later binding decision.

## User's final operational intent

| Requirement | Status | Evidence / remaining concern |
| --- | --- | --- |
| One-step, fully active installation; no disabled staging or subsequent activation | **CODE/HARDWARE** | Unified `scripts/rc16_release_install.py` detects fresh/upgrade and installs the fully active package; old staging remains an internal offline utility. Actual Pi restart not yet qualified. |
| Recognise the owner's modified RC15 controller rather than reject its SHA mismatch | **CODE** | `scripts/rc16_active_migration.py` pins both reviewed controller hashes. Unknown beta variants still rejected. |
| Transactional backup, install, restart, activation, health verification, automatic rollback | **CODE/HARDWARE** | `scripts/rc16_stage_upgrade.py`, `scripts/rc16_active_install.py`; offline success/failure simulations pass; hardware restart/rollback untested. |
| Clean active-only fresh install on an entirely uninstalled printer | **CODE/HARDWARE** | Unified release installer performs baseline preparation and complete active RC16 installation before a single Klipper restart; simulated registration rollback covered by offline tests. |
| Preserve native RatOS, Beacon, VAOC, camera URL, settings, saved X/Y/Z offsets | **CODE/HARDWARE** | Filesystem backup and no direct offset writes in installer; newly added recommission coordinator inherits existing gate camera URL. Verify on printer. |
| No slicer postprocessor or manual conversion to use protected IDEX prints | **CODE/HARDWARE** | `src/auto_xy_ratos_handoff_rc16.py` and `src/tools/rc16_prepare_worker.py` intercept normal RatOS processing; real start-route matrix not hardware qualified. |
| Protect *all* virtual-SD print-start and held-resume paths | **CODE/HARDWARE** | Guards PROCESS_GCODE_FILE, SDCARD_PRINT_FILE_BASE, M23, M26, M24, direct SDCARD_PRINT_FILE and do_resume. Exact runtime command registration must be verified on RatOS target. |
| Only independent two-tool jobs trigger camera calibration; single-tool, COPY, MIRROR bypass | **CODE/HARDWARE** | Mode classifier and offline processor/handoff tests. Physical print matrix required. |
| New print after steppers disable always homes, heatsoaks, measures, corrects and verifies again | **CODE/HARDWARE** | Protected gate explicitly disables reuse, receives motor-disable/home invalidations; actual repeat-print tests remain mandatory. |
| ±0.030 mm XY goal; two consecutive confirmations, bounded 10 corrections/6 checks, max 0.300 total and 0.050 each step | **CODE/HARDWARE** | `src/auto_xy_gate_rc16.py` and inherited RC15 tests. Any correction accuracy is not proven by static code tests. |
| Keep `AUTO_XY_RUN_NOW` independent from held-print continuation | **CODE** | Existing controller modes and macro paths. |
| Hold original file if auto calibration fails, offer one bounded retry or native manual VAOC, then guarded same-file resume | **CODE/HARDWARE** | Protected-session state machine and recovery commands. Still needs a supervised physical failure/recovery print. |
| No stale failed-VAOC context; faults remain latched until inspected/acknowledged | **PARTIAL/HARDWARE** | Recovery state markers, checks and popup exist; prior stale-context concern requires real recovery test. |
| 45-minute protected recovery timeout and motor/file/offset integrity invalidation | **CODE/HARDWARE** | Protected gate timestamp and invalidation guards; hardware confirmation outstanding. |
| Mainsail progress/popups, meaningful faults, retry/manual recovery actions | **PARTIAL** | Console messages and popup code exist, but native UI queue behaviour during synchronous START_PRINT remains unproven. |
| Normal RatOS heating, bed heatsoak before camera, camera before Beacon probing | **CODE/HARDWARE** | Existing integration hooks; physical thermal ordering not yet confirmed. |
| Optional slicer-derived independent T0/T1 printing temperature minus configurable reduction (default 50 C); ±2 C for 5 s | **CODE/HARDWARE** | Thermal code in gate with original target restoration; real overshoot and no-ooze behaviour not qualified. |
| Native Mainsail temperature-strategy dropdown and fixed absolute-temperature strategy | **CODE/HARDWARE** | Mainsail button prompt plus individual `AUTO_XY_TEMPERATURE_*` macros choose RatOS / slicer-derived per-tool reductions / fixed per-tool targets. It is a button menu, not a literal dropdown; hardware thermal response untested. |
| Preserve original settling path, 0.80s camera pair and settling, tested two-stage ROI + persistent worker | **CODE/HARDWARE** | Default example and known owner's installed settings; active upgrade preserves current settings rather than force-overwrite alternate tester settings. |
| Full camera frame; never solve ambiguity with a permanently tighter user ROI | **CODE/PARTIAL** | New recommission uses `roi: auto`, production `two_stage_roi` uses its own moving precision crop. Legacy detector and RC15 example still include superseded tighter-ROI error/advice. |
| Different nozzle rim sizes for T0/T1 (within supported pixel range) | **CODE** | Separate commissioning radius/centre, tests; unevenly worn rims remain an accuracy risk. |
| Use actual saved commissioning reference images to identify nozzle versus reflections | **PARTIAL/HARDWARE** | Three photographs per nozzle create compact radial appearance signatures; RC16 camera worker checks signature alongside original tuned `two_stage_roi` geometry. The saved photographs are not individually template-matched at runtime. Threshold accuracy on original unannotated nozzle images remains unqualified. |
| Beta tester's two candidate-circle commissioning failure | **PARTIAL/HARDWARE** | Native-manual-VAOC-only full-frame candidate contrast/reticle fallback and repeated-frame checks are implemented with fail-closed synthetic tests; real unannotated images and noncentral reticles need supervised validation. Normal `two_stage_roi` precision code remains unchanged. |
| Mainsail Recommission Nozzles launches native VAOC, positions manually, captures T0/T1, publishes replacements | **CODE/HARDWARE** | `config/auto_xy_recommission_rc16_macros.cfg`, `src/auto_xy_recommission_rc16.py`, `src/tools/rc16_recommission_worker.py`; includes cancel/status, 3 raw JPEGs per tool, atomic profile backup, no offset writes. No physical VAOC test yet. |
| Do not reload T0 unnecessarily immediately after native _VAOC_START | **CODE/HARDWARE** | Mainsail T0 macro skips a second load at initial STARTING phase; hardware confirmation outstanding. |
| Reference frames used by active profile never removed by ordinary storage cleanup | **CODE** | `src/auto_xy_retention_rc16.py`, protected reference library outside run tree, independently tested. |
| Previous references: 30 days from successful replacement, then automatic deletion; failed replacements preserve old references | **CODE** | Retirement record, separate idle cleanup worker `src/tools/rc16_reference_retention.py`, integrity checks and interrupted-cleanup replay tests. |
| Aborted/unpublished capture sessions should never accumulate indefinitely | **CODE** | Separate idle cleaner now expires only trusted, unreferenced abandoned session JPEGs after 30 days, with active/archive verification and fail-closed unknown-file handling. |
| Soft combined image target (currently 512 MiB), allow captures to exceed, opportunistic idle reclamation | **CODE** | Image retention planner counts protected references; deletes only eligible old run images, not current references. |
| Successful/failed/diagnostic image and report caps; reports not kept indefinitely | **CODE** | Per-category limits plus age limits in retention; active scheduled cleanup. |
| Generated protected G-code automatically cleaned without deleting a selected/paused/printing file | **CODE/HARDWARE** | `src/tools/rc16_generated_retention.py` and coordinator registry/lock. Needs live test. |
| Print end disables motors and invalidates last nozzle alignment | **PARTIAL/HARDWARE** | RatOS machine's existing end-of-print motor disable is a printer-specific dependency; AutoXY handles invalidation but does not itself guarantee the machine executes motor-off. |
| Native Mainsail Pause/Cancel immediate responsiveness during synchronous print-start | **MISSING / known limitation** | Older checklist explicitly states UI Pause/Cancel commands can queue behind synchronous gate. Not claimed fixed. |
| RC15 five-visit positioning, diagonal/dogleg, experimental camera registration default ON | **SUPERSEDED** | These remain unapproved experiments; production intent is proven original settling and independently verified Hough precision, not automatic promotion. |
| Legacy in-place retry as sole failure response | **SUPERSEDED** | Final design requires held-file recovery with manual VAOC and one guarded retry; legacy inline retry stays OFF. |

## Specific release blockers

1. **Real print-start and recovery qualification:** RatOS registered command/API ownership and all direct virtual-SD start/resume paths need supervised validation on the printer.
2. **Camera commissioning and appearance-match qualification:** Full-frame, manually centred ambiguity fallback and radial photo-signature check are now coded, but have only synthetic validation. Test original unannotated reflective images, diverse nozzles and Pi 4 load before declaring the automatic candidate acceptance reliable.
3. **Mainsail recommission workflow:** Implemented and offline tested; not yet demonstrated with actual native VAOC, camera captures and two different nozzles on RatOS.
4. **Thermal, motor, Beacon and saved-offset behaviour:** Need a supervised positive and failed independent IDEX print, followed by another print after motors disable.
5. **Installer qualification:** Both fresh and reviewed RC15 active paths now exist and pass simulated transactions, including RatOS extension rollback. Actual target restart, installer recovery and unknown beta variants remain unqualified.
6. **Optional requested temperature strategy UI:** Dropdown/fixed strategy not implemented; distinguish from existing working RatOS and slicer-derived modes.
7. **Automatic orphan cleanup:** The 30-day abandoned-session safety rule is code-tested but actual idle scheduling and recovery after interrupted Mainsail sessions still need real-printer tests.

## Test limitations

CI runs offline on GitHub runners, not on the user's Pi 4/RatOS/USB camera. Five historical skips include older staging cases and an optional Trial04 replay requiring an archived fixture. An earlier 25-change hardware endurance result relates to RC15 and does not qualify the new RC16 handoff and recovery path.

## Historical requirement reconciliation

- The Oct 2026 documents describing **measure-only first** are supervised commissioning instructions, not the intended ongoing operational state. The final owner installation is active with automatic correction, preserving fault checks.
- Early docs recommending a tighter camera crop conflict with the later full-frame decision; retain full `roi: auto` and the internally aligned `two_stage_roi` precision crop.
- Previously proposed calibration reuse is overridden for every protected independent print because motors turn off at every print end.
- Older B4.x inline-retry workflows do not meet the final held-original-file/native-manual-VAOC recovery contract.
- A successful source-level RatOS API probe does not prove exact runtime gcode command registration.

**Do not represent RC16 as fully released or advise unattended protected IDEX prints until the blockers are verified.**
