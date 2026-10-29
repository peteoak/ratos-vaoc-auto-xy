# Contributing to the community beta

Thanks for testing and helping improve compatibility. The safest contributions are **reproducible, small, reviewed changes**.

Before reporting a bug, run `python3 -m unittest discover -s tests -p 'test_*.py' -v` in a checkout with its Python vision dependencies installed. This runs offline tests only; compare the installed AutoXY version and relevant report excerpts to the published commit.

Please include the printer model, RatOS/Klipper revision, camera resolution/mount/ROI, mode and toolshift count, installer check result, what was expected, what happened, whether any offsets were saved, and **redacted** report excerpts. Use the GitHub test-report template for successful tests and bug-report template for failures.

Pull requests affecting calibration movement, thermal limits, retry/fault handling, saved offsets, or print-start hooks should include relevant new offline tests **and** a safe hardware validation plan. Do not remove guards merely to pass tests. All changes should continue to fail closed when an installation or measurement cannot be trusted.

PRs should not include personal printer configuration (`printer.cfg`, `ratos-variables.cfg`), remote access secrets, images without consent, or full G-code files that may contain private model data.

Review the repository GPL-3.0 licence and THIRD_PARTY_NOTICES.md before submitting contributions. Adapted upstream RatOS macro attribution remains under review. Contributions are not guaranteed to be accepted without review, especially when they widen supported hardware claims.
