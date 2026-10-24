"""Safe, narrowly scoped edits to Klipper printer.cfg.

Klipper's generated SAVE_CONFIG block is *not* ordinary cfg: normal include
statements must precede its header. Preserve a valid autosave block verbatim.
No Moonraker, Klipper or other printer dependencies: safely unit-testable.
"""
import os
import re
import stat
import tempfile
from pathlib import Path

AUTO_HEADER = re.compile(r'(?m)^#\*# <---------------------- SAVE_CONFIG ---------------------->[ \t]*\r?$')
INCLUDE_RE = re.compile(
    r'(?m)^[ \t]*\[include[ \t]+(auto_xy_print|auto_xy_control)\.cfg\][ \t]*(?:\r?\n|$)'
)
AUTOSAVE_LINE_RE = re.compile(r'^#\*#(?: |$)')
NEW_INCLUDE = '[include auto_xy_print.cfg]'
OLD_INCLUDE = '[include auto_xy_control.cfg]'


class PrinterCfgError(ValueError):
    """Refuse an ambiguous or damaged Klipper configuration."""


def _split(text, *, allow_known_tail=False):
    markers = list(AUTO_HEADER.finditer(text))
    if len(markers) > 1:
        raise PrinterCfgError('Multiple SAVE_CONFIG headers; do not edit automatically')
    if not markers:
        if re.search(r'(?m)^#\*#(?: |$)', text):
            raise PrinterCfgError('SAVE_CONFIG data without a recognisable header; inspect manually')
        return text, '', False
    pos = markers[0].start()
    regular, saved = text[:pos], text[pos:]
    if not re.search(r'(?m)^#\*# DO NOT EDIT THIS BLOCK OR BELOW\.', saved):
        raise PrinterCfgError('Unrecognised SAVE_CONFIG header/body; inspect manually')
    for line in saved.splitlines():
        if not line.strip():
            continue
        if AUTOSAVE_LINE_RE.match(line):
            continue
        if allow_known_tail and INCLUDE_RE.fullmatch(line):
            continue
        raise PrinterCfgError('Unexpected content after SAVE_CONFIG header: %r. '
                              'Stop and use repair_save_config.py if it is an old Auto XY include' % line[:100])
    return regular, saved, True


def _eol(text):
    return '\r\n' if '\r\n' in text and text.count('\r\n') >= text.count('\n') / 2 else '\n'


def _insert_before_autosave(regular, saved, include):
    eol = _eol(regular or saved)
    # The header remains strictly at the bottom, and its bytes are not rewritten.
    if regular.strip():
        return regular.rstrip('\r\n') + eol + eol + include + eol + eol + saved
    return include + eol + eol + saved


def install_include(text):
    """Return edited printer.cfg. Always preserve SAVE_CONFIG verbatim.

    Existing stage-5 include is replaced *in place*; an already-installed
    stage-6 include remains single. New includes go before the SAVE_CONFIG data.
    """
    regular, saved, _ = _split(text)
    found = list(INCLUDE_RE.finditer(regular))
    names = [m.group(1) for m in found]
    if len(found) > 1:
        raise PrinterCfgError('Duplicate Auto XY includes; resolve manually before installing')
    if found:
        if names[0] == 'auto_xy_print':
            return text  # exactly idempotent
        m = found[0]
        line = m.group()
        tail = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
        return regular[:m.start()] + NEW_INCLUDE + tail + regular[m.end():] + saved
    return _insert_before_autosave(regular, saved, NEW_INCLUDE)


def remove_include(text, *, restore_legacy=False):
    """Remove stage-6 include, optionally reinstall stage-5 in safe location."""
    regular, saved, _ = _split(text)
    matches = list(INCLUDE_RE.finditer(regular))
    if len(matches) > 1:
        raise PrinterCfgError('Duplicate Auto XY includes; resolve manually before disabling')
    if not matches:
        # Don't add a legacy include when stage-6 wasn't even installed.
        return text
    m = matches[0]
    if m.group(1) != 'auto_xy_print':
        return text
    if restore_legacy:
        tail = '\r\n' if m.group().endswith('\r\n') else '\n' if m.group().endswith('\n') else ''
        return regular[:m.start()] + OLD_INCLUDE + tail + regular[m.end():] + saved
    return regular[:m.start()] + regular[m.end():] + saved


def repair_legacy_tail(text):
    """Repair ONLY our misplaced include below SAVE_CONFIG, never other content.

    This is an explicit opt-in recovery action, not an install side effect.
    It refuses unexpected lines and keeps existing #*# lines unchanged.
    """
    regular, saved, has_header = _split(text, allow_known_tail=True)
    if not has_header:
        return text
    eol = _eol(text)
    kept = []
    bad = []
    for line in saved.splitlines(keepends=True):
        name = INCLUDE_RE.fullmatch(line.rstrip('\r\n'))
        if name:
            bad.append(name.group(1))
        else:
            kept.append(line)
    if not bad:
        return text
    if len(bad) > 1:
        raise PrinterCfgError('More than one misplaced Auto XY include; inspect manually')
    current = INCLUDE_RE.findall(regular)
    if len(current) > 1:
        raise PrinterCfgError('Duplicate Auto XY includes in normal config; inspect manually')
    if current and current[0] != bad[0]:
        raise PrinterCfgError('Conflicting Auto XY include types above/below SAVE_CONFIG')
    saved_clean = ''.join(kept)
    if current:
        result = regular + saved_clean
    else:
        inc = NEW_INCLUDE if bad[0] == 'auto_xy_print' else OLD_INCLUDE
        result = _insert_before_autosave(regular, saved_clean, inc)
    _split(result)  # revalidate before permitting write
    return result


def atomic_write_text(path, content):
    """Replace a validated text cfg atomically, preserving its permissions."""
    path = Path(path)
    existing = path.stat()
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='',
                                         prefix='.' + path.name + '.', suffix='.tmp',
                                         dir=str(path.parent), delete=False) as handle:
            tmp = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.chmod(stat.S_IMODE(existing.st_mode))
        os.replace(str(tmp), str(path))
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def remove_all_autoxy_includes(text):
    """Clean-uninstall known Stage5/6 includes, preserving SAVE_CONFIG byte-for-byte."""
    regular, saved, _ = _split(text)
    updated = INCLUDE_RE.sub('', regular) + saved
    _split(updated)
    return updated
