# RC15 dev6 — retention candidate

RC15 dev6 implements the bounded image/report/export retention policy on the `autoxy-rc15-dev6-storage-retention` branch. The previously validated camera and calibration movement paths are retained from dev5 apart from retention scheduling/integration changes; dev6 has not yet been tested on the printer.

Pre-release validation gates: manifest integrity, Python offline tests, retention dry-run against synthetic data, clean installation checks, safe uninstall planning, and supervised real printer commissioning. **Do not uninstall an existing working AutoXY until a clean reinstallation procedure and complete backup have been verified.**

Camera profile, private results and saved offsets must not be committed. Stock RatOS files must not be overwritten. Attribution review for adapted RatOS macros remains open.
