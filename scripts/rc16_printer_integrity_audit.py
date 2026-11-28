#!/usr/bin/env python3
"""Read-only AutoXY/RatOS printer extension inventory for supervised review.

Collects metadata, hashes, section/include names, and static interception
locations. Does NOT read/export G-code, calibration frames, camera profiles,
saved offsets, secrets, config values, or complete source/config files.
Does not import third-party printer code, restart services, change settings,
remove files, alter AutoXY ON/OFF, or execute a print command.

Use a revision-pinned GitHub repository checkout: the source manifest from
that checkout is used ONLY as a comparison baseline. All deviations are
classified as needing review, never silently modified.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

HOME = Path('/home/pi')
MAX_FILE = 2_500_000
MAX_SOURCES = 2500
MAX_CONFIGS = 300
MAX_LINE = 50_000
SKIP_PARTS = frozenset({
    '__pycache__', '.git', '.venv', 'venv', 'vision-env', 'vision_venv',
    'print_gate_runs', 'stage6_backups', 'rollbacks', 'backups',
    'autoxy_camera_refs', 'camera_refs', 'rc16_registry', 'registry',
    'generated', 'processed', 'gcodes', 'gcode_files',
    'vision_env', 'venv311', 'vision_venv311',
})
CODE_EXTENSIONS = frozenset({'.py', '.cfg', '.sh', '.service'})
INCLUDE = re.compile(r'^\s*\[include\s+([^\]]+)\]\s*$', re.I)
SECTION = re.compile(r'^\s*\[([^\]]+)\]\s*$')
HOOKS = {
    'PROCESS_GCODE_FILE': re.compile(r'PROCESS_GCODE_FILE', re.I),
    'SDCARD_PRINT_FILE': re.compile(r'SDCARD_PRINT_FILE(?:_BASE)?', re.I),
    'VIRTUAL_SD_RESUME': re.compile(r'\bdo_resume\b|\bM24\b', re.I),
    'M23_M26': re.compile(r'\bM23\b|\bM26\b', re.I),
    'START_PRINT': re.compile(r'\bSTART_PRINT\b', re.I),
    'AUTOXY_COMMAND': re.compile(r'\bAUTO_XY_|\bAUTOXY_', re.I),
}
EXPECTED_PATHS = {
    'auto_xy/klipper/auto_xy_gate.py': 'src/auto_xy_gate_rc16.py',
    'auto_xy/klipper/auto_xy_ratos_handoff_rc16.py': 'src/auto_xy_ratos_handoff_rc16.py',
    'auto_xy/vision/auto_xy_detector_rc16.py': 'src/auto_xy_detector_rc16.py',
    'auto_xy/vision/auto_xy_vision_worker.py': 'src/auto_xy_vision_worker_rc16.py',
    'auto_xy/vision/auto_xy_reference_signature_rc16.py': 'src/auto_xy_reference_signature_rc16.py',
    'auto_xy/vision/auto_xy_commission_candidates_rc16.py': 'src/auto_xy_commission_candidates_rc16.py',
    'auto_xy/vision/auto_xy_retention_rc16.py': 'src/auto_xy_retention_rc16.py',
    'auto_xy/klipper/auto_xy_recommission_rc16.py': 'src/auto_xy_recommission_rc16.py',
    'auto_xy/workers/rc16_prepare_worker.py': 'src/tools/rc16_prepare_worker.py',
    'auto_xy/workers/rc16_generated_retention.py': 'src/tools/rc16_generated_retention.py',
}
TRIAL04_SHA256 = '12ddf4100b6b61cbb71f3cc1b26d0d87e2134a8c9cbdb0385f83aba50e11c03f'
STATUS_QUERY = (
    'http://127.0.0.1:7125/printer/objects/query?'
    'print_stats&virtual_sdcard&gcode_macro%20_VAOC&'
    'auto_xy_ratos_handoff_rc16&save_variables&configfile'
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_blob(data):
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()


def path_under(root, candidate):
    try:
        return candidate.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False


def sha_file(path):
    h1, h2 = hashlib.sha256(), hashlib.sha1()
    size = path.stat().st_size
    if size > MAX_FILE:
        return {'sha256': None, 'git_blob': None, 'size': size, 'note': 'exceeds metadata hash limit'}
    h2.update(b'blob '+str(size).encode()+b'\0')
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h1.update(chunk)
            h2.update(chunk)
    return {'sha256': h1.hexdigest(), 'git_blob': h2.hexdigest(), 'size': size}


def describe_file(path, root):
    """Only metadata and checksums; never return file content."""
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    row = {'path': rel, 'kind': 'symlink' if path.is_symlink() else 'regular'}
    if path.is_symlink():
        try:
            row['target'] = str(path.resolve())
        except (OSError, RuntimeError) as exc:
            row['note'] = type(exc).__name__
    try:
        if not path.is_file():
            row['status'] = 'missing_or_nonregular'
        else:
            row.update(sha_file(path))
    except (OSError, RuntimeError) as exc:
        row['status'] = type(exc).__name__
    return row


def code_hits(path):
    """Return pattern name and bounded line numbers, never source snippets."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            return {}
        hits = {}
        with path.open('r', encoding='utf-8', errors='replace') as f:
            for lineno, line in enumerate(f, 1):
                if lineno > MAX_LINE:
                    break
                for name, regex in HOOKS.items():
                    if regex.search(line):
                        hits.setdefault(name, [])
                        if len(hits[name]) < 25:
                            hits[name].append(lineno)
        return hits
    except (OSError, RuntimeError):
        return {}


def safe_source_paths(root):
    """Don't descend camera, G-code, history, venv, image or backup trees."""
    if not root.is_dir() or root.is_symlink():
        return []
    found = []
    for where, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_PARTS and
                         not d.startswith('.') and not (Path(where)/d).is_symlink())
        for name in sorted(files):
            path = Path(where)/name
            if path.suffix.lower() not in CODE_EXTENSIONS:
                continue
            if len(found) >= MAX_SOURCES:
                return found
            found.append(path)
    return found


def config_includes(home):
    """List only names and section headings of active, recursively included cfgs."""
    root = home/'printer_data/config'
    entry = root/'printer.cfg'
    result = {'active_files': [], 'edges': [], 'sections': [],
              'warnings': [], 'active_legacy_trial04': False}
    if not entry.is_file():
        result['warnings'].append('printer.cfg unavailable')
        return result
    visited = set()
    queue = [entry]
    while queue:
        path = queue.pop(0)
        if path in visited:
            continue
        if len(visited) >= MAX_CONFIGS:
            result['warnings'].append('config traversal limit exceeded')
            break
        if not path_under(root, path):
            result['warnings'].append('out-of-tree include refused')
            continue
        visited.add(path)
        rel = str(path.relative_to(root))
        if not path.is_file() or path.is_symlink():
            result['warnings'].append('unreadable or linked active config: '+rel)
            continue
        result['active_files'].append(rel)
        try:
            with path.open(encoding='utf-8', errors='replace') as stream:
                for line in stream:
                    trimmed = line.strip()
                    if trimmed.startswith(('#',';')):
                        continue
                    inc = INCLUDE.fullmatch(trimmed)
                    if inc:
                        pattern = inc.group(1).strip()
                        # Only paths from config root are followed; no absolute or '..'.
                        if pattern.startswith('/') or '..' in Path(pattern).parts:
                            result['warnings'].append('unsafe active include under '+rel)
                            continue
                        matches = sorted(root.glob(pattern))[:MAX_CONFIGS]
                        result['edges'].append({'from':rel,'pattern':pattern,
                                                'count':len(matches)})
                        if not matches:
                            result['warnings'].append('missing include: '+pattern)
                        for candidate in matches:
                            if candidate.is_file() and path_under(root, candidate):
                                queue.append(candidate)
                            else:
                                result['warnings'].append('unreadable/outside include: '+pattern)
                        continue
                    section = SECTION.fullmatch(trimmed)
                    if section:
                        sec = section.group(1).strip()
                        # No config values, gcode scripts, secrets, passwords or IPs.
                        if (sec.lower().startswith(('auto_xy','autoxy','gcode_macro auto_','gcode_macro autoxy'))
                                or 'vaoc' in sec.lower()):
                            result['sections'].append({'file':rel, 'section':sec})
                            if sec.lower() == 'auto_xy_local_recovery':
                                result['active_legacy_trial04'] = True
        except OSError:
            result['warnings'].append('error reading active include: '+rel)
    result['active_files'] = sorted(set(result['active_files']))
    return result


def safe_flag(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ('1','1.0','true')


def filtered_runtime(result):
    """Protect saved printer offsets, temperatures and config contents."""
    status = result.get('status') if isinstance(result, dict) else None
    if not isinstance(status, dict):
        return {'connected': False, 'error': 'unexpected_status_schema'}
    sd = status.get('virtual_sdcard') or {}
    ps = status.get('print_stats') or {}
    vaoc = status.get('gcode_macro _VAOC') or {}
    handoff = status.get('auto_xy_ratos_handoff_rc16') or {}
    saved = (status.get('save_variables') or {}).get('variables') or {}
    loaded = (status.get('configfile') or {}).get('settings') or {}
    return {
        'connected': True,
        'print_state': ps.get('state'),
        'sd_active': sd.get('is_active'),
        'sd_file_selected': bool(sd.get('file_path') or sd.get('filename')),
        'native_vaoc_active': vaoc.get('is_started'),
        'rc16_handoff': {k:handoff.get(k) for k in ('enabled','armed','busy','sd_api_guarded',
                            'resume_api_guarded','retention_enabled')},
        'saved_flags': {k:safe_flag(saved[k]) if k in saved else None for k in
                    ('auto_xy_v2_enabled','auto_xy_v2_fault','auto_xy_v2_in_progress',
                     'auto_xy_v2_retry_available','auto_xy_v2_measure_only')},
        'loaded_auto_xy_sections': sorted(str(k) for k in loaded
                                          if str(k).lower().startswith(('auto_xy','autoxy'))),
    }


def moonraker_readonly():
    try:
        with urlopen(STATUS_QUERY, timeout=5) as response:
            payload = json.load(response)
        if 'error' in payload:
            return {'connected': False, 'error': 'Moonraker returned query error'}
        return filtered_runtime(payload.get('result') or {})
    except (OSError, HTTPError, URLError, ValueError, TimeoutError):
        return {'connected': False, 'error': 'Moonraker unavailable; no state assumed'}


def source_manifest(repo_root):
    path = repo_root/'docs/RC16_SOURCE_MANIFEST.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        files = data.get('source_files')
        if (data.get('hash_algorithm') == 'git-blob-sha1'
                and isinstance(files, dict)):
            return files
    except (OSError, ValueError, TypeError):
        pass
    return {}


def extension_inventory(home, root):
    extras = home/'klipper/klippy/extras'
    results = []
    if not extras.is_dir():
        return results
    # All local extensions: a hidden third-party name can intercept startup.
    for path in sorted(extras.glob('*.py'))[:MAX_SOURCES]:
        row = describe_file(path, root)
        row['hook_candidates'] = code_hits(path)
        results.append(row)
    return results


def system_hooks(home):
    """Read only names/line numbers of related cron/systemd startup files."""
    candidates = [
        Path('/etc/rc.local'), Path('/etc/crontab'),
        home/'.config/systemd/user', Path('/etc/systemd/system'),
        Path('/etc/cron.d'),
    ]
    needles = re.compile(r'auto.?xy|auto_xy_local_recovery|ratos-vaoc|rc16',re.I)
    hits = []
    for root in candidates:
        if root.is_file():
            paths = [root]
        elif root.is_dir():
            paths = [p for p in sorted(root.iterdir())[:300] if p.is_file()]
        else:
            continue
        for path in paths:
            try:
                if path.stat().st_size > MAX_FILE:
                    continue
                with path.open(encoding='utf-8', errors='replace') as f:
                    found = [i for i,line in enumerate(f,1) if needles.search(line)]
                if found:
                    hits.append({'path':str(path), 'matching_line_numbers': found[:20]})
            except OSError:
                continue
    return hits


def audited_modules(home):
    """Source-code inventory, not generated data/diagnostics."""
    root=home/'auto_xy'
    return [dict(describe_file(p,home),hook_candidates=code_hits(p))
            for p in safe_source_paths(root)]


def collect(home, repo_root, *, runtime=None, scan_system=False):
    home, repo_root = Path(home), Path(repo_root)
    manifest = source_manifest(repo_root)
    sections = config_includes(home)
    extensions = extension_inventory(home,home)
    installed = audited_modules(home)
    comparisons = []
    for target, source in EXPECTED_PATHS.items():
        path = home/target
        expected = manifest.get(source)
        measured = describe_file(path,home)
        observed = measured.get('git_blob')
        comparisons.append({
            'installed_path':target,'github_source':source,
            'expected_git_blob':expected,'observed_git_blob':observed,
            'result': ('UNAVAILABLE' if observed is None else
                       'NO_MANIFEST' if expected is None else
                       'MATCH_CURRENT_SOURCE' if observed == expected else
                       'DIFFERS_OR_OLDER'),
        })
    legacy = next((x for x in extensions if x['path'].endswith('/auto_xy_local_recovery.py')),None)
    legacy_source = home/'auto_xy/klipper/auto_xy_local_recovery.py'
    legacy_source_sha = None
    if legacy_source.is_file():
        legacy_source_sha = describe_file(legacy_source,home).get('sha256')
    runtime = runtime if runtime is not None else moonraker_readonly()
    loaded = set(runtime.get('loaded_auto_xy_sections') or [])
    legacy_on = (sections['active_legacy_trial04'] or 'auto_xy_local_recovery' in loaded)
    rc16_on = ('auto_xy_ratos_handoff_rc16' in loaded or
               runtime.get('rc16_handoff',{}).get('armed') is True)
    indicators = []
    if legacy_on:
        indicators.append('ACTIVE_LEGACY_TRIAL04_REVIEW_REQUIRED')
    if legacy_on and rc16_on:
        indicators.append('COMPETING_TRIAL04_AND_RC16_PRINT_HANDOFFS')
    if legacy_source_sha == TRIAL04_SHA256:
        indicators.append('KNOWN_TRIAL04_SOURCE_PRESENT')
    if any(x['result']=='DIFFERS_OR_OLDER' for x in comparisons):
        indicators.append('INSTALLED_SOURCE_DIFFERS_FROM_CURRENT_MAIN')
    if not runtime.get('connected'):
        indicators.append('RUNTIME_STATUS_UNAVAILABLE')
    known = {'auto_xy_ratos_handoff_rc16.py','auto_xy_local_recovery.py',
             'auto_xy_gate.py','ratos.py'}
    alternative = [x['path'] for x in extensions
                   if x.get('hook_candidates',{}).get('PROCESS_GCODE_FILE')
                   and Path(x['path']).name not in known]
    if alternative:
        indicators.append('OTHER_PRINT_PROCESSOR_HOOK_CANDIDATES')
    report = {
        'schema_version':1,
        'report_type':'READ_ONLY_AUTOXY_CODE_AND_HOOK_AUDIT',
        'limitations': [
            'Static hook candidates do not prove which command handler currently owns a runtime hook',
            'Source SHA mismatch may mean an older supported version, not necessarily intrusive code',
            'No files/configurations/extensions were modified or disabled',
            'No G-code files, printer offsets, credentials, raw source/config text, camera frames or reference JSON collected',
        ],
        'runtime':runtime,'active_config':sections,
        'installed_source_vs_repo':comparisons,
        'klipper_extras':extensions,
        'auto_xy_source_files':installed,
        'legacy_trial04':{
            'source_present':legacy_source.is_file(),
            'known_reviewed_sha256':legacy_source_sha == TRIAL04_SHA256,
            'extension_present': legacy is not None,
            'extension_symlink': legacy is not None and legacy['kind']=='symlink',
            'active_in_configuration':legacy_on,
        },
        'system_startup_candidates':system_hooks(home) if scan_system else [],
        'review_indicators':sorted(set(indicators)),
        'other_processor_hook_paths':alternative,
    }
    return report


def text_summary(report):
    lines = [
        'AutoXY read-only printer integrity inventory',
        'No printer modification performed. Upload ZIP privately for technical review.',
        '',
        'Runtime: '+('CONNECTED' if report['runtime'].get('connected') else 'UNAVAILABLE'),
        'Trial04 active: '+str(report['legacy_trial04']['active_in_configuration']),
        'Known Trial04 source present: '+str(report['legacy_trial04']['known_reviewed_sha256']),
        'Runtime RC16 handoff: '+str(report['runtime'].get('rc16_handoff')),
        'Source comparisons:',
    ]
    for item in report['installed_source_vs_repo']:
        lines.append('  '+item['result']+': '+item['installed_path'])
    lines += ['', 'Review indicators:']
    lines += ['  '+x for x in report['review_indicators']] or ['  none']
    lines += ['', 'IMPORTANT: Do NOT delete/quarantine anything just from these flags.']
    return '\n'.join(lines)+'\n'


def write_zip(report, output):
    output=Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError('Refusing to overwrite existing inventory ZIP')
    # Only write the requested report archive outside printer data directories.
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output, 'x',zipfile.ZIP_DEFLATED) as z:
        z.writestr('inventory.json', json.dumps(report,indent=2,sort_keys=True))
        z.writestr('SUMMARY.txt',text_summary(report))
    os.chmod(output,0o600)
    return output


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',default=str(HOME), help='read-only printer home, default /home/pi')
    parser.add_argument('--output',default='',help='new report ZIP path, never overwritten')
    args=parser.parse_args(argv)
    repo_root=Path(__file__).resolve().parent.parent
    home=Path(args.home)
    stamp=dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    output = Path(args.output) if args.output else home/('AutoXY_Printer_Integrity_Audit_'+stamp+'.zip')
    try:
        report=collect(home,repo_root,scan_system=True)
        archive=write_zip(report,output)
    except (OSError, ValueError, RuntimeError) as exc:
        print('READ-ONLY AUDIT REFUSED:',type(exc).__name__,str(exc),file=sys.stderr)
        return 2
    print(text_summary(report))
    print('Archive:',archive)
    print('Bytes:',archive.stat().st_size)
    print('Printer configuration, source files, offsets and other state: UNCHANGED')
    return 0


if __name__=='__main__':
    raise SystemExit(main())