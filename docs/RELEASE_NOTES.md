# AutoXY v0.5.0-rc15-dev6 — retention release candidate

Change since dev5: storage reclamation for successful (7d), failed (30d), diagnostic (7d), export (30d), and report (90d) artifacts; independent caps; terminal-run-only inventory; conservative file identity checks and skip-on-error behavior. A preview-only command is provided. Unknown and in-progress data are preserved.

The previous supervised dev5 25/50/100 tool-change endurance results are **not** dev6 hardware validation. The fresh installer refuses existing AutoXY and must not be used to overwrite a running dev5 setup. Installer manifest, license provenance and tests must be verified before shipping; real printer reinstall remains a supervised qualification step.

Not yet implemented: standalone scripted dev5 uninstall/migration; fixed-absolute-nozzle GUI; guaranteed cross-machine compatibility.
