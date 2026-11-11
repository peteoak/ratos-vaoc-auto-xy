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
GATE_SECTION = re.compile(r'^\s*\[auto_xy_gate\]\s*


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


def active_handoff_config(home):
    """Full operating profile, including background generated-file retention."""
    home = Path(home)
    return ('# RC16 active protected-print handoff.\n'
            '[auto_xy_ratos_handoff_rc16]\n'
            'enabled: True\n'
            'worker: ' + str(home / 'auto_xy/workers/rc16_prepare_worker.py') + '\n'
            'worker_python: /usr/bin/python3\n'
            'registry_directory: ' + str(home / 'auto_xy/rc16_registry') + '\n'
            'retention_enabled: True\n'
            'retention_days: 30\n'
            'retention_interval_hours: 24\n'
            'retention_worker: ' + str(home / 'auto_xy/workers/rc16_generated_retention.py') + '\n')


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
)
SECTION = re.compile(r'^\s*\[[^\]\n]+\]\s*


class MigrationError(RuntimeError):
    pass


def controller_variant(data):
    digest = hashlib.sha256(data).hexdigest()
    result = RECOGNISED_CONTROLLERS.get(digest)
    if result is None:
        raise MigrationError('Controller not recognised; preserving existing installation')
    return result


def preserve_enabled_gate(config_text):
    """Keep other settings intact, but require an unambiguous enabled gate."""
    if len(GATE_SECTION.findall(config_text)) != 1:
        raise MigrationError('Expected one [auto_xy_gate] section')
    matches = list(PROTECTION.finditer(config_text))
    if len(matches) != 1:
        raise MigrationError('Expected one explicit protected_print_enabled setting')
    match = matches[0]
    if match.group(2) == 'True':
        return config_text
    return config_text[:match.start(2)] + 'True' + config_text[match.end(2):]


def active_handoff_config(home):
    """Full operating profile, including background generated-file retention."""
    home = Path(home)
    return ('# RC16 active protected-print handoff.\n'
            '[auto_xy_ratos_handoff_rc16]\n'
            'enabled: True\n'
            'worker: ' + str(home / 'auto_xy/workers/rc16_prepare_worker.py') + '\n'
            'worker_python: /usr/bin/python3\n'
            'registry_directory: ' + str(home / 'auto_xy/rc16_registry') + '\n'
            'retention_enabled: True\n'
            'retention_days: 30\n'
            'retention_interval_hours: 24\n'
            'retention_worker: ' + str(home / 'auto_xy/workers/rc16_generated_retention.py') + '\n')


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
)
PROTECTION = re.compile(r'^(\s*protected_print_enabled\s*:\s*)(True|False)(\s*)


class MigrationError(RuntimeError):
    pass


def controller_variant(data):
    digest = hashlib.sha256(data).hexdigest()
    result = RECOGNISED_CONTROLLERS.get(digest)
    if result is None:
        raise MigrationError('Controller not recognised; preserving existing installation')
    return result


def preserve_enabled_gate(config_text):
    """Keep other settings intact, but require an unambiguous enabled gate."""
    if len(GATE_SECTION.findall(config_text)) != 1:
        raise MigrationError('Expected one [auto_xy_gate] section')
    matches = list(PROTECTION.finditer(config_text))
    if len(matches) != 1:
        raise MigrationError('Expected one explicit protected_print_enabled setting')
    match = matches[0]
    if match.group(2) == 'True':
        return config_text
    return config_text[:match.start(2)] + 'True' + config_text[match.end(2):]


def active_handoff_config(home):
    """Full operating profile, including background generated-file retention."""
    home = Path(home)
    return ('# RC16 active protected-print handoff.\n'
            '[auto_xy_ratos_handoff_rc16]\n'
            'enabled: True\n'
            'worker: ' + str(home / 'auto_xy/workers/rc16_prepare_worker.py') + '\n'
            'worker_python: /usr/bin/python3\n'
            'registry_directory: ' + str(home / 'auto_xy/rc16_registry') + '\n'
            'retention_enabled: True\n'
            'retention_days: 30\n'
            'retention_interval_hours: 24\n'
            'retention_worker: ' + str(home / 'auto_xy/workers/rc16_generated_retention.py') + '\n')


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
)


class MigrationError(RuntimeError):
    pass


def controller_variant(data):
    digest = hashlib.sha256(data).hexdigest()
    result = RECOGNISED_CONTROLLERS.get(digest)
    if result is None:
        raise MigrationError('Controller not recognised; preserving existing installation')
    return result


def preserve_enabled_gate(config_text):
    """Keep other settings intact, but require an unambiguous enabled gate."""
    if len(GATE_SECTION.findall(config_text)) != 1:
        raise MigrationError('Expected one [auto_xy_gate] section')
    matches = list(PROTECTION.finditer(config_text))
    if len(matches) != 1:
        raise MigrationError('Expected one explicit protected_print_enabled setting')
    match = matches[0]
    if match.group(2) == 'True':
        return config_text
    return config_text[:match.start(2)] + 'True' + config_text[match.end(2):]


def active_handoff_config(home):
    """Full operating profile, including background generated-file retention."""
    home = Path(home)
    return ('# RC16 active protected-print handoff.\n'
            '[auto_xy_ratos_handoff_rc16]\n'
            'enabled: True\n'
            'worker: ' + str(home / 'auto_xy/workers/rc16_prepare_worker.py') + '\n'
            'worker_python: /usr/bin/python3\n'
            'registry_directory: ' + str(home / 'auto_xy/rc16_registry') + '\n'
            'retention_enabled: True\n'
            'retention_days: 30\n'
            'retention_interval_hours: 24\n'
            'retention_worker: ' + str(home / 'auto_xy/workers/rc16_generated_retention.py') + '\n')


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
