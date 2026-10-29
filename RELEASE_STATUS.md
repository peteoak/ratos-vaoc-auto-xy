# RC15 dev5 — GitHub review snapshot

**Code version unchanged from live RC15 dev5**. Last hardware validation supplied by owner: 2026-10-29, 25/25, 50/50, 100/100 endurance tool changes with normal calibration verified, saved offsets unchanged during endurance and VAOC exited. All measurements from one printer. Long-term uptime, camera conditions and fresh multi-machine installations remain unqualified.

The files under `src/auto_xy_{gate,detector,vision_worker}.py` are **byte-identical** to the previously packaged dev5 release. No motion, heat, detection, state-machine, measure-only or calibration behavior changes are introduced by this GitHub snapshot.

The integrated fresh installer is adapted from the earlier guarded RC14 installer, with new RC15 source paths and version and a mandatory confirmation token; its offline preflight tests are included. It **must not be used to overwrite the owner's installed RC15 dev5**. Existing users use `scripts/check_installed_dev5.py`.

Known work not yet delivered: GUI dropdown / fixed absolute nozzle strategy; validation of alternate nozzle temperatures (including 150°C); fresh multi-machine installs; licence/provenance confirmation; community feedback. Experimental settling and existing thermal strategy options remain off by default.

This is **not** a tag or public GitHub release until somebody with repo credentials reviews and pushes it.
