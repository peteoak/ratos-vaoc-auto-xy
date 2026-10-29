# New installation (experimental; first install must be supervised)

1. Require RatOS 2.1 with fixed native VAOC USB camera at `/dev/RatOS/rr-vaoc-camera` exposed through crowsnest camera-streamer port 8080. Native `_VAOC` and RatOS macros must exist. Run native VAOC successfully and ensure saved `idex_[xyz]offset` variables exist before AutoXY commissioning.
2. **Backup the whole RatOS configuration first**. If any AutoXY files or include exist, this is **not a fresh install**; do not use the fresh installer or remove arbitrary existing files. For preexisting beta installations follow a reviewed migration with a rollback, not an overlay.
3. On Pi, install Python vision environment without modifying Klipper environment:

```bash
python3 -m venv /home/pi/auto-xy-venv
/home/pi/auto-xy-venv/bin/python -m pip install -r requirements-vision.txt
/home/pi/auto-xy-venv/bin/python -c 'import cv2,numpy; print(cv2.__version__,numpy.__version__)'
```

4. With the printer **cool (<50°C), heaters OFF, motion idle, RatOS VAOC inactive, Klipper READY, no active print/fault/retry**, use `python3 scripts/install_autoxy.py --check --mode fresh`. If checked, execute `python3 scripts/install_autoxy.py --install --mode fresh --confirm INSTALL_RC15_DEV5_FRESH`. Save its rollback folder path. DO NOT force through an unexpected collision.
5. Restart Klipper service while idle and rehome. The installed example is OFF and UNCOMMISSIONED; it is deliberately not operational until properly configured. Use `scripts/commission_camera.py --help`, follow camera/photo commissioning, then verify `AUTO_XY_HEALTH`, `AUTO_XY_STATUS`, supervised measurement and native VAOC recovery on your own printer. Only after qualification, configure verified camera references and activate two-stage ROI/automatic print flow.
6. The installer registers only AutoXY with RatOS, stages only the managed files, preserves saved offsets and creates an exact rollback directory. `python3 scripts/install_autoxy.py --rollback /home/pi/auto_xy/rollbacks/<actual-folder>` may be run *only* after verifying printer idle/cool, no edits to managed files, and that the folder is the exact printed path.

## Explicit limits

- Do not publish this as one-click universally compatible with all RatOS or nonstandard VAOC variants. The conservative installer requires the stock camera alias and unmodified macro surface, and refuses unknown ones.
- Pre-commissioning/fresh installation on other machines remains a needed community validation exercise.
- The configuration contains strategy **values** (`ratos`,`slicer_derived`) but no graphical dropdown. Fixed-temperature mode is a tracked enhancement, not current functionality.
