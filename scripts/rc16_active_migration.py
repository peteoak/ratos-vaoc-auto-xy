#!/usr/bin/env python3
"""RC16 active migration planning for recognised RC15 controllers.

Pure planning only: no file writes, printer motion, Klipper restarts, or
activation. Production installation requires a separate qualified transaction
with rollback and live readiness checks.
"""
import hashlib
import re
from pathlib import Path

BASELINE_SHA256 = '0a865df007e01e3687f318c300e037c3ca05f6bbdefa13fba99edcb2fdea9df3'
OWNER_RC15_SHA256 = '2f600766bc0322ae14c31592a0caf97a2e2e6bb0f1a28f42c08d147327528940'
RECOGNISED_CONTROLLERS = {
    BASELINE_SHA256: 'rc15-dev6-reference',
    OWNER_RC15_SHA256: 'rc15-dev6-owner-recovery-variant',
}
GATE_SECTION = re.compile(r'^\s*\[auto_xy_gate\]\s*$')
SECTION = re.compile(r'^\s*\[.*\]\s*$')
PROTECTION = re.compile(r'^(\s*protected_print_enabled\s*:\s*)(True|False)(\s*)$')


class MigrationError(RuntimeError):
    pass


def controller_variant(data):
    digest = hashlib.sha256(data).hexdigest()
    result = RECOGNISED_CONTROLLERS.get(digest)
    if result is None:
        raise MigrationError('Controller not recognised; preserving existing installation')
    return result


def preserve_enabled_gate(config_text):
    """Enable only the one protected-print key inside the one gate section."""
    lines = config_text.splitlines(keepends=True)
    gate_sections = 0
    in_gate = False
    matches = []
    for i, line in enumerate(lines):
        body = line.splitlines()[0] if line else ''
        if SECTION.fullmatch(body):
            in_gate = bool(GATE_SECTION.fullmatch(body))
            if in_gate:
                gate_sections += 1
        elif in_gate:
            match = PROTECTION.fullmatch(body)
            if match:
                matches.append((i, match))
    if gate_sections != 1:
        raise MigrationError('Expected one [auto_xy_gate] section')
    if len(matches) != 1:
        raise MigrationError('Expected one explicit protected_print_enabled setting in [auto_xy_gate]')
    index, match = matches[0]
    if match.group(2) == 'True':
        return config_text
    # Keep newline convention, surrounding whitespace, comments and every
    # other section byte-for-byte identical.
    line = lines[index]
    start, end = match.span(2)
    lines[index] = line[:start] + 'True' + line[end:]
    return ''.join(lines)


def installed_camera_python(home, gate_config=None):
    """Get the existing RC15 vision interpreter; don't force a different venv."""
    home = Path(home)
    current = (gate_config if gate_config is not None else
               (home / 'printer_data/config/auto_xy.cfg').read_text(encoding='utf-8'))
    starts = list(re.finditer(r'(?mi)^\s*\[auto_xy_gate\]\s*
    return ('# RC16 active protected-print handoff.\n'
            '[auto_xy_ratos_handoff_rc16]\n'
            'enabled: True\n'
            'worker: ' + str(home / 'auto_xy/workers/rc16_prepare_worker.py') + '\n'
            'worker_python: /usr/bin/python3\n'
            'registry_directory: ' + str(home / 'auto_xy/rc16_registry') + '\n'
            'retention_enabled: True\n'
            'retention_days: 30\n'
            'retention_interval_hours: 24\n'
            'retention_worker: ' + str(home / 'auto_xy/workers/rc16_generated_retention.py') + '\n'
            '\n[include auto_xy_recommission_rc16_macros.cfg]\n'
            '[include auto_xy_thermal_rc16_macros.cfg]\n'
            '\n[auto_xy_recommission_rc16]\n'
            'worker: ' + str(home / 'auto_xy/workers/rc16_recommission_worker.py') + '\n'
            'worker_python: ' + str(vision_python) + '\n'
            'reference_root: ' + str(home / 'auto_xy/nozzle_references') + '\n'
            'results_root: ' + str(home / 'auto_xy/print_gate_runs') + '\n'
            'profile: ' + str(home / 'auto_xy/camera_commission.json') + '\n')


def plan_existing_gate(home):
    """Inspect known files and return planned replacement bytes, without writing."""
    home = Path(home)
    controller = home / 'auto_xy/klipper/auto_xy_gate.py'
    settings = home / 'printer_data/config/auto_xy.cfg'
    if (not controller.is_file() or controller.is_symlink() or
            not settings.is_file() or settings.is_symlink()):
        raise MigrationError('Missing or unsafe installed controller/configuration')
    variant = controller_variant(controller.read_bytes())
    current = settings.read_text(encoding='utf-8')
    updated = preserve_enabled_gate(current)
    return {'variant': variant,
            'gate_config_changed': updated != current,
            'gate_config_bytes': updated.encode('utf-8'),
            'handoff_config_bytes': active_handoff_config(home, installed_camera_python(home, current)).encode('utf-8')}


def live_rc15_upgrade_safe(home, status_reader=None, python_check=None,
                            motion_reader=None):
    """Read-only RC15->RC16 preflight, distinct from RC15 *fresh/rollback*.

    A healthy installed RC15 may already have auto_xy_v2_enabled=1. That is
    the expected running configuration and MUST NOT block an active upgrade.
    Old fault/in-progress/retry markers are migrated only when provably stale.
    This function
    does not issue G-code, change saved variables or move/heat the printer.
    """
    import math
    import subprocess
    import install_autoxy as baseline

    home = Path(home)
    reader = status_reader if status_reader is not None else baseline.moonraker_status
    status, info = reader()

    def require(ok, message):
        if not ok:
            raise MigrationError(message)

    require(isinstance(info, dict) and info.get('state') == 'ready',
            'Klipper must be Ready for RC15 upgrade')
    require(isinstance(status, dict), 'Moonraker status unavailable')
    save = status.get('save_variables')
    require(isinstance(save, dict) and isinstance(save.get('variables'), dict),
            'Saved AutoXY state unavailable')
    variables = save['variables']

    def flag(name):
        return str(variables.get(name, 0)).strip().lower() in ('1', '1.0', 'true')

    # Legacy RC7 fault and in_progress records can be inert after the
    # documented clean RC15 migration. Never clear them automatically.
    for key in ('auto_xy_enabled', 'auto_xy_retry_available'):
        require(not flag(key), 'Legacy AutoXY remains enabled or held: ' + key)

    # Persisted RC15 fault/retry flags may outlive a failed or interrupted
    # standalone calibration. They can be archived and reset during the
    # *complete* RC16 install, but NEVER when a live recovery may be pending.
    # The installer does not change any saved variable during preflight.
    stale = {key: flag(key) for key in (
        'auto_xy_v2_fault', 'auto_xy_v2_in_progress',
        'auto_xy_v2_retry_available', 'auto_xy_v2_retry_used')}

    vaoc = status.get('gcode_macro _VAOC')
    require(isinstance(vaoc, dict) and vaoc.get('is_started') is False,
            'Exit native RatOS VAOC before installation')
    print_stats = status.get('print_stats')
    require(isinstance(print_stats, dict) and
            str(print_stats.get('state', '')).lower() in
            ('standby', 'complete', 'cancelled', 'error'),
            'Print active or state unknown; installation refused')
    sd = status.get('virtual_sdcard')
    require(isinstance(sd, dict) and sd.get('is_active') is False,
            'Virtual-SD active or unknown; installation refused')
    # The user may have old, latched state. It is only *stale* when there
    # is no selected file to recover and the last print is terminal. An ERROR
    # state is deliberately not assumed terminal while recovery is latched.
    # Never clear a selected/held print or throw away a possible resume token.
    selected = bool(sd.get('file_path') or sd.get('filename'))
    state = str(print_stats.get('state', '')).lower()
    if any(stale.values()):
        require(not selected and state in ('standby', 'complete', 'cancelled'),
                'Possible held RC15 print/retry context; cancel or inspect before upgrade')
    # Klipper 'toolhead' exposes position, homed axes and MAX velocity;
    # it does NOT expose instantaneous velocity. Use motion_report.live_velocity
    # instead. Missing/unreadable motion data is a safe refusal, not 0 mm/s.
    motion = status.get('motion_report')
    if motion is None:
        if motion_reader is not None:
            motion = motion_reader()
        else:
            import json
            from urllib.request import urlopen
            try:
                with urlopen(
                        'http://127.0.0.1:7125/printer/objects/query?motion_report',
                        timeout=8) as response:
                    motion = json.load(response)['result']['status']['motion_report']
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise MigrationError(
                    'Moonraker motion_report unavailable; cannot confirm printer idle') from exc
    require(isinstance(motion, dict),
            'Moonraker motion_report unavailable; cannot confirm printer idle')
    try:
        velocity = float(motion.get('live_velocity', float('nan')))
    except (ValueError, TypeError) as exc:
        raise MigrationError('Invalid motion_report.live_velocity') from exc
    require(math.isfinite(velocity) and abs(velocity) < .001,
            'Printer is moving or motion_report.live_velocity unknown')

    for heater in ('extruder', 'extruder1', 'heater_bed'):
        obj = status.get(heater)
        require(isinstance(obj, dict), 'Missing heater status: ' + heater)
        try:
            target = float(obj.get('target', float('nan')))
            measured = float(obj.get('temperature', float('nan')))
        except (ValueError, TypeError) as exc:
            raise MigrationError('Invalid heater status: ' + heater) from exc
        require(math.isfinite(target) and math.isfinite(measured) and
                target == 0. and measured < 50.,
                'Heater must be cool with target 0: ' + heater)

    config = status.get('configfile')
    require(isinstance(config, dict) and isinstance(config.get('settings'), dict),
            'Klipper configuration status unavailable')
    settings = config['settings']
    names = baseline.require_ratos_components(settings)
    require('auto_xy_gate' in names, 'Existing RC15 gate not loaded')
    gate = settings.get('auto_xy_gate', {})
    require(isinstance(gate, dict) and gate.get('worker') ==
            str(home / 'auto_xy/vision/auto_xy_vision_worker.py'),
            'Installed RC15 vision worker path differs; manual review needed')

    interpreter = installed_camera_python(home)
    configured = gate.get('python', str(home / 'auto-xy-venv/bin/python'))
    require(str(interpreter) == str(configured),
            'Live and configured AutoXY camera Python interpreters differ')
    standard = home / 'auto-xy-venv'
    # The standard, entirely absent venv can be created later by the one-shot
    # installer. Preflight is read-only, so report rather than make changes.
    needs_bootstrap = False
    if not interpreter.is_file():
        if (interpreter == standard / 'bin/python' and
                not standard.exists() and not standard.is_symlink()):
            import vision_env
            vision_env.host_check()
            needs_bootstrap = True
        else:
            raise MigrationError(
                'Configured AutoXY vision Python missing: %s. '
                'Existing environments are never silently overwritten.' % interpreter)
    else:
        check = (python_check if python_check is not None else
                 lambda command: subprocess.run(command, capture_output=True,
                                                text=True, timeout=12))
        try:
            result = check([str(interpreter), '-c',
                            'import cv2, numpy; print(cv2.__version__, numpy.__version__)'])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MigrationError('AutoXY camera Python environment could not be checked') from exc
        require(result.returncode == 0,
                'Configured camera Python %s cannot import OpenCV/NumPy: %s' %
                (interpreter, str(getattr(result, 'stderr', ''))[-300:]))

    offsets = {}
    for key in ('idex_xoffset', 'idex_yoffset', 'idex_zoffset'):
        require(key in variables, 'Required saved RatOS offset missing: ' + key)
        try:
            numeric = float(variables[key])
        except (ValueError, TypeError) as exc:
            raise MigrationError('Invalid saved RatOS offset: ' + key) from exc
        require(math.isfinite(numeric),
                'Nonfinite saved RatOS offset: ' + key)
        offsets[key] = variables[key]
    return {'existing_enabled': flag('auto_xy_v2_enabled'),
            'measure_only': flag('auto_xy_v2_measure_only'),
            'stale_flags': stale,
            'saved_offsets': offsets,
            'vision_python': str(interpreter),
            'vision_bootstrap_required': needs_bootstrap}
, current))
    if len(starts) != 1:
        raise MigrationError('Expected exactly one installed [auto_xy_gate]')
    section = current[starts[0].end():]
    next_section = re.search(r'(?m)^\s*\[.+?\]\s*
    return ('# RC16 active protected-print handoff.\n'
            '[auto_xy_ratos_handoff_rc16]\n'
            'enabled: True\n'
            'worker: ' + str(home / 'auto_xy/workers/rc16_prepare_worker.py') + '\n'
            'worker_python: /usr/bin/python3\n'
            'registry_directory: ' + str(home / 'auto_xy/rc16_registry') + '\n'
            'retention_enabled: True\n'
            'retention_days: 30\n'
            'retention_interval_hours: 24\n'
            'retention_worker: ' + str(home / 'auto_xy/workers/rc16_generated_retention.py') + '\n'
            '\n[include auto_xy_recommission_rc16_macros.cfg]\n'
            '[include auto_xy_thermal_rc16_macros.cfg]\n'
            '\n[auto_xy_recommission_rc16]\n'
            'worker: ' + str(home / 'auto_xy/workers/rc16_recommission_worker.py') + '\n'
            'worker_python: ' + str(home / 'auto-xy-venv/bin/python') + '\n'
            'reference_root: ' + str(home / 'auto_xy/nozzle_references') + '\n'
            'results_root: ' + str(home / 'auto_xy/print_gate_runs') + '\n'
            'profile: ' + str(home / 'auto_xy/camera_commission.json') + '\n')


def plan_existing_gate(home):
    """Inspect known files and return planned replacement bytes, without writing."""
    home = Path(home)
    controller = home / 'auto_xy/klipper/auto_xy_gate.py'
    settings = home / 'printer_data/config/auto_xy.cfg'
    if (not controller.is_file() or controller.is_symlink() or
            not settings.is_file() or settings.is_symlink()):
        raise MigrationError('Missing or unsafe installed controller/configuration')
    variant = controller_variant(controller.read_bytes())
    current = settings.read_text(encoding='utf-8')
    updated = preserve_enabled_gate(current)
    return {'variant': variant,
            'gate_config_changed': updated != current,
            'gate_config_bytes': updated.encode('utf-8'),
            'handoff_config_bytes': active_handoff_config(home).encode('utf-8')}


def live_rc15_upgrade_safe(home, status_reader=None, python_check=None,
                            motion_reader=None):
    """Read-only RC15->RC16 preflight, distinct from RC15 *fresh/rollback*.

    A healthy installed RC15 may already have auto_xy_v2_enabled=1. That is
    the expected running configuration and MUST NOT block an active upgrade.
    Old fault/in-progress/retry markers are migrated only when provably stale.
    This function
    does not issue G-code, change saved variables or move/heat the printer.
    """
    import math
    import subprocess
    import install_autoxy as baseline

    home = Path(home)
    reader = status_reader if status_reader is not None else baseline.moonraker_status
    status, info = reader()

    def require(ok, message):
        if not ok:
            raise MigrationError(message)

    require(isinstance(info, dict) and info.get('state') == 'ready',
            'Klipper must be Ready for RC15 upgrade')
    require(isinstance(status, dict), 'Moonraker status unavailable')
    save = status.get('save_variables')
    require(isinstance(save, dict) and isinstance(save.get('variables'), dict),
            'Saved AutoXY state unavailable')
    variables = save['variables']

    def flag(name):
        return str(variables.get(name, 0)).strip().lower() in ('1', '1.0', 'true')

    # Legacy RC7 fault and in_progress records can be inert after the
    # documented clean RC15 migration. Never clear them automatically.
    for key in ('auto_xy_enabled', 'auto_xy_retry_available'):
        require(not flag(key), 'Legacy AutoXY remains enabled or held: ' + key)

    # Persisted RC15 fault/retry flags may outlive a failed or interrupted
    # standalone calibration. They can be archived and reset during the
    # *complete* RC16 install, but NEVER when a live recovery may be pending.
    # The installer does not change any saved variable during preflight.
    stale = {key: flag(key) for key in (
        'auto_xy_v2_fault', 'auto_xy_v2_in_progress',
        'auto_xy_v2_retry_available', 'auto_xy_v2_retry_used')}

    vaoc = status.get('gcode_macro _VAOC')
    require(isinstance(vaoc, dict) and vaoc.get('is_started') is False,
            'Exit native RatOS VAOC before installation')
    print_stats = status.get('print_stats')
    require(isinstance(print_stats, dict) and
            str(print_stats.get('state', '')).lower() in
            ('standby', 'complete', 'cancelled', 'error'),
            'Print active or state unknown; installation refused')
    sd = status.get('virtual_sdcard')
    require(isinstance(sd, dict) and sd.get('is_active') is False,
            'Virtual-SD active or unknown; installation refused')
    # The user may have old, latched state. It is only *stale* when there
    # is no selected file to recover and the last print is terminal. An ERROR
    # state is deliberately not assumed terminal while recovery is latched.
    # Never clear a selected/held print or throw away a possible resume token.
    selected = bool(sd.get('file_path') or sd.get('filename'))
    state = str(print_stats.get('state', '')).lower()
    if any(stale.values()):
        require(not selected and state in ('standby', 'complete', 'cancelled'),
                'Possible held RC15 print/retry context; cancel or inspect before upgrade')
    # Klipper 'toolhead' exposes position, homed axes and MAX velocity;
    # it does NOT expose instantaneous velocity. Use motion_report.live_velocity
    # instead. Missing/unreadable motion data is a safe refusal, not 0 mm/s.
    motion = status.get('motion_report')
    if motion is None:
        if motion_reader is not None:
            motion = motion_reader()
        else:
            import json
            from urllib.request import urlopen
            try:
                with urlopen(
                        'http://127.0.0.1:7125/printer/objects/query?motion_report',
                        timeout=8) as response:
                    motion = json.load(response)['result']['status']['motion_report']
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise MigrationError(
                    'Moonraker motion_report unavailable; cannot confirm printer idle') from exc
    require(isinstance(motion, dict),
            'Moonraker motion_report unavailable; cannot confirm printer idle')
    try:
        velocity = float(motion.get('live_velocity', float('nan')))
    except (ValueError, TypeError) as exc:
        raise MigrationError('Invalid motion_report.live_velocity') from exc
    require(math.isfinite(velocity) and abs(velocity) < .001,
            'Printer is moving or motion_report.live_velocity unknown')

    for heater in ('extruder', 'extruder1', 'heater_bed'):
        obj = status.get(heater)
        require(isinstance(obj, dict), 'Missing heater status: ' + heater)
        try:
            target = float(obj.get('target', float('nan')))
            measured = float(obj.get('temperature', float('nan')))
        except (ValueError, TypeError) as exc:
            raise MigrationError('Invalid heater status: ' + heater) from exc
        require(math.isfinite(target) and math.isfinite(measured) and
                target == 0. and measured < 50.,
                'Heater must be cool with target 0: ' + heater)

    config = status.get('configfile')
    require(isinstance(config, dict) and isinstance(config.get('settings'), dict),
            'Klipper configuration status unavailable')
    settings = config['settings']
    names = baseline.require_ratos_components(settings)
    require('auto_xy_gate' in names, 'Existing RC15 gate not loaded')
    gate = settings.get('auto_xy_gate', {})
    require(isinstance(gate, dict) and gate.get('worker') ==
            str(home / 'auto_xy/vision/auto_xy_vision_worker.py'),
            'Installed RC15 vision worker path differs; manual review needed')

    interpreter = home / 'auto-xy-venv/bin/python'
    require(interpreter.is_file() and not interpreter.is_symlink(),
            'AutoXY camera Python environment missing')
    check = (python_check if python_check is not None else
             lambda command: subprocess.run(command, capture_output=True,
                                            text=True, timeout=12))
    try:
        result = check([str(interpreter), '-c',
                        'import cv2, numpy; print(cv2.__version__, numpy.__version__)'])
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MigrationError('AutoXY camera Python environment could not be checked') from exc
    require(result.returncode == 0,
            'AutoXY camera environment missing OpenCV/NumPy')

    offsets = {}
    for key in ('idex_xoffset', 'idex_yoffset', 'idex_zoffset'):
        require(key in variables, 'Required saved RatOS offset missing: ' + key)
        try:
            numeric = float(variables[key])
        except (ValueError, TypeError) as exc:
            raise MigrationError('Invalid saved RatOS offset: ' + key) from exc
        require(math.isfinite(numeric),
                'Nonfinite saved RatOS offset: ' + key)
        offsets[key] = variables[key]
    return {'existing_enabled': flag('auto_xy_v2_enabled'),
            'measure_only': flag('auto_xy_v2_measure_only'),
            'stale_flags': stale,
            'saved_offsets': offsets}
, section)
    if next_section:
        section = section[:next_section.start()]
    entries = re.findall(r'(?mi)^[ \t]*python[ \t]*:[ \t]*([^#\r\n]+)', section)
    if len(entries) > 1:
        raise MigrationError('Duplicated AutoXY vision Python setting')
    raw = entries[0].strip() if entries else str(home / 'auto-xy-venv/bin/python')
    if not raw or not Path(raw).is_absolute():
        raise MigrationError('AutoXY camera interpreter must use an absolute path')
    return Path(raw)


def active_handoff_config(home, vision_python=None):
    """Full operating profile, including background generated-file retention."""
    home = Path(home)
    vision_python = Path(vision_python) if vision_python else home / 'auto-xy-venv/bin/python'
    return ('# RC16 active protected-print handoff.\n'
            '[auto_xy_ratos_handoff_rc16]\n'
            'enabled: True\n'
            'worker: ' + str(home / 'auto_xy/workers/rc16_prepare_worker.py') + '\n'
            'worker_python: /usr/bin/python3\n'
            'registry_directory: ' + str(home / 'auto_xy/rc16_registry') + '\n'
            'retention_enabled: True\n'
            'retention_days: 30\n'
            'retention_interval_hours: 24\n'
            'retention_worker: ' + str(home / 'auto_xy/workers/rc16_generated_retention.py') + '\n'
            '\n[include auto_xy_recommission_rc16_macros.cfg]\n'
            '[include auto_xy_thermal_rc16_macros.cfg]\n'
            '\n[auto_xy_recommission_rc16]\n'
            'worker: ' + str(home / 'auto_xy/workers/rc16_recommission_worker.py') + '\n'
            'worker_python: ' + str(home / 'auto-xy-venv/bin/python') + '\n'
            'reference_root: ' + str(home / 'auto_xy/nozzle_references') + '\n'
            'results_root: ' + str(home / 'auto_xy/print_gate_runs') + '\n'
            'profile: ' + str(home / 'auto_xy/camera_commission.json') + '\n')


def plan_existing_gate(home):
    """Inspect known files and return planned replacement bytes, without writing."""
    home = Path(home)
    controller = home / 'auto_xy/klipper/auto_xy_gate.py'
    settings = home / 'printer_data/config/auto_xy.cfg'
    if (not controller.is_file() or controller.is_symlink() or
            not settings.is_file() or settings.is_symlink()):
        raise MigrationError('Missing or unsafe installed controller/configuration')
    variant = controller_variant(controller.read_bytes())
    current = settings.read_text(encoding='utf-8')
    updated = preserve_enabled_gate(current)
    return {'variant': variant,
            'gate_config_changed': updated != current,
            'gate_config_bytes': updated.encode('utf-8'),
            'handoff_config_bytes': active_handoff_config(home).encode('utf-8')}


def live_rc15_upgrade_safe(home, status_reader=None, python_check=None,
                            motion_reader=None):
    """Read-only RC15->RC16 preflight, distinct from RC15 *fresh/rollback*.

    A healthy installed RC15 may already have auto_xy_v2_enabled=1. That is
    the expected running configuration and MUST NOT block an active upgrade.
    Old fault/in-progress/retry markers are migrated only when provably stale.
    This function
    does not issue G-code, change saved variables or move/heat the printer.
    """
    import math
    import subprocess
    import install_autoxy as baseline

    home = Path(home)
    reader = status_reader if status_reader is not None else baseline.moonraker_status
    status, info = reader()

    def require(ok, message):
        if not ok:
            raise MigrationError(message)

    require(isinstance(info, dict) and info.get('state') == 'ready',
            'Klipper must be Ready for RC15 upgrade')
    require(isinstance(status, dict), 'Moonraker status unavailable')
    save = status.get('save_variables')
    require(isinstance(save, dict) and isinstance(save.get('variables'), dict),
            'Saved AutoXY state unavailable')
    variables = save['variables']

    def flag(name):
        return str(variables.get(name, 0)).strip().lower() in ('1', '1.0', 'true')

    # Legacy RC7 fault and in_progress records can be inert after the
    # documented clean RC15 migration. Never clear them automatically.
    for key in ('auto_xy_enabled', 'auto_xy_retry_available'):
        require(not flag(key), 'Legacy AutoXY remains enabled or held: ' + key)

    # Persisted RC15 fault/retry flags may outlive a failed or interrupted
    # standalone calibration. They can be archived and reset during the
    # *complete* RC16 install, but NEVER when a live recovery may be pending.
    # The installer does not change any saved variable during preflight.
    stale = {key: flag(key) for key in (
        'auto_xy_v2_fault', 'auto_xy_v2_in_progress',
        'auto_xy_v2_retry_available', 'auto_xy_v2_retry_used')}

    vaoc = status.get('gcode_macro _VAOC')
    require(isinstance(vaoc, dict) and vaoc.get('is_started') is False,
            'Exit native RatOS VAOC before installation')
    print_stats = status.get('print_stats')
    require(isinstance(print_stats, dict) and
            str(print_stats.get('state', '')).lower() in
            ('standby', 'complete', 'cancelled', 'error'),
            'Print active or state unknown; installation refused')
    sd = status.get('virtual_sdcard')
    require(isinstance(sd, dict) and sd.get('is_active') is False,
            'Virtual-SD active or unknown; installation refused')
    # The user may have old, latched state. It is only *stale* when there
    # is no selected file to recover and the last print is terminal. An ERROR
    # state is deliberately not assumed terminal while recovery is latched.
    # Never clear a selected/held print or throw away a possible resume token.
    selected = bool(sd.get('file_path') or sd.get('filename'))
    state = str(print_stats.get('state', '')).lower()
    if any(stale.values()):
        require(not selected and state in ('standby', 'complete', 'cancelled'),
                'Possible held RC15 print/retry context; cancel or inspect before upgrade')
    # Klipper 'toolhead' exposes position, homed axes and MAX velocity;
    # it does NOT expose instantaneous velocity. Use motion_report.live_velocity
    # instead. Missing/unreadable motion data is a safe refusal, not 0 mm/s.
    motion = status.get('motion_report')
    if motion is None:
        if motion_reader is not None:
            motion = motion_reader()
        else:
            import json
            from urllib.request import urlopen
            try:
                with urlopen(
                        'http://127.0.0.1:7125/printer/objects/query?motion_report',
                        timeout=8) as response:
                    motion = json.load(response)['result']['status']['motion_report']
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise MigrationError(
                    'Moonraker motion_report unavailable; cannot confirm printer idle') from exc
    require(isinstance(motion, dict),
            'Moonraker motion_report unavailable; cannot confirm printer idle')
    try:
        velocity = float(motion.get('live_velocity', float('nan')))
    except (ValueError, TypeError) as exc:
        raise MigrationError('Invalid motion_report.live_velocity') from exc
    require(math.isfinite(velocity) and abs(velocity) < .001,
            'Printer is moving or motion_report.live_velocity unknown')

    for heater in ('extruder', 'extruder1', 'heater_bed'):
        obj = status.get(heater)
        require(isinstance(obj, dict), 'Missing heater status: ' + heater)
        try:
            target = float(obj.get('target', float('nan')))
            measured = float(obj.get('temperature', float('nan')))
        except (ValueError, TypeError) as exc:
            raise MigrationError('Invalid heater status: ' + heater) from exc
        require(math.isfinite(target) and math.isfinite(measured) and
                target == 0. and measured < 50.,
                'Heater must be cool with target 0: ' + heater)

    config = status.get('configfile')
    require(isinstance(config, dict) and isinstance(config.get('settings'), dict),
            'Klipper configuration status unavailable')
    settings = config['settings']
    names = baseline.require_ratos_components(settings)
    require('auto_xy_gate' in names, 'Existing RC15 gate not loaded')
    gate = settings.get('auto_xy_gate', {})
    require(isinstance(gate, dict) and gate.get('worker') ==
            str(home / 'auto_xy/vision/auto_xy_vision_worker.py'),
            'Installed RC15 vision worker path differs; manual review needed')

    interpreter = home / 'auto-xy-venv/bin/python'
    require(interpreter.is_file() and not interpreter.is_symlink(),
            'AutoXY camera Python environment missing')
    check = (python_check if python_check is not None else
             lambda command: subprocess.run(command, capture_output=True,
                                            text=True, timeout=12))
    try:
        result = check([str(interpreter), '-c',
                        'import cv2, numpy; print(cv2.__version__, numpy.__version__)'])
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MigrationError('AutoXY camera Python environment could not be checked') from exc
    require(result.returncode == 0,
            'AutoXY camera environment missing OpenCV/NumPy')

    offsets = {}
    for key in ('idex_xoffset', 'idex_yoffset', 'idex_zoffset'):
        require(key in variables, 'Required saved RatOS offset missing: ' + key)
        try:
            numeric = float(variables[key])
        except (ValueError, TypeError) as exc:
            raise MigrationError('Invalid saved RatOS offset: ' + key) from exc
        require(math.isfinite(numeric),
                'Nonfinite saved RatOS offset: ' + key)
        offsets[key] = variables[key]
    return {'existing_enabled': flag('auto_xy_v2_enabled'),
            'measure_only': flag('auto_xy_v2_measure_only'),
            'stale_flags': stale,
            'saved_offsets': offsets}
