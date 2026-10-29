#!/usr/bin/env python3
"""Prepare an exact AutoXY RC15 dev5 publication snapshot in a CLEAN local Git branch.

Refuses main/master and detached HEAD, refuses dirty working trees, never pushes.
Never changes anything without --apply and a specific --confirm token. Git repo
history/tags/remote are preserved; obsolete historical beta files are removed
from the proposed branch, but remain available in Git history.
"""
import argparse
import os
from pathlib import Path
import subprocess
import shutil

PACKAGE=Path(__file__).resolve().parents[1]
BRANCH='autoxy-rc15-dev5-community-review'
TOKEN='STAGE_RC15_DEV5_REVIEW'

def git(root,*args):
    p=subprocess.run(['git','-C',str(root),*args],text=True,capture_output=True,check=False)
    if p.returncode:raise RuntimeError('Git failed: '+' '.join(args)+'\n'+p.stderr[-1000:])
    return p.stdout.strip()

def plan(repo):
    repo=repo.resolve(strict=True)
    assert repo.is_dir()
    top=Path(git(repo,'rev-parse','--show-toplevel')).resolve()
    if top != repo:raise RuntimeError('Pass the Git repository ROOT, not a subdirectory')
    branch=git(repo,'branch','--show-current')
    if branch!=BRANCH:raise RuntimeError('Expected review branch '+BRANCH+'; got '+repr(branch))
    if git(repo,'status','--porcelain'):raise RuntimeError('Git working directory is dirty; refuse to overwrite')
    keep=set()
    for p in PACKAGE.rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts and '.git' not in p.parts:
            keep.add(p.relative_to(PACKAGE).as_posix())
    tracked={Path(p).as_posix() for p in git(repo,'ls-files','-z').split('\x00') if p}
    # Do not remove untracked user data; refuse path collisions instead.
    conflicts=[p for p in sorted(keep) if (repo/p).exists() and p not in tracked]
    if conflicts:raise RuntimeError('Untracked conflicting destination(s): '+str(conflicts[:15]))
    return repo, keep, tracked-keep

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('repo',type=Path,help='clean cloned Git repo ROOT on review branch')
    ap.add_argument('--apply',action='store_true')
    ap.add_argument('--confirm',default='')
    args=ap.parse_args()
    repo,keep,obsolete=plan(args.repo)
    print('REVIEW BRANCH:',BRANCH)
    print('Candidate files:',len(keep))
    print('Tracked old files removed from review branch:',len(obsolete))
    for p in sorted(obsolete):print('  RETIRE',p)
    print('No git push/commit will be performed by this script.')
    if not args.apply:
        print('DRY RUN ONLY. Re-run --apply --confirm '+TOKEN+' to copy and stage.')
        return
    if args.confirm!=TOKEN:raise RuntimeError('Specific confirmation token required')
    # Path containment: only tracked relative paths from git and package paths.
    for rel in obsolete:
        source=repo/rel
        if source.is_symlink() or source.is_file():source.unlink()
        elif source.exists():raise RuntimeError('Unexpected tracked directory: '+str(source))
    for rel in keep:
        src=PACKAGE/rel
        dest=repo/rel
        if src.is_symlink():raise RuntimeError('No package symlinks may be staged')
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,dest)
    git(repo,'add','-A')
    print('STAGED. Review `git diff --cached --stat` and `git diff --cached --check` before committing.')

if __name__=='__main__':
    try:main()
    except Exception as error:
        raise SystemExit('STAGING REFUSED: '+str(error))
