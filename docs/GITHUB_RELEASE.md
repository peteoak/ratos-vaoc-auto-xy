# GitHub publication and ongoing verification

## Current state

The RC15 dev6 correction was merged into `main` through [PR #6](https://github.com/peteoak/ratos-vaoc-auto-xy/pull/6), merge commit `b56320ec75abba577fd7f1a91a306725419bbd1a`. Earlier instructions to stage the dev5 review branch or treat publication as not attempted are historical and no longer describe the current repository.

The corrected source commit `d4e0b20b8f1bbe018653462e69baecc9db7e0f28` passed GitHub Actions compilation/unit tests. An independent GitHub Actions result specifically for the merge commit was not confirmed. The owner's printer completed a fresh GitHub install and reviewed controller/config update, with hash matching and Klipper Ready; that does not constitute first-print validation of the T0 fix.

## Release qualification checklist

- [x] Corrected sources and manifest merged to `main`.
- [x] GitHub Actions compilation and unit tests passed on corrected source commit.
- [x] Owner's single RatRig installation and loaded configuration checked.
- [x] Camera commissioning profile and saved offsets preserved across reviewed update.
- [ ] First physical two-tool print with revised initial T0 selection verified.
- [ ] Live retention deletion observed and audited.
- [ ] Failure retry, native VAOC prompt and held-print continuation tested end-to-end on deployed release.
- [ ] Cross-printer compatibility evidence obtained.
- [ ] Review third-party RatOS macro provenance, licence and attribution in `THIRD_PARTY_NOTICES.md` before any stable/public-general-release claim.
- [ ] Inspect tracked test fixtures for any private machine information.

## Distribution and safety

The guarded `scripts/install_autoxy.py` installer only accepts a fresh installation. **Do not run it over a working AutoXY installation.** Existing installs require backup, review, safe retirement and rollback. Do not include machine-specific `printer.cfg`, `ratos-variables.cfg`, commissioned camera data, personal images, diagnostic `report.json` or host logs in repository commits.

Protected print is disabled in the example configuration so ordinary RatOS printing is not blocked by the experimental protected-print prerequisite. Enabling protected print requires a correctly prepared two-phase virtual-SD print file. The option does not automatically inject guard commands into arbitrary unmodified slicer files. AutoXY itself remains disabled until explicitly commissioned and enabled. Do not label dev6 as stable or universally tested based on this single-printer deployment.
