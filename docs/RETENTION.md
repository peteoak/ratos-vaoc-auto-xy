# Calibration photographs, reports and automatic cleanup

Each print calibration creates its own timestamp-named folder under `/home/pi/auto_xy/print_gate_runs`. Files are **not overwritten** across calibration runs.

## Retention defaults

| Result | Photos | JSON report |
|---|---:|---:|
| Calibration complete (`CALIBRATED_AND_VAOC_EXITED`, `RECOVERED_INLINE`) | 7 days | 90 days |
| Completed failed calibration (`FAILED...`, `INLINE_...`) | 30 days | 90 days |
| Incomplete, unknown, or malformed reports | **preserved for review** | **preserved for review** |

The Klipper extension starts `auto_xy_retention.py` in a **separate background process** after approximately five minutes of runtime and about every 24 hours thereafter, only while printer reports idle and the Auto XY controller is not busy. If printing at the scheduled time, it tries later. It does not send printer commands, edit `printer.cfg`, remove saved offsets, or affect camera measurement during calibration.

The retention period starts at the timestamp encoded in each run folder name, not the file modification time. Cleanup only deletes known numbered calibration images (`001_...jpg`) and known completed run folders whose files are all recognised. Folders with unexpected files, symbolic links, incomplete or unrecognised statuses are left untouched. These conservative exclusions mean some exceptional runs can still accumulate and require manual review.

## Preview before deleting

```bash
python3 ~/auto_xy/stage6/auto_xy_retention.py --check
```

For an on-demand cleanup, only if you have reviewed the preview and the printer is idle:

```bash
python3 ~/auto_xy/stage6/auto_xy_retention.py --run
```

This is distinct from calibration; it cannot change XY offsets. Avoid running the manual cleanup during calibration, even though only old directories are selected.

To adjust retention, edit the following **in** `/home/pi/printer_data/config/auto_xy_print.cfg` while idle and restart Klipper:

```ini
retention_success_images_days: 7
retention_failed_images_days: 30
retention_reports_days: 90
```

Valid settings: `1 <= success_days <= failed_days <= report_days <= 3650`. Keep enough history for troubleshooting. For a full filesystem backup, copy the reports/images yourself before their expiry.

## Does cleanup guarantee free disk space?

No. It is intentionally conservative and does not have a broad low-disk-space purge. Incomplete, unknown or unrecognised results are preserved rather than deleted. Monitor with:

```bash
du -sh ~/auto_xy/print_gate_runs
```
