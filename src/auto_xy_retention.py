#!/usr/bin/env python3
"""Conservative AutoXY report-image retention. Only explicitly referenced JPEGs.

Unknown run directories, symlinks, report failures and non-image files are
preserved. Reports are retained to support calibration forensics; older reports
must be purged separately after review. Never removes a directory.
"""
import argparse
import json
import time
from pathlib import Path

VALID={'CALIBRATED_AND_VAOC_EXITED','FAILED_DO_NOT_PRINT'}

def plan(root,success_days,failed_days,report_days,now=None):
    now=time.time() if now is None else now
    chosen=[]
    if not root.is_dir() or root.is_symlink():return chosen
    for run in root.iterdir():
        if run.is_symlink() or not run.is_dir():continue
        report=run/'report.json'
        if not report.is_file() or report.is_symlink():continue
        try:
            data=json.loads(report.read_text())
            status=data.get('status')
            if status not in VALID:continue
            age_days=(now-report.stat().st_mtime)/86400.
            age_limit=success_days if status=='CALIBRATED_AND_VAOC_EXITED' else failed_days
            if age_days<age_limit:continue
            referenced=set()
            for record in data.get('captures',[])+data.get('capture_failures',[]):
                p=Path(record.get('image',''))
                if p.suffix.lower() not in ('.jpg','.jpeg'):continue
                if p.parent.resolve()!=run.resolve():continue
                if p.is_symlink() or not p.is_file():continue
                referenced.add(p)
            chosen.extend(sorted(referenced))
        except (OSError,ValueError,TypeError,KeyError):continue
    return chosen

def main():
    a=argparse.ArgumentParser()
    a.add_argument('--run',action='store_true')
    a.add_argument('--results',type=Path,required=True)
    a.add_argument('--success-days',type=int,default=7)
    a.add_argument('--failed-days',type=int,default=30)
    a.add_argument('--report-days',type=int,default=90)
    opts=a.parse_args()
    if not (1<=opts.success_days<=opts.failed_days<=opts.report_days):
        a.error('Invalid retention order')
    files=plan(opts.results,opts.success_days,opts.failed_days,opts.report_days)
    print('AutoXY retention:',len(files),'eligible referenced JPEGs',
          '(run)' if opts.run else '(preview only)')
    if opts.run:
        for p in files:
            if p.is_file() and not p.is_symlink():p.unlink()
    # Reports deliberately retained: do not delete data used for safety review.
if __name__=='__main__':main()
