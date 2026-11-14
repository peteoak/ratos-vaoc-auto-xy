# RC16 source audit: private Trial04 handoff (2026-11-09)

> **HISTORICAL ENGINEERING RECORD — not current installation or operational guidance.** Private Trial04 source audit, retained as evidence for why the later guarded integration was designed. For current behaviour and remaining qualification use [RC16 Start Here](RC16_START_HERE.md) and [RC16 Release Status](RC16_RELEASE_STATUS.md).

**Status:** sanitized engineering findings. Source received privately; **the Trial04 implementation is not being copied to GitHub**. No active printer modification.

## Offline verification completed

The uploaded archive contains Trial04's Klipper handoff extension, local installer, documentation, and simulation tests. `python3 -m unittest discover -s tests -q` reported **57 tests, OK**; `py_compile` for handoff/installer passed. This proves simulated behaviour only. There is no real Klipper reactor, RatOS CLI run, USB camera or MCU timing validation.

## Confirmed resource contention risks

Trial04 intercepts RatOS `PROCESS_GCODE_FILE` with a Klipper-side handler and **synchronously** executes:

1. `stage_file`: SHA-256 pass on source; large source copy; second SHA-256 on source; third SHA-256 on staged file; plus `fsync`.
2. Full staged-file `read_text` for RatOS-header inspection.
3. `ratos.process_gcode_file` (stock RatOS processor invocation), within the same Klipper handler.
4. Full processed-file `read_text`, regex parsing, full-string AutoXY G-code replacement, and `atomic_write` with `fsync`.
5. Another full staged-file SHA-256 when writing the reprint registry; another digest on the generated file if a last-generated-file reprint is requested.
6. Only then `v_sd.cmd_SDCARD_PRINT_FILE` opens the staged file.

These are potential reactor stalls and large memory allocations. A six-second Klippy stats gap with elevated Klippy CPU time was previously observed around a `Timer too close` shutdown; this supports investigation of blocking work but does **not** prove which specific function was responsible.

## Architectural blockers beyond performance

- The wrapper only covers starts routed through `PROCESS_GCODE_FILE`. Direct `SDCARD_PRINT_FILE` and other bypass paths are explicitly outside Trial04. RC16 cannot advertise universal protected-start coverage until the bypass matrix is proven.
- Calling the existing RatOS method synchronously may still block Klipper even if AutoXY's file copy and hashing move into a worker. The RatOS CLI must remain unrestricted, but the bridge must arrange non-blocking supervision through supported interfaces; do not move RatOS into the AutoXY low-priority subprocess.
- Trial04 expects a particular RatOS postprocessor header/metadata and the owner's existing startup hooks. This must be made version- and hook-compatible before a tester release.
- Generated files and registry entries remain indefinitely in Trial04; integrate crash-safe retention only after current-print/reprint ownership checks.
- The installed RC15 fresh-only installer is not an RC15→RC16 migration tool. Preserving commissioning, saved nozzle offsets and restoring an existing installation remain mandatory release gates.
- Trial04's tests do not establish native manual VAOC failure recovery, same held-file continuation or rehoming invalidation on live hardware.

## RC16 implementation rule

Use **two independent resource domains**:

- AutoXY-owned staging, streaming metadata inspection, checksum and protection transforms run in an **external worker** with reduced scheduling priority and bounded-memory streaming.
- The *unmodified* native RatOS processor runs at normal priority, with its original options and without AutoXY-imposed CPU/I/O limits. An asynchronous coordinator must supervise the sequence and fail closed.

A short Klipper coordinator may poll immutable worker outcomes, validate a single print-request generation, and authorise verified virtual-SD handoff. It must never perform whole-file work, block on `communicate()/wait()`, or release unknown/stale/changed files.

## Required tests before enabling print interception

Run old Trial04 57-test simulation suite as a compatibility reference, plus new RC16 streaming-worker tests. Add large-file memory/CPU and worker-kill tests, duplicate/reprint/mode matrix, native processed-file identity, nested and direct virtual-SD starts, restore of baseline RatOS standalone behaviour, and real printer Klipper stats under loaded starts.

**No source from the private Trial04 ZIP, personal G-code, printer config, offsets, logs, images or test host information is included in this document.**
