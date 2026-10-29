#!/usr/bin/env python3
"""Offline prepare a separate AutoXY protected-print G-code from an already RatOS-processed file.
Never modifies the source G-code. Does not talk to Klipper, Moonraker or printers.
"""
import argparse, os, re, sys
from pathlib import Path
MARKER='; AutoXY v0.5 protected pre-Beacon two-phase start'
PAT=re.compile(r'^(?P<ws>\s*)START_PRINT\s+(?P<args>[^;\r\n]+?)(?P<comment>\s*(?:;[^\r\n]*)?)(?P<nl>\r?\n|$)',re.I|re.M)

def convert(source):
    if MARKER in source or '; AutoXY OPT-IN PREBEACON TWO-PHASE' in source:
        raise ValueError('Already converted; refusing second transform')
    matches=list(PAT.finditer(source))
    if len(matches)!=1:raise ValueError('Require exactly one plain START_PRINT line')
    m=matches[0]
    args=m.group('args').strip()
    kv={}
    for pair in args.split():
        if pair.count('=')!=1:raise ValueError('Unexpected START_PRINT parameter '+pair)
        key,val=pair.split('=',1)
        key=key.upper()
        if key in kv:raise ValueError('Duplicate parameter '+key)
        kv[key]=val
    required=('EXTRUDER_TEMP','EXTRUDER_OTHER_LAYER_TEMP','BED_TEMP','TOTAL_TOOLSHIFTS','INITIAL_TOOL')
    if any(key not in kv for key in required):raise ValueError('Missing required RatOS start parameter')
    try:
        shifts=int(kv['TOTAL_TOOLSHIFTS']); bed=float(kv['BED_TEMP'])
    except ValueError:raise ValueError('Invalid toolshift or bed parameters')
    if shifts<1 or not (20<=bed<=125):
        raise ValueError('AutoXY requires 2-tool print, nonzero toolshifts and plausible bed target')
    if len(kv['EXTRUDER_TEMP'].split(','))!=2:raise ValueError('Require both T0/T1 first-layer temperatures')
    if len(kv['EXTRUDER_OTHER_LAYER_TEMP'].split(','))!=2:raise ValueError('Require both T0/T1 other-layer temperatures')
    if kv['INITIAL_TOOL'] not in ('0','1'):raise ValueError('Unsupported initial tool')
    if re.search(r'^\s*(AUTOXY_AutoXY_|AutoXY_|\[include\s)', source,re.I|re.M):
        raise ValueError('Existing AutoXY commands or include directives; manual review')
    newline=m.group('nl') or '\n'
    commands=[MARKER,
      'AUTOXY_PRINT_BEGIN BED_TEMP='+kv['BED_TEMP'],
      'AUTOXY_PRINT_PREPARE '+args,
      'AUTOXY_PRINT_CALIBRATE',
      'AUTOXY_PRINT_HOLD',
      'AUTOXY_PRINT_CHECK',
      'AUTOXY_PRINT_FINISH '+args]
    replacement=newline.join(commands)+newline
    return source[:m.start()]+replacement+source[m.end():]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('source',type=Path)
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    source=a.source.resolve(strict=True)
    if source.is_symlink() or source.suffix.lower()!='.gcode':p.error('Source must be an ordinary .gcode file')
    dest=a.output if a.output else source.with_name(source.stem+'_AUTOXY_V050.gcode')
    if dest.resolve()==source:p.error('Never overwrite source')
    if dest.exists():p.error('Output already exists; refusing overwrite')
    text=source.read_text(encoding='utf-8',errors='strict')
    output=convert(text)
    flags=os.O_CREAT|os.O_EXCL|os.O_WRONLY
    fd=os.open(str(dest),flags,0o644)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='') as f:f.write(output)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    print('AutoXY protected-print file created:',dest,'Original unchanged:',source)
if __name__=='__main__':main()
