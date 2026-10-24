# How to publish this beta on YOUR GitHub

The maintainer should review source, GPL-3.0 license choice, documentation and safety statement before publication. This is **not** currently uploaded to your GitHub by virtue of having this ZIP.

## Suggested repository settings

- **Name:** `ratos-vaoc-auto-xy`
- **Description:** `Experimental camera-based automatic IDEX XY nozzle alignment for RatOS 2.1 (community beta; use at your own risk)`
- **Topics:** `ratos`, `ratrig`, `klipper`, `idex`, `vaoc`, `3d-printing`, `calibration`
- **Visibility:** Public, after auditing all files for secrets/private data.
- Enable **Issues** (the `Test report` and `Bug report` forms are included).
- Optionally enable **Discussions** for support questions.
- Use **pre-release** tags until compatibility and fault recovery are sufficiently tested.

## Windows-friendly procedure

1. Extract the **GitHub repository ZIP** into a directory, e.g. `ratos-vaoc-auto-xy` (not a folder of previous test run JPEGs).
2. Inspect `README.md`, `docs/SAFETY.md`, `LICENSE`, `auto_xy_print.cfg`, and remove any data you don't intend to publish.
3. Install [Git for Windows](https://git-scm.com/download/win) if needed and open Git Bash in the extracted folder.
4. Create an empty GitHub repository with the suggested name; **do not initialize it with README, license or `.gitignore`**, because this package already contains them.
5. Use these commands (replace `YOUR_GITHUB_USERNAME`):

```bash
git init -b main
git add .
git commit -m "Initial community beta: RatOS VAOC Auto XY Stage 6.3.7"
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/ratos-vaoc-auto-xy.git
git push -u origin main
```

Alternatively, with authenticated GitHub CLI:

```bash
gh repo create ratos-vaoc-auto-xy --public --source=. --remote=origin --push
```

If Git requests identity configuration, set your preferred `user.name` and `user.email` before committing. Never include real credentials in a Git remote URL you plan to share publicly.

## Release candidate

Use GitHub **Releases → Draft a new release**, choose tag `v0.1.0-beta.3`, mark it **Pre-release**, and use a title such as **RatOS Auto XY community beta — Stage 6.3.7**. The attached repository ZIP can act as a source snapshot, but the canonical install instructions should use the versioned GitHub repo/tag so testers all start from the same code. Prefer immutable release tags once testers begin reporting regressions.

Suggested release-note summary:

> Experimental RatOS 2.1 IDEX camera XY calibration. Two-tool independent printing only; T0/T1 single-tool and COPY/MIRROR modes bypass. Pre-Beacon startup timing, Mainsail toggle, bounded corrections, two-check verification, and one inline recoverable retry. Camera detector and installer currently assume one tested Pi/VAOC layout; **not verified broadly**. Back up, read the safety guide, and supervise first use. Community testing requested through Issues.

## Before accepting other testers

- Verify correct operation of GitHub Actions offline suite on the default branch.
- Check that source tree contains no `.env`, credentials, printer configuration backups, saved variables, private logs, camera captures or personal IPs.
- Review licensing. `LICENSE` in this package is a **proposed GPL-3.0** choice; change it *before* public distribution if you intend a different compatible license.
- Create labels such as `bug`, `hardware-test`, `documentation`, `help-wanted` as you need (the issue forms do not require predefined labels).
- Pin a note about the current tested hardware and the camera-geometry limitation.
- Encourage testers to submit small PRs with before/after offline tests, not unexplained changes to temperature or travel guards.

For project governance, see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Updating an existing beta.2 repository on `peteoak`'s GitHub

From the existing local repository clone on Windows, apply the versioned beta.3 patch (if provided), inspect the changes, commit and push to `main`. **Do not reset or force-push the remote branch** just to publish an update.

```bash
git status
git apply --check ~/Downloads/AutoXY_beta3_retention_clean_install.patch
git apply ~/Downloads/AutoXY_beta3_retention_clean_install.patch
git add -A
git commit -m "Add safe history retention and clean reinstall utilities (beta.3)"
git push origin main
```

If `git apply --check` fails, do not apply it; the repository differs from the reference beta.2 snapshot. Reconcile manually before proceeding.

Create a **new pre-release tag** `v0.1.0-beta.3`, attach the beta.3 tester-installer ZIP, and label beta.1 as unsafe/superseded due to its historical SAVE_CONFIG issue. Do not move or replace an already-published release tag. Keep beta.2 as historical source, with a note that beta.3 adds retention and the clean-reinstall test flow.


## Beta 4.1 owner validation before another public release

Beta 4.1 is a *hardware-test candidate*, not a public-release recommendation.
Run `python3 test_response_gain_recheck.py` and the existing offline suite,
then installer `--check` before any installation. Verify no fault and no
active VAOC state, retain configuration backups, and supervise a single
manual calibration first. Do not push a new GitHub release/tag until the
owner verifies the new gain-recheck path on the physical machine.
Never overwrite or force-push existing beta tags.


## Beta 4.2 release-candidate validation (current)

This candidate is derived from the owner's beta.3 GitHub baseline. **Do not tag it or open a public community pre-release until the maintainer has checked the independent print-start report and is satisfied with the available bypass-mode evidence.** Include untested modes honestly in the release notes; do not fabricate a passing test by forcing an unsafe mode. Anomalous-gain remeasurement is only offline-validated.

The Klipper Python extra requires a full `sudo systemctl restart klipper` after install on an **idle, safely recovered printer**, not merely a G-code configuration restart. Review the [owner validation evidence](TESTING.md#beta-42-owner-validation-snapshot-25-october-2026-nz-time).

For an existing beta.3 checkout, copy the candidate source files over the checkout (preserve the hidden `.git` directory), or use the beta.3→beta.4.2 patch after `git apply --check`. **Never force-push or rewrite previous beta release tags.** Once validation is sufficient, create a new pre-release tag `v0.1.0-beta.4.2` and attach the matching installer ZIP.
