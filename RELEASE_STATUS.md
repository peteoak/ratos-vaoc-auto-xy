# RC15 dev6 — current release-candidate status

**Published source:** GitHub `main`, merged via PR #6. This remains a development release candidate, not a generally validated stable release.

## Verified

- Repository contains the RC15 dev6 retention implementation, redundant T0-load removal after native VAOC start, and the validated machine defaults: `two_stage_roi`, persistent worker, original settling path, 0.80-second camera pair gap and settling, commissioned detector guard, protected print disabled by default until supported automatic preparation is available.
- The owner's RatRig V-Core 4 300 IDEX completed a backed-up quarantine and fresh install from GitHub, followed by a reviewed update to the corrected controller/config. The installed controller SHA256 matched the packaged source; Klipper returned Ready and loaded the expected settings.
- Existing camera commissioning profile was restored intact; camera device and JPEG snapshot endpoint were confirmed working. Saved offsets matched the pre-update backup. AutoXY was re-enabled without measure-only, latched fault or pending retry.
- Offline printer test suite: 39 tests run, 34 passed, 5 skipped (historical migration cases and optional archived optical replay). GitHub Actions for corrected source commit `d4e0b20b8f1bbe018653462e69baecc9db7e0f28` passed compilation and unit tests.
- Previously supervised dev5 endurance testing reached 25, 50 and 100 tool changes on this one printer; those results are not new dev6 hardware qualification.

## Not yet verified or incomplete

- First physical calibration/eligible print using the dev6 T0-reload fix.
- Actual scheduled retention deletion on the live printer; retention policy and unit tests are verified, not live expiry behaviour.
- Complete failed-calibration recovery prompt and same-file protected-print continuation for this deployed release.
- Transparent protected-print wrapping of ordinary unmodified slicer files without a post-processor or equivalent prepared-file integration.
- General-purpose scripted safe upgrades/uninstall and cross-machine compatibility.
- Upstream RatOS macro licensing/provenance and attribution review (see `THIRD_PARTY_NOTICES.md`).

## Safety and publication boundaries

The fresh installer is **fresh-only**; do not run it over an existing AutoXY. Keep a machine-specific backup and a tested rollback plan before changes. Protected print is configured OFF by default to preserve ordinary RatOS print starts. AutoXY enablement still requires deliberate operator action and valid commissioning. The experimental protected-print feature is opt-in and requires separately prepared two-phase guard commands. Never commit private camera profiles, calibration images, reports, host logs or `ratos-variables.cfg`. Avoid publishing a stable/multi-printer validation claim.
