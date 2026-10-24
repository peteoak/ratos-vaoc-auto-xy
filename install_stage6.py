#!/usr/bin/env python3
"""Idempotent, offline-first installation/upgrade of RatOS AutoXY Stage 6.3.7.

No remote GCode is sent. `--install` refuses until saved AUTO XY is OFF,
then backs up printer.cfg before modifying the include.
It verifies both pre-reset mode capture and post-bed heatsoak hook order. It does not restart
Klipper, change any calibrated offsets or edit any RatOS-managed cfg files.
"""
import argparse, json, re, shutil, sys, time
from pathlib import Path
from urllib.request import Request,urlopen
from printer_cfg_editor import install_include, atomic_write_text, PrinterCfgError

HOME=Path('/home/pi')
CFG=HOME/'printer_data/config'
DEST=HOME/'auto_xy/stage6'
KLIPPER=HOME/'klipper/klippy/extras/auto_xy_gate.py'
INCLUDE='[include auto_xy_print.cfg]'
EXISTING='[include auto_xy_control.cfg]'
HOOK='[gcode_macro _USER_START_PRINT_AFTER_HEATING_BED]'
EARLY_MODE_HOOK='[gcode_macro _USER_START_PRINT]'
LEGACY_HOOKS=('[gcode_macro _USER_START_PRINT_BED_MESH]',
              '[gcode_macro _USER_START_PRINT_AFTER_HEATING_EXTRUDER]')
FILES=('auto_xy_vision_worker.py','auto_xy_detector.py','disable_stage6.py',
       'check_last_failure.py','printer_cfg_editor.py','repair_save_config.py','auto_xy_retention.py','uninstall_auto_xy.py')


def live_status():
    req=Request('http://127.0.0.1:7125/printer/objects/query',
                data=json.dumps({'objects':{'save_variables':None,'print_stats':None,
                                            'virtual_sdcard':None,'gcode_macro _VAOC':None}}).encode(),
                headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=6) as r: data=json.load(r)
    s=data['result']['status']
    v=s['save_variables']['variables']
    if str(v.get('auto_xy_enabled',0)).strip().lower() not in ('0','0.0','false'):
        raise RuntimeError('AUTO XY is ON. Run AUTO_XY_OFF in Mainsail BEFORE installing.')
    if str(v.get('auto_xy_in_progress',0)).strip().lower() in ('1','1.0','true'):
        raise RuntimeError('Previous calibration is marked incomplete; recover before installing.')
    if str(v.get('auto_xy_fault',0)).strip().lower() in ('1','1.0','true'):
        raise RuntimeError('Unresolved AutoXY safety fault; reconcile offsets and ACKNOWLEDGE before installing.')
    if str(s.get('gcode_macro _VAOC',{}).get('is_started',False)).lower() in ('1','1.0','true'):
        raise RuntimeError('VAOC is active; safely exit VAOC and verify pose before installing.')
    if str(s['print_stats'].get('state','')).lower() in ('printing','paused') or s['virtual_sdcard'].get('is_active',False):
        raise RuntimeError('A print is active or paused. Installation requires printer IDLE.')
    return v


def verify_local_ratos_hook_order():
    """Refuse installation on RatOS variants with different print-start ordering."""
    candidates=(CFG/'RatOS/macros.cfg', HOME/'ratos-configurator/configuration/macros.cfg')
    candidate=next((p for p in candidates if p.is_file()),None)
    if candidate is None:
        raise RuntimeError('Cannot locate RatOS macros.cfg to verify after-bed hook order. Do not install blindly.')
    src=candidate.read_text()
    begin=src.find('[gcode_macro START_PRINT]')
    if begin<0: raise RuntimeError('START_PRINT macro missing from '+str(candidate))
    body=src[begin:]
    # Require the early hook before RatOS's temporary _IDEX_SINGLE reset.
    early=body.find('_USER_START_PRINT { rawparams }')
    reset=body.find('_IDEX_SINGLE X={parking_position}')
    if early<0 or reset<0 or early>=reset:
        raise RuntimeError('RatOS early mode capture is not before the IDEX carriage reset: '+str(candidate))
    # Ensure the local RatOS version uses the same toolshift-count rule
    # that Auto XY uses to distinguish one-tool from two-tool prints.
    if ('params.TOTAL_TOOLSHIFTS|default(0)|int' not in body
        or 'if total_toolshifts == 0' not in body):
        raise RuntimeError('RatOS START_PRINT does not use the expected TOTAL_TOOLSHIFTS single-tool rule; refuse to guess.')
    sequence=('MAYBE_HOME',
              'G4 P{(bed_heat_soak_time * 1000)}',
              '_USER_START_PRINT_AFTER_HEATING_BED { rawparams }',
              '_START_PRINT_AFTER_HEATING_BED T=',
              '_USER_START_PRINT_BED_MESH { rawparams }')
    spots=[body.find(pattern) for pattern in sequence]
    if -1 in spots or spots!=sorted(spots) or len(set(spots))!=len(spots):
        raise RuntimeError('RatOS START_PRINT hook order does not match this safety-reviewed release: '+str(candidate))
    print('RatOS print-start order verified in:',candidate)
    print('Confirmed: START_PRINT mode+toolshifts capture -> carriage reset -> bed heatsoak -> two-tool-only AutoXY hook -> Beacon/post-bed -> bed mesh')


def validate(src):
    p=CFG/'printer.cfg'
    if not p.exists():raise RuntimeError('Expected RatOS printer.cfg not present: '+str(p))
    text=p.read_text()
    if '[include RatOS.cfg]' not in text: raise RuntimeError('Not a recognised RatOS printer.cfg')
    # Do not overwrite a user implementation of the print-start hook.
    for hook in (HOOK, EARLY_MODE_HOOK, *LEGACY_HOOKS):
        if hook in text:
            raise RuntimeError('Existing user print-start hook in printer.cfg: '+hook+'; merge manually.')
    existing=list(CFG.glob('*.cfg'))
    for other in existing:
        if other.name in ('printer.cfg','auto_xy_print.cfg','auto_xy_control.cfg'):
            continue
        try:
            if any(h in other.read_text() for h in (HOOK,EARLY_MODE_HOOK,*LEGACY_HOOKS)):
                raise RuntimeError('Existing conflicting print-start hook in '+str(other))
        except UnicodeError:pass
    for f in ('auto_xy_gate.py','auto_xy_print.cfg',*FILES):
        if not (src/f).is_file():raise RuntimeError('Release file missing: '+f)
    proposed=(src/'auto_xy_print.cfg').read_text()
    if HOOK not in proposed or EARLY_MODE_HOOK not in proposed or any(h in proposed for h in LEGACY_HOOKS):
        raise RuntimeError('Release package does not contain the expected pre-Beacon, after-bed-heatsoak hook')
    # Refuse a release without the visible, one-click Mainsail bypass control.
    if 'AUTO_XY_CAPTURE_MODE TOTAL_TOOLSHIFTS={params.TOTAL_TOOLSHIFTS|default(0)|int}' not in proposed:
        raise RuntimeError('Release must classify independent single vs two-tool prints using RatOS toolshift count')
    if '[gcode_macro AUTO_XY_TOGGLE]' not in proposed:
        raise RuntimeError('Release is missing required AUTO_XY_TOGGLE Mainsail macro button')
    if '[gcode_macro AUTO_XY_RETRY_PROMPT]' not in proposed or '[gcode_macro AUTO_XY_RETRY_DISMISS]' not in proposed:
        raise RuntimeError('Release is missing the retry-dismiss button')
    gate_src=(src/'auto_xy_gate.py').read_text()
    if ('def cmd_AUTO_XY_CAPTURE_MODE' not in gate_src
        or 'requested=self.pending_print_mode' not in gate_src
        or 'pending_toolshifts' not in gate_src
        or "'SINGLE_TOOL'" not in gate_src
        or "requested in ('COPY','MIRROR','SINGLE_TOOL')" not in gate_src
        or 'def cmd_AUTO_XY_RETRY_ONCE' not in gate_src
        or 'action:prompt_begin Auto XY calibration stopped' not in gate_src
        or 'def retryable_failure' not in gate_src
        or 'reusing STILL-ACTIVE VAOC' not in gate_src
        or 'def try_inline_print_retry' not in gate_src):
        raise RuntimeError('Release must contain in-place active-VAOC one-time retry and Mainsail prompt')
    if 'retention_success_images_days: 7' not in proposed or 'retention_failed_images_days: 30' not in proposed or 'retention_reports_days: 90' not in proposed:
        raise RuntimeError('Release must include configured safe calibration history retention')
    if ('def confirm_anomalous_alignment' not in gate_src
        or 'response_gain_rechecks' not in gate_src
        or 'measured_alignment_in_tolerance' not in gate_src):
        raise RuntimeError('Release missing guarded alignment-first no-write response confirmation')
    if 'def _retention_tick' not in gate_src:
        raise RuntimeError('Release missing standalone retention timer')
    if not re.search(r'(?m)^max_corrections:\s*10\s*$',proposed):
        raise RuntimeError('Release must configure max_corrections: 10')
    if not re.search(r'(?m)^inline_print_retry:\s*True\s*$',proposed):
        raise RuntimeError('Release must enable exactly one synchronous PRINT retry')
    if not re.search(r'(?m)^max_verifications:\s*6\s*$',proposed):
        raise RuntimeError('Release must configure max_verifications: 6')
    # Validate configuration transformation *before* any file is installed.
    # Includes are never allowed below Klipper's SAVE_CONFIG block.
    install_include(text)
    return text


def run():
    ap=argparse.ArgumentParser()
    g=ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--check',action='store_true')
    g.add_argument('--install',action='store_true')
    args=ap.parse_args()
    src=Path(__file__).resolve().parent
    text=validate(src)
    verify_local_ratos_hook_order()
    saved=live_status()
    print('OFF confirmed from live Klipper save_variables; printer idle.')
    print('Current saved offsets:',[saved.get(k) for k in ('idex_xoffset','idex_yoffset','idex_zoffset')])
    print('Existing Stage5 control include:', EXISTING in text)
    print('Existing Stage6 include:', INCLUDE in text)
    print('SAVE_CONFIG-aware include placement: validated')
    if args.check:
        print('CHECK PASSED. No files changed.')
        return
    stamp=time.strftime('%Y%m%d-%H%M%S')
    backup=HOME/'auto_xy/stage6_backups'/stamp
    backup.mkdir(parents=True,exist_ok=False)
    shutil.copy2(CFG/'printer.cfg',backup/'printer.cfg')
    old=CFG/'auto_xy_print.cfg'
    if old.exists(): shutil.copy2(old,backup/'auto_xy_print.cfg')
    if KLIPPER.exists(): shutil.copy2(KLIPPER,backup/'auto_xy_gate.py')
    if (CFG/'auto_xy_control.cfg').exists():
        shutil.copy2(CFG/'auto_xy_control.cfg',backup/'auto_xy_control.cfg')
    DEST.mkdir(parents=True,exist_ok=True)
    for f in FILES:shutil.copy2(src/f,DEST/f)
    shutil.copy2(src/'auto_xy_gate.py',KLIPPER)
    shutil.copy2(src/'auto_xy_print.cfg',CFG/'auto_xy_print.cfg')
    # Idempotent, SAVE_CONFIG-aware insertion. Never write beneath #*# header.
    updated=install_include(text)
    if updated != text:
        atomic_write_text(CFG/'printer.cfg',updated)
    print('printer.cfg include inserted before SAVE_CONFIG (if present).')
    (backup/'INSTALL_RECORD.json').write_text(json.dumps({'backup_dir':str(backup),
       'installed_file':str(KLIPPER),'printer_cfg_before':str(backup/'printer.cfg')},indent=2))
    print('INSTALLED (not yet active until Klipper RESTART).')
    print('Backup:',backup)
    print('Mainsail: RESTART Klipper while IDLE; AUTO_XY_TOGGLE is the bypass. Single-tool, COPY and MIRROR skip VAOC; two-tool INDEPENDENT IDEX calibrates (one inline retry).')
    print('Verify AUTO_XY_HEALTH and AUTO_XY_STATUS; leave Auto XY OFF until supervised testing.')
    print('Keep AUTO XY OFF for initial test; turn ON only after supervised validation.')


if __name__=='__main__':
    try:run()
    except Exception as e:
        print('INSTALL STOP: '+str(e),file=sys.stderr)
        sys.exit(2)
