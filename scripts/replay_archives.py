#!/usr/bin/env python3
"""Read-only dual detector vs guarded-legacy replay on archived Pi photographs.

Does not connect to Moonraker, motors, heater, camera or installed AutoXY.
"""
from pathlib import Path
import argparse,json,math,re,statistics,sys,time
import cv2
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from auto_xy_detector import detect

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,default=Path('/home/pi/auto_xy/print_gate_runs/20261029-060042_idle'))
    parser.add_argument('--profile',type=Path,default=Path('/home/pi/auto_xy/camera_commission.json'))
    parser.add_argument('--require-images',type=int,default=30)
    parser.add_argument('--threads',type=int,choices=(1,2,3,4),default=2,help='OpenCV threads in this replay process only')
    args=parser.parse_args()
    cv2.setNumThreads(args.threads)
    print('OpenCV threads:',cv2.getNumThreads())
    profile=json.loads(args.profile.read_text())
    if profile.get('status') != 'COMMISSIONED_FROM_IMAGES':
        raise SystemExit('Commissioning profile not accepted; refusing comparative replay')
    images=sorted(p for p in args.run.glob('*.jpg') if not p.name.startswith('detected_'))
    if len(images)<args.require_images:
        raise SystemExit('Run has insufficient archived photographs: '+str(len(images)))
    stats={'image_files':len(images),'compared':0,'legacy_pass':0,'two_stage_pass':0,
           'recovered':0,'new_rejections':0,'changed_precision_centre':0,'both_failed':0,'unmatched':0,
           'dev4_two_stage_pass':0,'dev5_roi_pass':0,'roi_new_rejections':0,'roi_changed_centres':0,'roi_recovered':0}
    old_times=[];new_times=[];roi_times=[]
    for path in images:
        m=re.search(r'(?:^|_)t([01])_',path.stem)
        if not m:
            stats['unmatched']+=1; print('NO TOOL LABEL:',path.name);continue
        entry=profile['tool'+m.group(1)]
        kwargs=dict(expected_center=entry['registration_center_px'], expected_radius=entry['radius_px'])
        result=[]
        for mode,times in (('legacy',old_times),('two_stage',new_times),('two_stage_roi',roi_times)):
            start=time.perf_counter()
            try: result.append((detect(path,detector_mode=mode,**kwargs),None))
            except ValueError as e:result.append((None,str(e)))
            times.append(time.perf_counter()-start)
        (a,ae),(b,be),(c,ce)=result;stats['compared']+=1
        stats['dev4_two_stage_pass']+=b is not None
        stats['dev5_roi_pass']+=c is not None
        if b is not None and c is None:
            stats['roi_new_rejections']+=1;print('ROI NEW REJECTION:',path.name,ce)
        elif b is not None and c is not None:
            if any(abs(float(b[k])-float(c[k]))>1e-9 for k in range(3)):
                stats['roi_changed_centres']+=1;print('ROI CHANGED:',path.name,b,c)
        elif b is None and c is not None:
            stats['roi_recovered']+=1;print('ROI RECOVERED:',path.name)
        if a is not None:stats['legacy_pass']+=1
        if b is not None:stats['two_stage_pass']+=1
        if a is None and b is not None:
            stats['recovered']+=1;print('RECOVERED:',path.name)
        elif a is not None and b is None:
            stats['new_rejections']+=1;print('NEW REJECTION:',path.name,be)
        elif a is not None and b is not None:
            if any(abs(float(a[k])-float(b[k]))>1e-9 for k in range(3)):
                stats['changed_precision_centre']+=1;print('CHANGED PRECISION:',path.name,a,b)
        else:stats['both_failed']+=1
    print('\n=== ARCHIVED CAMERA REPLAY ===')
    for key,val in stats.items():print(key+':',val)
    if old_times and new_times:
        print('median legacy detector s:',round(statistics.median(old_times),4))
        print('median two-stage detector s:',round(statistics.median(new_times),4))
        print('median additional dev4 detector s:',round(statistics.median(new_times)-statistics.median(old_times),4))
        print('median dev5 cropped detector s:',round(statistics.median(roi_times),4))
        print('median dev5 vs dev4 saving s:',round(statistics.median(new_times)-statistics.median(roi_times),4))
    if (stats['new_rejections'] or stats['changed_precision_centre'] or stats['unmatched']
            or stats['roi_new_rejections'] or stats['roi_changed_centres']):
        raise SystemExit('REPLAY FAILED: do not install/enable this candidate')
    if stats['compared']<args.require_images:
        raise SystemExit('REPLAY INSUFFICIENT: too few matched tool images')
    print('REPLAY PASS: dev5 cropped ROI has identical dev4 precision centres; no new rejections')

if __name__=='__main__':main()
