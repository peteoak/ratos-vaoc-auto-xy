#!/usr/bin/env python3
"""READ ONLY: inspect last AutoXY failure, active-VAOC retry and offsets.

Does NOT move carriages, clear faults, perform rollback, or assert that the
Klipper named-offset compensation is safe. The controller performs separate
read-only live pose/temperature checks before permitting one retry.
"""
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT=Path('/home/pi/auto_xy/print_gate_runs')


def main():
    reports=sorted(ROOT.glob('*/report.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    if not reports:
        raise SystemExit('NO REPORT. Manual investigation required.')
    report_path=reports[0]
    report=json.loads(report_path.read_text())
    if report.get('status') in ('RECOVERED_INLINE','INLINE_RETRY_FAILED_PRINT_ABORTED'):
        linked=report.get('inline_retry_report_dir')
        if linked and Path(linked,'report.json').is_file():
            print('First attempt audit:',report_path)
            print('Inline retry status:',report['status'])
            report_path=Path(linked,'report.json')
            report=json.loads(report_path.read_text())
    req=Request('http://127.0.0.1:7125/printer/objects/query',
                data=json.dumps({'objects': {'save_variables':None,'print_stats':None,
                    'virtual_sdcard':None,'toolhead':None,'gcode_macro _VAOC':None,
                    'dual_carriage':None,'gcode_move':None,
                    'extruder':None,'extruder1':None}}).encode(),
                headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=6) as response:
        status=json.load(response)['result']['status']
    var=status['save_variables']['variables']
    offset_keys=('idex_xoffset','idex_yoffset','idex_zoffset')
    current=[float(var[k]) for k in offset_keys]
    baseline=report.get('original_offsets')
    expected=report.get('saved_corrections',[])
    expected=expected[-1]['after'] if expected else baseline
    match=(expected is not None and all(abs(current[i]-expected[i])<.0002 for i in range(3)))
    actual_vaoc=status.get('gcode_macro _VAOC',{}).get('is_started','UNKNOWN')
    print('Latest report:',report_path)
    for k in ('status','mode','error','retry_offered','one_time_retry','reuse_active_vaoc','inline_print_retry_enabled','rollback',
              'measurement_cycles','corrections_applied','verification_attempts'):
        print(k+':',report.get(k,'not recorded'))
    print('Original offset baseline:',baseline)
    print('Last confirmed saved offset:',expected)
    print('Current saved offsets:',current)
    print('Match latest confirmed write/original:',match)
    print('Offset write intents:',len(report.get('write_intents',[])),
          'confirmed writes:',len(report.get('saved_corrections',[])))
    print('Printer state:',status['print_stats'].get('state'),
          'virtual SD active:',status.get('virtual_sdcard',{}).get('is_active'))
    print('VAOC started:',actual_vaoc)
    print('Homed axes:',status['toolhead'].get('homed_axes'))
    print('Dual carriage:',status.get('dual_carriage',{}).get('carriage_1'))
    print('Current G-code position:',status.get('gcode_move',{}).get('gcode_position'))
    for name in ('extruder','extruder1'):
        t=status.get(name,{})
        print('%s actual/target:'%name,t.get('temperature'),t.get('target'))
    for k in ('auto_xy_enabled','auto_xy_fault','auto_xy_in_progress',
              'auto_xy_retry_available','auto_xy_retry_used'):
        print(k+':',var.get(k,0))
    if report.get('retry_offered') and bool(var.get('auto_xy_retry_available',0)):
        print('ONE ACTIVE-VAOC RETRY TOKEN PRESENT.')
        print('Do NOT exit VAOC, move toolheads, restart Klipper or acknowledge the fault before retry.')
        print('The controller must independently verify pose, camera temperature, active VAOC and saved offsets.')
        print('If a print is still active, stop it safely and recheck pose; changed pose will block the retry.')
        print('This read-only checker CANNOT prove live VAOC pose or runtime named offsets are safe.')
    else:
        print('NO VERIFIED IN-PLACE RETRY available based on latest report/token.')
        print('Manually recover VAOC and reconcile saved/runtime offsets before acknowledging fault or printing.')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        raise SystemExit('READ-ONLY REVIEW FAILED: '+str(exc))
