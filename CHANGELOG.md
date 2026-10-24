# Stage 6.3.7 — Only independently printed two-tool jobs calibrate

Relative to Stage 6.3.6:

- **Single-head T0 and single-head T1 prints now bypass VAOC**, including when the selected RatOS printing mode is `IDEX_SINGLE`.
- Dual-tool independent prints (`IDEX_SINGLE` with `TOTAL_TOOLSHIFTS > 0`) still run automatic calibration.
- COPY and MIRROR bypass unchanged.
- Classification happens at the early `_USER_START_PRINT` hook, using the original carriage mode **and** the `TOTAL_TOOLSHIFTS` slicer/postprocessor parameter, before RatOS changes carriage state.
- The later after-bed-heatsoak hook consumes the one-use classification and cannot mistake COPY/MIRROR for an independent print.
- The installer additionally confirms installed RatOS `START_PRINT` uses `TOTAL_TOOLSHIFTS` to make the same one-tool vs two-tool decision. If source code differs, installation refuses.
- Existing `AUTO_XY_TOGGLE` bypass, 10 corrections, 6 verification attempts, one immediate inline PRINT retry, 150°C preheat, 155°C camera maximum, and ±0.03 mm tolerance are unchanged.
- Zero or **missing** `TOTAL_TOOLSHIFTS` results in bypass, matching RatOS's `default(0)` behaviour. For a genuine two-tool print, ensure your RatOS slicer postprocessor supplies this count. Calibration cannot infer tool use by looking at the currently selected carriage.

**Testing:** Local offline simulations cover single T0/T1 bypass, COPY/MIRROR bypass, dual independent calibration, stale/missing mode handling, installation preflight, bounded adjustments, retries and fault handling. This release has **not been validated on the physical printer**.
