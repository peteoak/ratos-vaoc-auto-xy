# RC16 calibration diagnostics and evidence

**Current telemetry applies to the geometry-first detector and camera retry worker in `main`.** Reports from earlier RC16 builds naturally lack some fields; **missing/null** is unknown, not a measured zero.

## Where to look

Each run has its own directory below `/home/pi/auto_xy/print_gate_runs/`, such as `20261113-231235_print`. The `report.json` is the authoritative run record, with original and `detected_` annotated JPEGs. Klipper logs are usually in `/home/pi/printer_data/logs/klippy.log`.

`status: CALIBRATED_AND_VAOC_EXITED` means the alignment phase finished and native VAOC exited; it is **not** independent evidence that the entire print finished. `FAILED_DO_NOT_PRINT` means the protected print was not authorised by automatic alignment; inspect the saved failure and guarded recovery fields. Run timestamps are those of the Pi and must be interpreted with its timezone.

### Reliability fields

| Field | Meaning |
| --- | --- |
| `captures[].appearance_evidence.method` | `geometry-only` for a unique geometric circle; a photographic tie-break method only if exactly two geometry-qualified candidates remain |
| `captures[].appearance_evidence.used_for_selection` | Whether photographic reference evidence actually determined nozzle choice |
| `captures[].appearance_evidence.photo_weight` | 0 for geometry-only; at most 0.25 when an ambiguous pair required comparison |
| `captures[].appearance_evidence.candidate_count` | Qualified circle count reported for this successful capture |
| `captures[].camera_http_attempts` | Number of **fresh** HTTP requests, where 1 is normal |
| `captures[].camera_http_retries` | Number of fresh retries after transient camera server 500/502/503/504 errors |
| `captures[].camera_http_retry_statuses` | Status codes that triggered retry before the accepted JPEG |
| `captures[].photo_reference_available` | Whether a commissioned reference signature was supplied to that worker capture |
| `vision_reliability_summary` | Aggregates successful captures, geometry-only/photo tie-break decisions, HTTP retries and worker-level retries |
| `camera_disagreement_percent` | Per-axis disagreement between T0/T1 live camera scale measurements |
| `capture_failures` | Worker-level failures that may precede a successful second attempt, or a final fail-closed result |
| `measurements`, `final_error_mm` | Raw alignment checks and final residual error in mm; require two consecutive in-tolerance verification results |

A report showing zero HTTP retries establishes only that no **successful** capture needed a tracked HTTP retry; a failed or interrupted camera request may appear in `capture_failures` or Klipper logs instead. Stage timings and motion diagnostics are optional and depend on configuration.

The geometry-first change does **not** prove the actual physical extrusion axis of a worn or off-centre nozzle. Confirm the annotated rim and real alignment independently when commissioning or troubleshooting false detections.

## Read-only recent run collection

Run on the Pi over SSH. This gathers the six most recent run reports with their images and a bounded selection of relevant log lines; it **does not** alter printer files, offsets or printer state. It can produce a sizable ZIP and may include printer diagnostic information, so review before sharing publicly.

```bash
python3 - <<'PY'
from pathlib import Path
from collections import deque
from datetime import datetime
import json, re, zipfile, hashlib
home=Path.home()
root=home/'auto_xy/print_gate_runs'
reports=sorted(root.glob('*/report.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:6] if root.exists() else []
output=home/('AutoXY_Diagnostics_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.zip')
versions={}
for name,p in {
    'gate':home/'auto_xy/klipper/auto_xy_gate.py',
    'worker':home/'auto_xy/vision/auto_xy_vision_worker.py',
    'detector':home/'auto_xy/vision/auto_xy_detector_rc16.py',
}.items():
    versions[name]=hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
    for report in reports:
        folder=report.parent
        for f in folder.iterdir():
            if f.is_file() and not f.is_symlink() and f.suffix.lower() in ('.json','.jpg','.jpeg','.png','.csv','.webp'):
                z.write(f, 'runs/'+folder.name+'/'+f.name)
    log=home/'printer_data/logs/klippy.log'
    if log.is_file():
        with log.open(errors='replace') as stream:
            lines=list(deque(stream,maxlen=20000))
        pattern=re.compile(r'autoxy|auto.xy|vaoc|camera|snapshot|verification|scale|calibrat|traceback',re.I)
        keep=set()
        for i,line in enumerate(lines):
            if pattern.search(line):keep.update(range(max(0,i-2),min(len(lines),i+5)))
        z.writestr('recent_klippy_autoxy.txt',''.join(lines[i] for i in sorted(keep)))
    z.writestr('installed_component_sha256.json',json.dumps(versions,indent=2))
    z.writestr('run_index.json',json.dumps([p.parent.name for p in reports],indent=2))
print('Reports:',len(reports))
print('Archive:',output)
PY
```

Download the exact printed file from Windows PowerShell using `scp "pi@RatOS:/home/pi/AutoXY_Diagnostics_YYYYMMDD_HHMMSS.zip" "$HOME\Downloads\"`; substitute the actual filename shown by the command. Avoid wildcard copying old private diagnostics by accident.

## Distinguish code testing from hardware qualification

- Green GitHub CI verifies simulated control paths and regression tests, **not** every RatOS/MCU/camera combination.
- The owner has demonstrated multiple guarded calibrations and a native manual-VAOC continuation. These establish useful per-printer evidence, not beta-nozzle disambiguation.
- Original beta tester images, rather than overlays, are necessary for a valid replay of ambiguous two-circle recognition.
- Any uncertainty that might authorize an incorrect XY offset or unsafe print should fail closed.
