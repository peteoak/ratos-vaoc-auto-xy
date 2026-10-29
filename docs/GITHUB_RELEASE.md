# GitHub review / publication checklist

Target from prior release planning: `https://github.com/peteoak/ratos-vaoc-auto-xy`. **Not verified or written during build.** The existing repository may contain historical `v0.1.0-beta.4.2` files and install instructions. Publish on a **review branch**, not by blindly overlaying obsolete installers or force-pushing `main`.

1. Inspect `THIRD_PARTY_NOTICES.md` and verify licence/provenance for the adapted RatOS macros before a public push. Check `tests/fixtures/*.jpg` for any private machine information; remove if you do not want them public (and update the tests accordingly).
2. Clone the repo using your authenticated GitHub CLI/git environment and create branch `autoxy-rc15-dev5-community-review` off `main`.
3. Back up the repository. The included `scripts/stage_github_review.py` can inspect and stage a replacement of obsolete beta files **only on the exact clean review branch**, preserving `.git/`, earlier tags and history. The tool prints every tracked file it intends to retire, requires a confirmation token to apply and never commits or pushes. Review those removals carefully. Verify `git status`, `git diff --cached --check`, and run Python tests with installed dependencies before committing.
4. Push the **review branch** and open a PR; optionally mark a `v0.5.0-rc15-dev5` *pre-release* after reviewing licence and fresh installer. Do not label as universally installation-tested or stable; the latest hardware result is from one RatRig V-Core 4 IDEX machine.

Example, after copying this package into a **separate clone** (not the printer configuration directory):

```bash
git clone https://github.com/peteoak/ratos-vaoc-auto-xy.git
cd ratos-vaoc-auto-xy
git switch -c autoxy-rc15-dev5-community-review
# Run this script from the EXTRACTED publication ZIP directory, pointing at your clone:
# python3 /path/to/AutoXY_v0.5.0-rc15-dev5_GitHub_Review/scripts/stage_github_review.py .
# Review retired file list. Then (explicit approval):
# python3 /path/to/AutoXY_v0.5.0-rc15-dev5_GitHub_Review/scripts/stage_github_review.py . --apply --confirm STAGE_RC15_DEV5_REVIEW
python3 -m unittest discover -s tests -p 'test_*.py'
git diff --cached --check
git diff --cached --stat
git status --short
# After manual verification:
git add -A
git commit -m 'Publish AutoXY RC15 dev5 consolidated community prerelease'
git push -u origin autoxy-rc15-dev5-community-review
# Open a pull request on GitHub; do not force-push main.
```

Do NOT include `printer.cfg`, `ratos-variables.cfg`, actual commissioned camera data, report.json or host logs in the public commit. Use issue/PR templates to solicit varied printer models and temperature strategy feedback.
