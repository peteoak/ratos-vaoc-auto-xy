# RC16 — read-only printer code and hook integrity audit

**Purpose:** Identify active AutoXY/Klipper print interceptors, stale historical add-ons, unexpected configuration includes or code changes before any file cleanup. **Do not delete, disable, quarantine, uninstall or restart anything based only on this audit.**

The script `scripts/rc16_printer_integrity_audit.py` is an offline-reviewed, **read-only printer inventory**. It reads installed code/config metadata, current Moonraker status and possible startup hook references. It does not command movements, run G-code, import printer extensions, set AutoXY ON/OFF, rewrite configuration or delete files.

## Download and run on printer

Run over SSH (normal `pi` user; no `sudo`). Use the exact repository commit approved for the audit, rather than a moving branch:

```bash
mkdir -p /home/pi/AutoXY_Audit_ReadOnly
curl -fL --retry 3 \
  "https://api.github.com/repos/peteoak/ratos-vaoc-auto-xy/tarball/<PINNED_AUDIT_MERGE_SHA>" \
  -o /tmp/AutoXY_Audit_ReadOnly.tar.gz
tar -xzf /tmp/AutoXY_Audit_ReadOnly.tar.gz \
  -C /home/pi/AutoXY_Audit_ReadOnly --strip-components=1
python3 -B /home/pi/AutoXY_Audit_ReadOnly/scripts/rc16_printer_integrity_audit.py
```

The tool prints an exact path like `/home/pi/AutoXY_Printer_Integrity_Audit_YYYYMMDD_HHMMSS.zip`. Download that *exact* ZIP to a trusted PC with `scp` (do not copy archives by wildcard), inspect and upload privately. An archive is written outside the user's printer config and source directories; existing archives are never overwritten.

```powershell
scp "pi@RatOS:/home/pi/AutoXY_Printer_Integrity_Audit_YYYYMMDD_HHMMSS.zip" "$HOME\Downloads\"
```

Substitute the exact output name. The pinned release commit will be given alongside the tool once merged; the placeholder above deliberately cannot install an unknown moving source.

## Contents and privacy

`inventory.json` and `SUMMARY.txt` contain:
- Active `printer.cfg` include names and selected AutoXY/VAOC section headings.
- Klipper `extras` extension file metadata, symlink targets and SHA256 / Git blob digests.
- Bounded inventory of AutoXY Python/cfg/shell scripts, with only **hook-name + line-number evidence**, no full code.
- Current reviewed GitHub source-digest comparisons, labelled MATCH / DIFFERS_OR_OLDER / UNAVAILABLE. A mismatch alone does not prove an unwanted extension.
- Sanitized Moonraker values: print/SD/VAOC and RC16-hand-off booleans, operator ON/fault flags; **not** temperatures, positions, saved offsets, selected G-code names or configuration values.
- Potential systemd/cron references by filename and matching line number only.

The scanner excludes private G-code content, JPEGs, reference profiles, protected-run reports, backups and credentials. It **does not** claim to detect every dynamic Python monkeypatch or clandestine service. Treat report paths as potentially revealing local directory names and share only with trusted reviewers.

## Interpreting the old Trial04 problem

The prior printer evidence showed the older `auto_xy_local_recovery.py` source and active include, plus RC16. Legacy Trial04 intercepted `PROCESS_GCODE_FILE` and unnecessarily staged a COPY-mode job as `AutoXY_...`. PR #15 introduced a separately supervised retirement migration at `scripts/rc16_retire_trial04_recovery.py`, but **do not assume that migration has been installed**.

If this inventory reports `COMPETING_TRIAL04_AND_RC16_PRINT_HANDOFFS`, first confirm the active configuration and exact hashes, printer state and migration compatibility. Never simply remove the symlink to make the message disappear. Both PRINT processing and direct virtual-SD bypass guards must remain coherent.

## Cleanup sequence after review

1. **Audit and classify** each candidate: active and essential, active and conflicting, installed but inactive legacy, recoverable backup, private reference/data, generated file eligible for existing retention, or unknown.
2. **Protect and back up** the entire live configuration, commissioned camera references, saved offsets, critical ext/module files and relevant registration state; verify the backup off-device.
3. **Retire one verified conflict at a time** by an exact-version, rollback-safe migration; verify Klipper readiness and RC16 guards after restart.
4. **Supervised printer qualification**: COPY/MIRROR and single-tool native paths; independent two-tool fresh XY verification; manual fallback, cancellation, guarded bypasses, and scheduled retention.
5. Only then discuss a **separate dry-run cleanup manifest** for genuinely orphaned or redundant files. No broad `rm`, `find -delete`, wildcards against backup folders, or deletion of commissioned reference JPEGs.

The scanner is safe to run while printing because it doesn't mutate state; however source digests may reflect a changing install if other maintenance runs concurrently. For a trustworthy cleanup decision, collect the report while **fully idle and with no concurrent upgrade**.

See [handover](DEVELOPMENT_HANDOVER.md), [current status](CURRENT_STATUS.md), [RC16 status](RC16_RELEASE_STATUS.md), and [supervised qualification](RC16_SUPERVISED_QUALIFICATION.md).
