# Hook-only AutoXY protected recovery: offline feasibility test

**Status: experimental analysis, fail-closed. Do not install these files as printer macros.**

## Actual RatOS 2.1 startup finding (owner's active START_PRINT, Nov 2026)

The monolithic RatOS `START_PRINT` macro executes its bed wait/heatsoak, invokes
`_USER_START_PRINT_AFTER_HEATING_BED { rawparams }`, then immediately invokes
`_START_PRINT_AFTER_HEATING_BED`, bed-mesh hooks, Beacon-related probing and
further hotend/print preparation *within the same expanded macro command stream*.

The existing AutoXY hook already runs `AUTO_XY_GATE MODE=PRINT`. This correctly
places automatic measurement before Beacon probing. It does **not** provide a
continuation boundary for manual intervention after a recoverable failure.

## Why the proposed six-command hook does not safely hold the print

`M25` acts on the virtual-SD **reader**, not on G-code that Klipper has already
expanded into commands from `START_PRINT`. Inserting
`AUTOXY_PRINT_BEGIN/PREPARE/CALIBRATE/HOLD/CHECK/FINISH` in the hook therefore
cannot guarantee RatOS stops after HOLD. Its subsequent
`_START_PRINT_AFTER_HEATING_BED`, probing/heating and other startup commands
could run ahead of native manual VAOC recovery.

The protected implementation also checks that BEGIN occurs **before heatsoak**;
moving it into the post-heatsoak hook breaks the existing invariant.

## Offline test implementation

Run `python3 scripts/audit_hook_recovery.py /path/to/exported-start-print.gcode.txt`
to inspect a local macro export. It intentionally exits nonzero and refuses
to certify hook-only safe continuation. Run
`python3 -m unittest tests/test_hook_recovery_feasibility.py` for unit checks.
These tests do not trigger any motion, heaters, offsets, printer commands or
changes to RatOS.

## Safe architecture candidates (not deployed)

1. **Existing protected-file path:** RatOS post-processes first; AutoXY inserts
   independent file-level BEGIN/PREPARE/CALIBRATE/HOLD/CHECK/FINISH commands,
   preserving a genuine virtual-SD pause boundary. This already exists as an
   offline converter but automatic invocation is not yet integrated.
2. **Redesigned split-phase startup:** RatOS startup must be refactored so that
   the post-bed checkpoint ends the active G-code stream and an independently
   validated continuation runs only when the controller authorises it. Must
   preserve RatOS heatsoak, initial-tool selection, Beacon probing, mesh,
   temperature waits, G-code state, LED/fan settings and release on fault.
   A user hook in the existing monolithic macro **cannot do this alone**.

No safe live-printer hook-only hold-and-resume implementation is offered by
this branch. Do not conflate a popup display with a protected print hold.
