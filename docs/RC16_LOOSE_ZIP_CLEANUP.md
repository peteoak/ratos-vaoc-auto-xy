# RC16: review-first cleanup of obsolete loose package ZIPs

**Scope:** Historical download packages only, not installed AutoXY, registered Klipper extensions, RatOS config, G-code, camera references, diagnostic evidence, rollbacks or original historical backups. The requested goal is to reduce accumulated source/distribution clutter without reopening the previously retired Trial04 print-interceptor conflict. **This is not a security audit or a guarantee that the print pipeline is qualified.**

## Evidence and preservation constraints

On 28 November 2026, the operator's printer audit showed 10/10 installed RC16 sources matched reviewed GitHub, both SD and resume guards armed, and Trial04 unloaded. The owner subsequently confirmed successful retirement of the dormant Trial04 import symlink and separately confirmed deletion of **three exactly duplicate** `config-*.zip` files from the pre-uninstall backup after on-Pi SHA256 comparison; their matching ZIPs under `/home/pi/printer_data/config/` were retained. **No off-Pi copies are confirmed.**

Remaining large historical backup folders and original config ZIPs must remain untouched pending separately verified external backup. In particular, do **not** run a general `find /home/pi -name '*.zip' -delete`, or assume an old diagnostic ZIP is dispensable. Many diagnostically useful raw images and commissioning references are printer-specific, even when older code is reproducible from GitHub.

## Reviewed eligible names

The only packages eligible for this script are these exact seven files, **directly beneath `/home/pi`**:

- `AutoXY_RC15_Dev2_Diagnostic_Logging.zip`
- `AutoXY_RC15_Dev3_Endurance_Diagnostics.zip`
- `AutoXY_RC15_Dev4_Two_Stage_Detector.zip`
- `AutoXY_RC15_Dev5_Cropped_Detector.zip`
- `AutoXY_RC15_Dev5_100_Change_Results.zip`
- `AutoXY_RC15_Persistent_Camera_Integration.zip`
- `AutoXY_RC15_Camera_Optimization_Lab.zip`

These are old development *packages* and small (under 2 MB each). This is an **optional** cleanup; it is not necessary for RC16 operation, and they may hold historical work not fully represented in the current repository. If that history matters, copy the seven ZIPs to your PC or separate drive first. The tool cannot reverse an archive deletion from its receipt.

Not eligible: **all** `config-*.zip` backups, any archive under `/home/pi/printer_data/config/`, ZIPs containing diagnostic reports/camera data or nozzle reference backups, Trial04 forensic ZIPs, both `node_modules` test ZIPs, `printer_data/gcodes`, `auto_xy` source/data, historical directories, and *any new filename*. Large backups are a separate archive-before-delete project.

## Supervised commands on Pi (download the reviewed exact commit)

Download from an explicitly pinned GitHub **merge SHA** supplied after CI/merge. Do not download a moving branch for a destructive operation:

```bash
mkdir -p /home/pi/AutoXY_ZipCleanup
curl -fL --retry 3 \
  "https://api.github.com/repos/peteoak/ratos-vaoc-auto-xy/tarball/<REVIEWED_MERGE_SHA>" \
  -o /tmp/AutoXY_ZipCleanup.tar.gz
tar -xzf /tmp/AutoXY_ZipCleanup.tar.gz -C /home/pi/AutoXY_ZipCleanup --strip-components=1
python3 -B /home/pi/AutoXY_ZipCleanup/scripts/rc16_loose_zip_cleanup.py check
```

The `check` command validates ZIP integrity (with decompression limits), direct parent directory, single-link normal files and a fixed maximum size, then creates a small, owner-local hash/inode/mtime-bound JSON **plan**. It does not modify existing packages, config, or installed code. Its output will show only candidates present, their sizes and the plan path. If the plan includes anything unexpected, do not continue.

To optionally remove only those packages, supply the **exact plan path printed by `check`**:

```bash
python3 -B /home/pi/AutoXY_ZipCleanup/scripts/rc16_loose_zip_cleanup.py remove \
  --plan /home/pi/AutoXY_Zip_Package_Plan_<exact-suffix>.json
```

The script checks *every* pre-approved file again against its prior SHA256, size, inode, device and modification time, refusing if the inventory changed, and requires typing `DELETE OLD PACKAGES`. It writes a metadata-only receipt under `/home/pi/auto_xy/rollbacks/` **before** unlinking. On success the packages are gone and the receipt cannot restore them. Do not run while another maintenance session modifies these files. No sudo, restart or motion commands are used.

## Next phase (separate from this script)

The larger historical directories `autoxy-pre-uninstall-20261102-040312`, `autoxy-factory-backups`, and `autoxy-quarantine-rc15-dev5-20261102` still require off-Pi archives and verification before deletion. The former already had three ZIP duplicates deleted in a previous confirmed step, so its previous size is now stale. Verify current disk usage and backup uniqueness before promising a volume of reclaimed storage. A verified off-device archive needs to retain original symlink metadata, file ownership where important, config revisions, camera commissioning and rollback data. Treat all three retained original `config-*.zip` files as protected until their external checksums are confirmed.