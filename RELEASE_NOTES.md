# RatOS VAOC Auto XY — v0.1.0-beta.4.2 (OWNER-VALIDATED RELEASE CANDIDATE; NOT YET PUBLISHED)

**Alignment first:** Earlier owner tests recorded a first-write Y response gain 2.078 and a second-write X gain 2.372, with the final camera-measured XY alignment within ±0.030 mm. This test build treats a bounded, correctly directed but unusual gain as a **warning that requires verification**, not an automatic fault. It takes two additional camera-measured T0/T1/T0 observations with **no further offset writes**; both must remain in tolerance, agree within 0.015 mm, demonstrate ≤0.015 mm T0 return drift/scatter, and preserve confirmed saved offsets. These consume existing verification slots. Success still requires RatOS VAOC exit and state checks.

**Hard failures retained:** gross absolute observed change >0.11 mm, reverse response, outside-tolerance alignment following anomalous gain, unstable confirmations, unexpected saved-offset changes, or exhausted budgets. The original offset correction limits, safe rollback, inline retry restrictions, heating, pose guards and mode bypasses are unchanged. The friendly no-fault unhomed preflight from the latest beta.4.1 branch is included.

**Test coverage:** offline regressions replay the owner's observed first- and second-correction gains, normal convergence, and multiple unsafe cases (including restored saved offsets on failure); prior installer, SAVE_CONFIG, retention, uninstall, print-start and homing suites pass. **Physical RatRig owner validation on 25 October 2026 (NZ time):** unhomed manual start gave the intended non-fault warning; subsequent idle calibration completed `CALIBRATED_AND_VAOC_EXITED` with two stable settling checks, one correction, two verification attempts and last reported camera errors X +0.028226 mm / Y +0.000779 mm. `AUTO_XY_STATUS` showed ON, FAULT=0, IN_PROGRESS=0. The owner additionally reported that automatic calibration in an independent IDEX print start completed and printing began normally. **The print-start report and physical printed-registration results have not yet been supplied or reviewed.** Response-gain anomaly/remeasurement remains offline-tested only. Single-tool T0/T1 and COPY/MIRROR bypasses in this revision are covered offline but still await physical-owner confirmation. Full clean reinstall and retention pruning remain unverified on live hardware. One machine's results do not establish broader compatibility.

**Status wording:** `AUTO_XY_STATUS` now explains ON and OFF appropriately instead of always appending the sentence `OFF retains ...` when enabled. No calibration logic or safety thresholds were changed by this release-candidate wording correction.

**Installation note:** changing `klippy/extras/auto_xy_gate.py` requires a **full Klipper service restart** after the printer is safe and idle, not only a configuration or firmware RESTART. Verify the installed file matches the release and the new process start time before testing. Do not restart with an unresolved active VAOC session or fault; follow the recovery procedure first.

---

# Beta 4.1 homing preflight test update (unreleased)

Manual Auto XY now displays **"Printer must be homed first (G28)"** and does nothing else if XYZ is not homed. This is a non-fault prerequisite check, not an automatic homing command. During a real two-tool print start, the same missing prerequisite still **blocks the print** with an ordinary error (no persistent calibration fault), because silently skipping required calibration would be unsafe. An already latched fault or loss of homing after VAOC starts still fails closed.

The response-gain recheck, settling checks, offset safety, retention, and rollback logic remain unchanged. Verify first with a supervised idle, unhomed dry run, then home safely and perform a supervised calibration test. Not yet physically tested.

---

# RatOS VAOC Auto XY — v0.1.0-beta.4.1 (Unreleased hardware-test candidate)

**Beta 4 settling has been tested on the owner's RatRig:** two no-write settling cycles agreed within 0.015 mm (observed X change 0.0046 mm, Y change 0.0069 mm), then an initial offset write produced **Y response gain 2.078**. Despite the post-write X/Y camera errors being within tolerance (X +0.0165 mm, Y +0.0004 mm), the strict gain check stopped the run and the original saved offsets were successfully restored. The user manually recovered VAOC and fault state.

Beta 4.1 keeps the strict response guard, but handles one *marginal first-correction* overshoot: only one anomalous axis, <=0.020 mm write, gain in (2.0, 2.5], in-tolerance observed alignment, verified persisted offsets, and room for two additional verification passes. It performs **two complete T0/T1/T0 measurements without additional saved-offset writes**, requiring both readings to stay in tolerance, agree within 0.015 mm and demonstrate tight T0 return and image scatter (<=0.015 mm). The gate records a prominent warning when such an anomaly is reverified. Gross, reverse, ambiguous, or unstable responses remain fatal with a latched fault and saved-offset rollback attempt. The final RatOS VAOC exit and runtime checks still apply.

Offline simulations reproduce the **actual observed Y gain 2.078** and test guarded success alongside failed/out-of-range and unstable cases. The full earlier offline suite is retained. This build is **not yet confirmed on physical hardware**; use only for an observed, supervised commissioning run and keep Auto XY OFF for other prints until qualified. Saved-offset rollback does not prove live VAOC compensation: recover carefully on fault.

---

# RatOS VAOC Auto XY — v0.1.0-beta.4 (Unreleased test candidate)

This build adds **pre-adjustment settling**: after camera scale calibration, the gate observes two consecutive T0/T1/T0 measurement sequences that agree within 0.015 mm on both axes. It permits up to four no-write sequences, then **stops safely** if they do not stabilise. Existing gain limits remain active after real corrections.

The read-only failure checker now recognises a confirmed restoration of original saved offsets. This does **not** independently prove active VAOC compensation is synchronised.

All previous beta.3 image-retention, safe-install/uninstall, print-mode bypass, offset/write safety and single inline retry protections are retained. This is an **offline-tested, not yet hardware-validated** candidate. Supervise live tests; keep Auto XY OFF until commissioning.

---

# RatOS VAOC Auto XY — v0.1.0-beta.3 (Pre-release)

**What changed from beta.2**

- Automatically remove successful-run JPEGs after 7 days, failed-run JPEGs after 30 days, and completed calibration reports after 90 days, approximately daily while the printer is idle. Unknown/incomplete runs are not touched.
- New preview-only cleanup command: `python3 ~/auto_xy/stage6/auto_xy_retention.py --check`.
- New clean uninstall with an explicit full-purge option so owners can reproduce a from-scratch software installation. It backs up `printer.cfg` and `ratos-variables.cfg` outside the project directory and deliberately **preserves RatOS saved IDEX XY/Z offsets**.
- Safe edits continue to respect Klipper's generated SAVE_CONFIG block (beta.1 defect fixed in beta.2).
- New offline regression tests in GitHub Actions.

**This remains a hardware-specific experimental beta.** Test OFF/bypass before enabling camera motion; follow [docs/INSTALL.md](docs/INSTALL.md) and [docs/CLEAN_REINSTALL.md](docs/CLEAN_REINSTALL.md). Do not uninstall during a print, with VAOC active, or while a calibration fault is unresolved. No physical full-purge/reinstall validation has yet been completed.

---

# v0.1.0-beta.2 — Community installer safety fix

**Supersedes v0.1.0-beta.1. Do not install beta.1.**

A community tester found that the previous installer appended an Auto XY include to the very end of `printer.cfg`, including below Klipper's auto-generated `SAVE_CONFIG` section. That can prevent Klipper from recognising its saved values and cause future SAVE_CONFIG errors.

**Fixed in beta.2:** Both install and disable use guarded, idempotent configuration editing that keeps the generated `SAVE_CONFIG` section at the end. The original generated values are left untouched. There is now an explicit read-only check and opt-in repair command for affected beta.1 installs; a timestamped backup is made before edits. The installer refuses ambiguous/corrupted configs rather than trying to guess.

The VAOC calibration algorithm, dual-tool-only mode routing, Mainsail bypass, and inline one-time retry are unchanged from beta.1 (Stage 6.3.7). This patch passed offline regression tests and still needs supervised hardware validation.

**For anyone who ran beta.1**, follow `docs/INSTALL.md` → *Fixing a previous beta.1 install* before installing beta.2. Don't run SAVE_CONFIG simply to test this repair.

**Safety reminder:** This beta controls motion and writes saved toolhead offsets. Back up your settings and supervise initial tests. Supported camera configuration is currently hardware-specific; the project is not officially supported by RatOS.
