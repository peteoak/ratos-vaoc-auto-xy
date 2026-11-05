# AutoXY RC15 dev6 — fresh installation guide

**For first-time users of RatOS 2.1 IDEX.** This guide covers the **public AutoXY v0.5.0-rc15-dev6 development release**, not experimental private integrations. AutoXY has been validated on one RatRig V-Core 4 300 IDEX; other printers and modified RatOS installations still need supervised validation.

**Start here:** If you have **ever installed AutoXY** on this printer, **STOP**. This is a *fresh-only* installer, not an upgrade or an uninstall tool. Do not delete old files just to pass its checks. Arrange a reviewed migration with a rollback plan.

## Three milestones

1. **Installed:** Klipper loads AutoXY, but automatic calibration is **OFF**.
2. **Commissioned:** You have completed native RatOS manual VAOC and created a camera profile from **your own** nozzle photographs.
3. **Enabled:** You have passed a supervised *measure-only* check and separately authorised normal calibration.

Completing milestone 1 does **not** mean AutoXY is ready to move the tools or write offsets. No printer-independent camera profile or offset values are supplied.

## Before you begin

You will need:

- A working **RatOS 2.1 IDEX** printer with Klipper and Mainsail.
- Working **native RatOS manual VAOC**. Complete and verify a normal manual VAOC calibration **before installing** so the saved `idex_xoffset`, `idex_yoffset` and `idex_zoffset` values exist.
- The standard fixed VAOC USB camera at `/dev/RatOS/rr-vaoc-camera`, configured in crowsnest as **camera-streamer on port 8080**. Nonstandard setups may be refused by the installer.
- SSH access to the printer's Pi and enough free storage for a backup, Python dependencies and calibration images.
- A **cool, idle printer**: Klipper Ready; no print, pause, recovery or active VAOC; **heater targets at 0**; both hotends and bed **below 50°C**; no motion.
- Your own full printer backup and a safe way to restore it.

The commands below assume the Pi user is `pi` and its home directory is `/home/pi`, as required by this installer. If your setup differs, **do not blindly change paths**; this release's live installer is not designed for arbitrary home directories.

## Step 1 — Connect to the Pi

From Windows PowerShell / Windows Terminal, macOS Terminal or Linux Terminal:

~~~bash
ssh pi@ratos.local
~~~

If `ratos.local` does not resolve, use your printer's IP address instead, for example `ssh pi@192.168.1.100` (replace with **your actual IP**). Enter your existing SSH credentials.

You should end up at a shell prompt like `pi@RatOS:~ $`.

**Shell commands belong in SSH, not in Mainsail's G-code Console.** Later, G-code commands marked *Mainsail Console* go in Mainsail.

## Step 2 — Back up before installing

While connected by SSH, create a timestamped **minimum configuration backup**:

~~~bash
tar -czf "$HOME/ratos-config-before-autoxy-$(date +%Y%m%d-%H%M%S).tar.gz" -C "$HOME" printer_data/config
~~~

Keep an additional, verified full-printer backup **off the Pi**. The example command above archives `printer_data/config`; it is **not** a full system image. Your printer-specific commissioning photos, camera settings and stored offsets must also be recoverable.

Do not publish backups, host logs, saved variables or camera images without checking for private information.

## Step 3 — Download the public source

In SSH:

~~~bash
cd /home/pi
git clone https://github.com/peteoak/ratos-vaoc-auto-xy.git
cd /home/pi/ratos-vaoc-auto-xy
~~~

If `git clone` reports that the directory already exists, **stop and inspect it**. Do not run the fresh installer from an unknown old checkout or overwrite files.

The commands below must be run from the downloaded `/home/pi/ratos-vaoc-auto-xy` directory unless a full path is shown.

## Step 4 — Set up the separate camera-processing Python environment

Do not install camera dependencies into Klipper's own Python environment.

~~~bash
python3 -m venv /home/pi/auto-xy-venv
/home/pi/auto-xy-venv/bin/python -m pip install -r requirements-vision.txt
/home/pi/auto-xy-venv/bin/python -c 'import cv2,numpy; print(cv2.__version__,numpy.__version__)'
~~~

**Expected:** the last command prints an OpenCV version and a NumPy version without an error. If `venv` or dependencies fail, fix that problem before proceeding.

## Step 5 — Verify the stock VAOC camera

With native VAOC working, run this **read-only** check in SSH:

~~~bash
python3 scripts/verify_stock_camera.py
~~~

**Expected:** `STOCK RATOS VAOC CAMERA PASSED (read only)`.

The script checks the stock device alias, crowsnest configuration and local image snapshot at `http://127.0.0.1:8080/?action=snapshot`. Passing proves camera access, **not** nozzle recognition or optical commissioning.

If this fails, correct your native RatOS camera setup first. Do not fake the camera alias, skip the check or force installation on an unverified configuration.

## Step 6 — Run installer preflight (no changes)

Confirm the printer is **Ready, below 50°C, with all heater targets off**, no active print, motion or VAOC, and **no existing AutoXY**.

Run:

~~~bash
python3 scripts/install_autoxy.py --check --mode fresh
~~~

**Expected ending:**

~~~text
AutoXY 0.5.0-rc15-dev6 preflight PASS; mode: fresh
...
CHECK PASSED; NO FILES CHANGED
~~~

The installer verifies the package checksums, required RatOS macros, saved numeric offsets, compatible camera, Python environment and absence of AutoXY files, registrations and conflicting macros. It will **refuse** an existing installation or uncertain conditions.

**If you see `AutoXY installer REFUSED`:** Stop. Read the exact message; do not remove RatOS macros, delete calibration files, edit saved offsets or bypass guards to make it pass. Include the message in a support report.

## Step 7 — Install and restart Klipper

**Only after Step 6 passes**, and while still idle and cool:

~~~bash
python3 scripts/install_autoxy.py --install --mode fresh --confirm INSTALL_RC15_DEV6_FRESH
~~~

Use the exact `INSTALL_RC15_DEV6_FRESH` token shown above; older instructions referring to `DEV5` are outdated.

**Expected:** the installer prints the installed version and a unique rollback path beneath `/home/pi/auto_xy/rollbacks/`. **Copy that exact rollback path somewhere safe.** The installer registers the AutoXY extension, installs its managed files and adds a single `[include auto_xy.cfg]` to `printer.cfg`, above Klipper's saved configuration block when present. It does **not** replace saved RatOS nozzle offsets.

Now restart the Klipper **service** from SSH:

~~~bash
sudo systemctl restart klipper
~~~

In Mainsail, confirm Klipper returns to **Ready**. If Klipper is halted or shows a configuration error, **do not print**. Collect the error and follow the rollback/support guidance below.

## Step 8 — Verify installation (automatic calibration remains OFF)

From SSH:

~~~bash
cd /home/pi/ratos-vaoc-auto-xy
python3 scripts/post_install_check.py
~~~

**Expected final line:** `VALIDATION PASS`.

Then in **Mainsail → Console**, run these **one at a time**:

~~~gcode
AUTO_XY_HEALTH
AUTO_XY_STATUS
~~~

The fresh installation deliberately begins **OFF and uncommissioned**. Do **not** enable it just because Klipper says Ready.

## Step 9 — Commission the nozzle camera using your printer

This is required for each printer. The supplied `scripts/commission_camera.py` **does not move the printer or capture images for you**; it reads the photos you supply.

**9A. Complete native manual VAOC again in the commissioning session.** Home your printer according to RatOS and complete a successful *native RatOS manual VAOC*, checking both nozzles' alignment. For the ensuing camera captures, preserve that valid homed/aligned reference: do not disable motors, restart Klipper, rehome or start a print between manual VAOC and commissioning.

**9B. Capture at least three independent images of T0 and three of T1.** Use the native RatOS VAOC controls to position each nozzle over the camera. Confirm the *correct nozzle* and a clear, centred rim are visible **before each group**. Do not use arbitrary `G1` moves, unverified coordinates or manual carriage movement to position the nozzles.

Prepare a photo directory in SSH:

~~~bash
mkdir -p /home/pi/autoxy-commission-images
~~~

With **T0** correctly in view, run these three SSH commands **separately**, allowing fresh frames between captures:

~~~bash
curl -fsS 'http://127.0.0.1:8080/?action=snapshot' -o /home/pi/autoxy-commission-images/t0_1.jpg
curl -fsS 'http://127.0.0.1:8080/?action=snapshot' -o /home/pi/autoxy-commission-images/t0_2.jpg
curl -fsS 'http://127.0.0.1:8080/?action=snapshot' -o /home/pi/autoxy-commission-images/t0_3.jpg
~~~

Now use the **native VAOC controls** to bring **T1** into the commissioned camera pose. Verify the image, then capture:

~~~bash
curl -fsS 'http://127.0.0.1:8080/?action=snapshot' -o /home/pi/autoxy-commission-images/t1_1.jpg
curl -fsS 'http://127.0.0.1:8080/?action=snapshot' -o /home/pi/autoxy-commission-images/t1_2.jpg
curl -fsS 'http://127.0.0.1:8080/?action=snapshot' -o /home/pi/autoxy-commission-images/t1_3.jpg
~~~

Inspect the six images. They must each show the right nozzle clearly at a **stable camera position**. Reusing or copying a single photo does **not** count as independent captures.

**9C. Generate the commissioning profile.** Only if you really completed and visually verified native manual VAOC, run from SSH:

~~~bash
cd /home/pi/ratos-vaoc-auto-xy
/home/pi/auto-xy-venv/bin/python scripts/commission_camera.py \
  --t0 /home/pi/autoxy-commission-images/t0_1.jpg /home/pi/autoxy-commission-images/t0_2.jpg /home/pi/autoxy-commission-images/t0_3.jpg \
  --t1 /home/pi/autoxy-commission-images/t1_1.jpg /home/pi/autoxy-commission-images/t1_2.jpg /home/pi/autoxy-commission-images/t1_3.jpg \
  --confirm-native-manual-vaoc
~~~

**Expected:** `Photo-only commissioning PROFILE CREATED: /home/pi/auto_xy/camera_commission.json`.

The script rejects duplicate frames, unreasonable drift or missing images and will not overwrite an existing profile. If it fails, review positioning, lighting, focus, background reflections and images instead of loosening safety checks.

Commissioning records **your** manual VAOC confirmation; the script cannot independently prove nozzle alignment. Keep the commissioning profile and source photos backed up, outside managed results cleanup.

## Step 10 — First supervised measure-only calibration

After commissioning, exit any active native VAOC session normally. Ensure the printer remains in a valid homed state, is idle, and is at the safe preheat conditions required by the installed AutoXY configuration. The camera-temperature limits are printer-specific; do not exceed them.

In **Mainsail → Console**:

~~~gcode
AUTO_XY_MEASURE_ONLY_ON
~~~

Then, while present at the printer:

~~~gcode
AUTO_XY_RUN_NOW
~~~

The first command enables **measurement without saved nozzle-offset writes**. The second runs a supervised standalone measurement; it does **not** start a print. Watch both carriages and the camera process, then review the result or failure report.

**Do not progress if** detection fails, alignment is out of tolerance, measurements are unstable, unexpected motion occurs or a fault is latched. Recover safely using native VAOC and investigate the report. Never use `AUTO_XY_ACKNOWLEDGE` to hide an unresolved fault.

## Step 11 — Deliberately enable normal automatic correction

**Only after** you have independently qualified camera recognition, measurement repeatability and safe correction behaviour on **your** printer, you may use the following in **Mainsail Console**:

~~~gcode
AUTO_XY_ON
AUTO_XY_STATUS
~~~

`AUTO_XY_ON` exits measure-only mode and enables the controller for eligible independent two-tool IDEX print starts. **This is a printer-specific operational decision**: passing a no-write test alone is not proof that automatic offset writes are safe on another machine. Keep the first eligible print supervised.

The stock public RC15 dev6 example uses `camera_detector_mode: two_stage_roi`, a persistent worker, and keeps `protected_print_enabled: False`. **Leave protected printing disabled** for ordinary slicer files. This public release does not include a generally validated transparent protected-print/recovery integration. Do not paste experimental print-start commands into your slicer or enable `protected_print_enabled` as a shortcut.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| `git clone` says directory exists | Inspect the existing checkout; don't overwrite unknown files. |
| Preflight says AutoXY already present | Stop: use a reviewed migration, **not** this fresh installer. |
| Python `venv` or `cv2` import fails | Fix the separate vision environment; don't install into Klipper's Python. |
| Stock VAOC camera check fails | Fix crowsnest, USB alias, stream port or native VAOC first. |
| `AutoXY installer REFUSED` or macro collision | Save the exact error. Don't delete existing user macros to force success. |
| Klipper is halted after install | Do not print. Collect `klippy.log` / Mainsail error and use a reviewed recovery. |
| `VALIDATION FAILED` | Stop: verify extension registration, includes and paths before use. |
| Nozzle not recognised / commissioning fails | Check the actual frames, focus, lighting, reflections, stable pose and ROI. |
| Fault / retry / in-progress state latched | Do not force enable or resume. Verify native VAOC and saved/runtime state. |

## Roll back the fresh installation

**Save the precise rollback path printed by the installer in Step 7.** The following command is a *template*: replace the path with your **actual recorded directory**.

Only attempt normal rollback with Klipper Ready, no print/recovery/VAOC, no motion, all heater targets at 0, and the printer below 50°C. The installer also refuses unexpected edits to its managed files.

~~~bash
cd /home/pi/ratos-vaoc-auto-xy
python3 scripts/install_autoxy.py --rollback /home/pi/auto_xy/rollbacks/YOUR-ACTUAL-ROLLBACK-DIRECTORY
sudo systemctl restart klipper
~~~

If `rollback` refuses, **do not force it**: stop and inspect the exact error. A halted-Klipper or modified-file recovery needs a reviewed machine-specific procedure.

## Asking for help

When opening a GitHub issue, provide your printer model, RatOS version, AutoXY version, **fresh versus existing installation**, the step that failed, the exact error and whether native VAOC works independently. Do not attach full printer backups, unreviewed camera photos, private logs, saved variables or credentials.

This is an independent experimental project, not an official RatOS installer. Community feedback from new installs is welcome; please report both successful preflight checks and unexpected failures.
