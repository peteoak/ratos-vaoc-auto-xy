# Beta 4.2 final owner checks (read-only first)

This checklist is for the exact owner RatRig and does not establish broad compatibility.
Run the reports command on the Pi by SSH. It only reads local JSON files and saved state; it neither homes nor moves carriages.

## 1. Review latest successful independent IDEX print-start report

```bash
python3 - <<'PY'
import json
from pathlib import Path
r=Path.home()/'auto_xy'/'print_gate_runs'
reports=[]
for p in r.glob('*/report.json'):
    try:
        d=json.loads(p.read_text())
    except (OSError,ValueError):
        continue
    if d.get('mode')=='PRINT':
        reports.append((p.stat().st_mtime,p,d))
if not reports:
    print('No PRINT report found. Do not infer print-start success from an IDLE report.')
else:
    _,p,d=max(reports)
    print('Report:',p)
    for k in ('status','mode','requested_idex_mode','settling_status','settling_checks_used',
              'corrections_applied','verification_attempts','response_gain_anomalies',
              'response_gain_rechecks','response_gain_warning','error','rollback'):
        print(k+':',d.get(k))
    for m in d.get('measurements',[])[-4:]:
        print('measurement:',m.get('label'),m.get('error_mm'))
PY
```

Expected on success: `status: CALIBRATED_AND_VAOC_EXITED`, `mode: PRINT`, error absent, stable settling, two in-tolerance final verification measurements, fault-free printer after the run. Review actual data rather than assuming fixed counts.

## 2. Test mode bypasses only if safe and already supported

- `AUTO_XY_OFF` with a suitable safe print: normal RatOS start, no Auto XY VAOC.
- Auto XY ON with a **single-tool T0-only** job whose `TOTAL_TOOLSHIFTS=0`: bypass without changing offsets.
- Auto XY ON with **single-tool T1-only** job with `TOTAL_TOOLSHIFTS=0`: bypass without changing offsets.
- Auto XY ON with standard COPY and MIRROR jobs: bypass without changing offsets. Do not force a mode unavailable on this printer.
- Before starting any test print, verify its `START_PRINT` `TOTAL_TOOLSHIFTS` and mode are what you expect; test one mode at a time.

After each test, note the console's `AUTO XY: ... skipping VAOC` (or OFF bypass) message. Check whether a new print calibration report exists. A skip should not generate a new calibration run. If any mode starts VAOC unexpectedly, stop safely and report the exact console output.

## 3. Verify housekeeping without deleting anything

```bash
python3 ~/auto_xy/stage6/auto_xy_retention.py --check
```

Check the age/retention preview only. **Do not force-delete runs or artificially age files** to simulate retention on a live printer.

## 4. Verify install/uninstall code offline, not with a live purge

In the beta.4.2 release folder:

```bash
python3 test_config_editing.py
python3 test_retention_uninstall.py
python3 test_stage6_offline.py
python3 test_response_gain_recheck.py
python3 test_homing_preflight.py
python3 install_stage6.py --check
```

The suite simulates complete install→uninstall→reinstall; it does not prove the owner's actual old installation was cleanly purged. A full uninstall/purge is destructive and unnecessary just for this release-candidate check.

## 5. Release decision

Only then consider publishing an explicitly marked **community pre-release**, indicating the limits of single-machine testing and any NOT TESTED modes. Do not claim that the rare response-gain anomaly was exercised on hardware when it was only simulated.
