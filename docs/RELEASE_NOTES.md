# Suggested GitHub pre-release notes — v0.5.0-rc15-dev5

This is a community feedback **pre-release**, not stable or multi-machine verified.

Features: automatic camera-based IDEX XY calibration after heatsoak, independent repeatability checks and bounded offset corrections, native manual VAOC fallback, persistent camera worker, opt-in two-stage detector with grid-aligned cropped precision stage, opt-in original/experimental settling paths, configurable RatOS/slicer-derived thermal modes, measure-only diagnostics, and no-write 25–100-tool-change endurance diagnostics. Normal calibration verification tolerances are separate from endurance drift ceilings.

Owner hardware results on RatRig V-Core 4 300 IDEX, RatOS 2.1: successful 25, 50 and 100 change runs (100 changes, 101 visits, 202 images), stable saved offsets throughout endurance, VAOC exited normally. One HTTP camera fetch error recovered via retry in full test. The earlier transient T1 MCU timing shutdown has not recurred on dev5 but was not individually root-caused. Results do not guarantee compatibility on other machines or prove 150°C full endurance performance.

New users: fresh-install preflight and one-include configuration, OFF/uncommissioned initially. Existing users should **not** overlay the fresh installer on any previous AutoXY; migration from public beta requires a carefully tested plan. Nozzle-temperature GUI dropdown and fixed target option remain future work. Issues and PRs with sanitized hardware/commissioning logs are welcomed.

Do not publish until adapted RatOS macro licence/provenance is reviewed. Do not upload local printer configuration, camera profile, owner-specific offsets, photo archives or logs.
