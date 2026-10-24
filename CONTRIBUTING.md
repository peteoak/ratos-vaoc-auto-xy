# Contributing to the community beta

Thanks for testing and helping improve compatibility. The safest contributions are **reproducible, small, reviewed changes**.

Before reporting a bug, run `python3 test_stage6_offline.py` against the version you have installed, if your printer is safely idle. Compare your local file revisions with the GitHub release tag.

Please include the printer model, RatOS/Klipper revision, camera resolution/mount/ROI, mode and toolshift count, installer check result, what was expected, what happened, whether any offsets were saved, and **redacted** report excerpts. Use the GitHub test-report template for successful tests and bug-report template for failures.

Pull requests affecting calibration movement, thermal limits, retry/fault handling, saved offsets, or print-start hooks should include relevant new offline tests **and** a safe hardware validation plan. Do not remove guards merely to pass tests. All changes should continue to fail closed when an installation or measurement cannot be trusted.

PRs should not include personal printer configuration (`printer.cfg`, `ratos-variables.cfg`), remote access secrets, images without consent, or full G-code files that may contain private model data.

The copyright/license statement is proposed as GPL-3.0. Contributors should review it before submitting contributions. Contributions are not guaranteed to be accepted without review, especially when they widen supported hardware claims.
