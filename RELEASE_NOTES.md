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
