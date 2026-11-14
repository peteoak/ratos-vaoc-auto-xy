# Document map: current RC16 vs historical engineering records

**Current operator-facing RC16 documents** (maintained for the `main` branch):

- [Repository landing page](../README.md)
- [RC16 start-here guide](RC16_START_HERE.md)
- [Current release and qualification status](RC16_RELEASE_STATUS.md)
- [Diagnostics / reporting](RC16_DIAGNOSTICS.md)
- [Recommission Nozzles maintenance](RC16_RECOMMISSION_MAINTENANCE.md)
- [Supervised qualification checklist](RC16_SUPERVISED_QUALIFICATION.md)
- [RC16 code source inventory](RC16_SOURCE_MANIFEST.json)

**Historical RC15 package — intentionally preserved, not current RC16 installation advice:**

- [Frozen, intact RC15 Dev6 branch](https://github.com/peteoak/ratos-vaoc-auto-xy/tree/release/rc15-dev6-frozen). Use this branch for the *original*, mutually consistent RC15 files including `README.md`, `MANIFEST.json` and the RC15 installer.
- [Archived RC15 README](legacy/RC15_README.md) and [archived original manifest](legacy/RC15_MANIFEST.json) copied byte-for-byte for traceability. The archived historical manifest records hashes at the original **root paths** on the frozen branch, not within `docs/legacy`.
- `docs/NEW_INSTALL.md`, `docs/RELEASE_NOTES.md`, `docs/GITHUB_RELEASE.md`, and `RELEASE_STATUS.md` remain original RC15 documents to preserve baseline verification. **Do not follow them as RC16 quick-start instructions.**

**Historical RC16 engineering artifacts — retained for requirements traceability, not current runbooks:**

| Document | Historical focus |
| --- | --- |
| [RC16 Dev1 Qualification](RC16_DEV1_QUALIFICATION.md) | Initial staging, offline-only preflight and early acceptance criteria, before main promotion |
| [RC16 Resource-Aware Integration Plan](RC16_RESOURCE_AWARE_INTEGRATION_PLAN.md) | Architecture proposals and early print-start interception concerns |
| [RC16 Vision Dependency Trial](RC16_VISION_DEPENDENCY_TRIAL.md) | Initial NumPy/OpenCV ABI test; its claim that RC16 is not on main is obsolete |
| [RC16 Trial04 Source Audit](RC16_TRIAL04_SOURCE_AUDIT.md) | Private Trial04 problems that motivated integrated RC16 |
| [RC16 Implementation Audit](RC16_IMPLEMENTATION_AUDIT.md) | Requirements-to-source mapping as of 11 November |
| [RC16 First Complete Trial](RC16_FIRST_COMPLETE_TRIAL.md) | Dated initial installation/recovery trial and older vision revision; superseded photo-check wording |
| [RC16 Pause/Cancel Integration Design](RC16_PAUSE_CANCEL_INTEGRATION_DESIGN.md) | Optional, **not implemented** asynchronous architecture |

**Rule:** When documents disagree, use the current code, [release status](RC16_RELEASE_STATUS.md), current operator guides and an actual verified printer-specific installation. Historical language saying `main` is still RC15, that the install is not implemented, or that photo likeness can veto a unique geometric circle must **not** be treated as current behaviour.

RC16 being on `main` does **not** mean the release has been hardware-qualified for unattended printing or for every beta-tester nozzle image.
