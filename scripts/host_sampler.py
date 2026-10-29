#!/usr/bin/env python3
"""Read-only low-rate Raspberry Pi host resource sampler during supervised diagnostics.

No printer, camera, CAN, USB or Klipper control. CSV in current directory or given path.
CPU percent is host-wide /proc/stat interval utilization, not process-specific.
"""
import argparse
import csv
from datetime import datetime
import os
from pathlib import Path
import time


def cpu_ticks():
    fields=Path('/proc/stat').read_text().splitlines()[0].split()
    if fields[0]!='cpu':raise RuntimeError('Unexpected /proc/stat header')
    vals=list(map(int,fields[1:]))
    idle=vals[3] + (vals[4] if len(vals)>4 else 0)
    return sum(vals),idle


def memory_available_mb():
    for row in Path('/proc/meminfo').read_text().splitlines():
        if row.startswith('MemAvailable:'):
            return round(int(row.split()[1])/1024,2)
    return None


def temperature_c():
    path=Path('/sys/class/thermal/thermal_zone0/temp')
    try:return round(int(path.read_text().strip())/1000,2)
    except (OSError,ValueError):return None


def pressure10():
    path=Path('/proc/pressure/cpu')
    try:
        first=path.read_text().splitlines()[0].split()
        item=next(s for s in first if s.startswith('avg10='))
        return float(item.partition('=')[2])
    except (OSError,ValueError,StopIteration):return None


def sample(previous):
    current=cpu_ticks()
    diff_total=current[0]-previous[0]
    diff_idle=current[1]-previous[1]
    busy=round(100*(1-diff_idle/diff_total),2) if diff_total>0 else None
    row=[datetime.now().astimezone().isoformat(timespec='seconds'),busy,
         round(os.getloadavg()[0],3),memory_available_mb(),
         temperature_c(),pressure10()]
    return current,row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seconds',type=int,default=600)
    p.add_argument('--interval',type=float,default=2.)
    p.add_argument('--output',type=Path,
                   default=Path('autoxy_host_sample_'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.csv'))
    a=p.parse_args()
    if not 1<=a.seconds<=7200 or not .5<=a.interval<=30:
        p.error('seconds must be 1..7200, interval 0.5..30')
    if a.output.exists():p.error('Refusing to overwrite existing output')
    last=cpu_ticks()
    end=time.monotonic()+a.seconds
    with a.output.open('x',newline='') as handle:
        writer=csv.writer(handle)
        writer.writerow(['local_time','cpu_busy_percent','load1','mem_available_mib',
                         'cpu_temp_c','cpu_pressure_avg10'])
        print('HOST MONITOR:',a.output,'(Ctrl+C stops; printer unaffected)',flush=True)
        try:
            while time.monotonic()<end:
                time.sleep(min(a.interval,max(0,end-time.monotonic())))
                last,row=sample(last)
                writer.writerow(row)
                handle.flush()
        except KeyboardInterrupt:pass
    print('HOST MONITOR SAVED:',a.output)

if __name__=='__main__':main()
