#!/usr/bin/env python3
"""Offline fail-closed feasibility check for an AutoXY hook-only recovery design.

Consumes a locally exported START_PRINT G-code macro, NOT a printer connection.
No printer commands are executed. No user configuration is uploaded.
"""
import argparse
import json
import re
from pathlib import Path

HOOK = "_USER_START_PRINT_AFTER_HEATING_BED"
FOLLOW = "_START_PRINT_AFTER_HEATING_BED"

def _command_lines(source):
    return [(n, line.strip()) for n, line in enumerate(source.splitlines(), 1)
            if line.strip() and not line.lstrip().startswith(("{%", "{#", "#", ";"))]

def inspect_start_print(source):
    commands = _command_lines(source)
    hook_lines = [n for n, line in commands if re.match(r"^"+HOOK+r"(?:\\s|$)", line)]
    follow_lines = [n for n, line in commands if re.match(r"^"+FOLLOW+r"(?:\\s|$)", line)]
    reasons = []
    if len(hook_lines) != 1:
        reasons.append("Exactly one post-bed hook call is required")
    if len(follow_lines) != 1:
        reasons.append("Exactly one RatOS post-bed continuation call is required")
    if len(hook_lines) == 1 and len(follow_lines) == 1:
        if hook_lines[0] >= follow_lines[0]:
            reasons.append("The RatOS continuation must occur after the hook")
        else:
            reasons.append("RatOS continuation is queued in the same START_PRINT macro expansion")
    if re.search(r"\\bAUTOXY_PRINT_(?:HOLD|CHECK)\\b", source):
        reasons.append("Embedding HOLD and CHECK in one expanded macro does not create an SD-file boundary")
    # This tool intentionally cannot certify the hook-only method: M25 stops
    # virtual-SD file reading, NOT execution of already-expanded START_PRINT.
    return {"hook_line": hook_lines[0] if len(hook_lines) == 1 else None,
            "continuation_line": follow_lines[0] if len(follow_lines) == 1 else None,
            "safe_to_deploy_hook_only_hold_resume": False,
            "reason": "Virtual-SD pause cannot suspend commands already expanded by START_PRINT.",
            "observations": reasons,
            "required_next_step": "Use independently gated SD-file commands or redesign startup as a tested split-phase state machine."}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start_print_macro", type=Path, help="Text export of START_PRINT gcode")
    a=parser.parse_args()
    data=inspect_start_print(a.start_print_macro.read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2))
    # Nonzero exit is intentional: do not interpret structural inspection as approval.
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
