# RC16 — verified external archive of historical AutoXY backups

**Stage:** Preparation for archival; **no deletion stage is implemented**. 28 November 2026.  
**Printer:** RatOS 2.1, V-Core 4 IDEX.  
**Authoritative script:** [`../scripts/rc16_archive_historical_backups.py`](../scripts/rc16_archive_historical_backups.py).

## Why this exists

The printer owner has completed two **real printer-side** cleanup operations: removal of a dormant Trial04 Klipper import symlink, and deletion of three byte-identical duplicate historical `config-*.zip` copies (total 2,243,046,213 bytes) plus seven old RC15 installer ZIPs (2,104,784 bytes). The original three `config-*.zip` archives still exist under `printer_data/config`; **no verified off-printer backup has been confirmed**.

The 28 November audit identified larger historical folders. Its original combined 4.40 GB size **became stale after the duplicate archives were removed**. Do not use the old total to estimate current free space, backup size or future savings.

This tool creates a **private, independently verified external backup** before any further file retirement. It archives exactly four groups, with no guessing, wildcard deletion or file selection based on age:

| Archive group | Exact Pi source |
| --- | --- |
| `pre-uninstall` | `/home/pi/autoxy-pre-uninstall-20261102-040312/` |
| `factory-backups` | `/home/pi/autoxy-factory-backups/` |
| `rc15-quarantine` | `/home/pi/autoxy-quarantine-rc15-dev5-20261102/` |
| `retained-config-zips` | Three retained `config-20261023-183344.zip`, `config-20261023-182815.zip`, `config-20260916-193926.zip` beneath `/home/pi/printer_data/config/` |

These may contain private user configuration, captured images, recovery and commissioning files. Keep the USB drive and archives **private**. Do not upload the archives or `receipt.json` to a public GitHub repository. Use an encrypted external drive if possible.

## Safety properties

- `plan` is strictly **read-only**. It checks exact source paths, file types, file counts and bytes, without copying, opening private contents, changing the printer or requiring sudo.
- `archive` requires the **exact mountpoint** of a mounted drive on a **different filesystem** from `/home/pi`. It refuses `/home/pi`, `/`, unmounted directories and same-filesystem targets. It creates a uniquely named archive directory; never overwrites an existing archive.
- Each group is preserved in a separate `.tar.gz` with paths, symlinks (without following them), Unix permissions, original owners and timestamps. A conservative free-space reserve is checked before copying; FAT32 users should check their per-file 4 GB limit. Symlinks may point outside the source tree, but the archive stores the *link*, not its target content.
- Source files are SHA256-hashed before and after archive creation. Archive entries are independently read and compared against the private source manifest (including hardlinks and symlinks); each resulting compressed archive receives its own SHA256. Unsupported special files, mount-crossing subtrees, changed source bytes, unreadable files, or unexpected tar members trigger **refusal**. No source files are deleted even on failure.
- Only after **all four groups** verify does the tool write a final `receipt.json` to the external archive directory. No receipt means incomplete and **not** a verified backup. The receipt includes private per-file names/hashes and must remain on the external device.
- `verify` rehashes all four archives, rereads their tar member contents, and compares them to the **still-existing** Pi source tree; it writes nothing. If any source changed since archival, do not interpret old verification as permission to delete it.
- The script cannot delete, quarantine, modify printer configuration, start G-code or restart Klipper. The only newly created files are on the external mountpoint. No root or sudo privileges are required.

**Limitations:** A distinct mounted filesystem is not proof of a distinct physical drive (e.g. two partitions of the Pi's disk). Confirm a **physically separate USB drive or independent mounted network storage** yourself using `lsblk`. A drive that fails later can invalidate a previously good archive; keep another copy for important config and commissioning data. This archive does not certify the printer's physical calibration or authorize unattended prints. No cleanup/deletion may occur until off-device verification and a separate explicit, narrowly scoped review.

## On the Raspberry Pi — preflight, download and read-only plan

Connect a USB drive with at least **6 GB free** (more is better), ensure it is mounted and writable by user `pi`. Identify its **actual mountpoint** (examples only: `/media/pi/AUTOXY_BACKUP` or `/mnt/external`):

```bash
lsblk -o NAME,TRAN,SIZE,FSTYPE,MOUNTPOINTS
findmnt -t vfat,exfat,ext4,ntfs,fuseblk -o SOURCE,TARGET,FSTYPE
```

Do not choose the Pi's root filesystem, `printer_data`, or `/home/pi` as the destination. The archive tool also refuses an unmounted/same-filesystem target.

Download from the **exact reviewed GitHub merge SHA**, which is provided with the release instructions once merged (do not substitute an old commit or moving branch):

```bash
mkdir -p /home/pi/AutoXY_Historical_Archive_Tool
curl -fL --retry 3 \
  "https://api.github.com/repos/peteoak/ratos-vaoc-auto-xy/tarball/<PINNED_ARCHIVE_MERGE_SHA>" \
  -o /tmp/AutoXY_Historical_Archive_Tool.tar.gz
tar -xzf /tmp/AutoXY_Historical_Archive_Tool.tar.gz \
  -C /home/pi/AutoXY_Historical_Archive_Tool --strip-components=1

python3 -B /home/pi/AutoXY_Historical_Archive_Tool/scripts/rc16_archive_historical_backups.py plan
```

`plan` must list all four groups with current file sizes, especially the now-smaller `pre-uninstall` tree. If a source is missing or unexpectedly large, investigate before proceeding.

## Archive to the external device, not to the Pi

Substitute the **real USB mountpoint** for `/media/pi/YOUR_USB_LABEL`:

```bash
python3 -B /home/pi/AutoXY_Historical_Archive_Tool/scripts/rc16_archive_historical_backups.py archive \
  --drive /media/pi/YOUR_USB_LABEL
```

The program prints `ARCHIVE VERIFIED` for each group and then an exact `ARCHIVE SET VERIFIED: /media/pi/.../AutoXY_Historical_Backups_<timestamp>` path and a `receipt.json` path. **Do not assume success without that final line.** The backup may involve reading many gigabytes; the printer's existing files remain intact. If it refuses, preserve the output and diagnose; do not bypass the separate-device guard.

## Re-verify — before any separate cleanup decision

Use the **exact archive directory from the previous output**:

```bash
python3 -B /home/pi/AutoXY_Historical_Archive_Tool/scripts/rc16_archive_historical_backups.py verify \
  --archive-dir /media/pi/YOUR_USB_LABEL/AutoXY_Historical_Backups_<ACTUAL_TIMESTAMP>
```

A successful run prints `REVERIFIED` four times followed by `SUCCESS: External archive matches current sources; no deletion performed.` As an additional precaution, reconnect the USB drive and re-run `verify`, and store a second copy of irreplaceable camera/configuration references.

**Do not delete the backed-up directories or any original config ZIPs based solely on this step.** Once the owner shares only the safe aggregate success output (not the private manifest), assess external backup availability, historical cross-references and actual remaining disk usage. Then develop a **separate, exact-path, reversible cleanup plan** with dry-run and verified retention of unique recovery data. The historical backups are not active print hooks; there is no urgency to remove them just to prevent the old Trial04 conflict.