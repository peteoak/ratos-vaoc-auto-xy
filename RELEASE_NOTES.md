# v0.1.0-beta.1 — Community testing release

**Code baseline:** Stage 6.3.7. **Status:** Experimental / pre-release, not official RatOS.

## Included

- Klipper-hosted post-bed-heatsoak/pre-Beacon calibration gate, keeping normal RatOS print flow.
- Camera-based T0/T1 calibration and persisted X/Y correction, with bounded movement/writes.
- Mode-aware routing: dual-tool independent jobs only; single T0/T1, COPY, MIRROR bypass.
- AUTO_XY_TOGGLE master control in Mainsail, persistent across restarts.
- Up to 10 correction writes, 6 verification-only checks per attempt, and two successful checks required.
- One automatic same-print retry for recoverable measurement/convergence failures, cumulative 0.30mm cap across attempts.
- Installation check, backups, read-only recovery checker and disable helper.
- Offline tests, GitHub Action, structured community feedback templates and installation/rollback docs.

## Testing basis

Earlier integrated dual-tool printing tested successfully on one V-Core 4 IDEX under Stage 6.3.3; COPY bypass verified on that printer under Stage 6.3.7. Current offline Stage 6.3.7 simulations pass. Other printing modes and the automatic in-place retry still require independent hardware testing. No claim of general unattended reliability.

## Important limitations

- `/home/pi` and RatOS 2.1 print-start macros assumed and checked by installer.
- Camera Hough detector fixed to a 1920x1080 centered downward VAOC view; default ROI and nozzle radius must match physically.
- Printing-temperature thermal shift and actual material registration need physical verification.
- Do not test on unfamiliar hardware without competent manual VAOC calibration and verified mechanical clearance.
- The GPL-3.0 license choice must be reviewed/approved by the repository owner before public publication.

See [README](README.md), [Safety](docs/SAFETY.md), [Install / rollback](docs/INSTALL.md), [Community tests](docs/TESTING.md) and [Publishing](docs/PUBLISHING.md).
