# Security and physical-safety reports

This is experimental printer control software. If a bug could enable **unexpected toolhead motion, heater control, a corrupted persisted offset, bypass of a safety latch or a configuration vulnerability**, please **do not trigger it intentionally on a running printer**.

Report sensitive security findings privately via GitHub **Security → Report a vulnerability** (if enabled by the repository owner), or contact the maintainer through an explicitly listed private channel once one is added. Avoid posting credentials, tokens, printer access URLs, private full configurations, identifying details, or diagnostic camera captures in a public Issue.

If the printer is unsafe now, stop and isolate it using normal physical controls, do not enable calibration, and follow the [rollback and recovery guide](docs/INSTALL.md) after reaching a safe state.
