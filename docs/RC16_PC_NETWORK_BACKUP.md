# RC16: Windows network backup without a printer USB port

Status: development candidate, subject to CI. No backup deletion or printer calibration changes.

The owner ran the read-only inventory on the actual Pi and confirmed four groups totaling 4,400,944,978 bytes and 14,032 files. This is source discovery, not yet a verified off-device backup.

The matched pair of scripts:
- scripts/rc16_archive_historical_backups.py: network-stream subcommand; read-only streaming via SSH, no archives staged on the Pi.
- scripts/rc16_pc_network_backup.py: receives all four streams onto Windows, verifies each archive, writes a private receipt only after all succeed, and independently re-verifies local copies.

Safety checks include bounded manifests, exact group allowlist, SHA256 source inventories, metadata and member checks, source hash recheck after streaming, required final completion marker, successful SSH process exit, and SHA256 archive verification. Streams are encrypted using SSH; validate the printer SSH host fingerprint. Archive files can include credentials, configuration and sensitive photos; never upload them or the receipts to public GitHub.

Requirements: Python 3 and OpenSSH on Windows, at least 6 GiB free, an SSH connection to the Pi. Run one-off commands below after substituting the pinned tested revision. No mounted USB drive required; no Pi files are deleted.

## Download on Pi SSH terminal

~~~bash
REV="<PINNED_TESTED_SHA>"
mkdir -p /home/pi/AutoXY_Historical_Archive_Tool
curl -fL --retry 3 "https://raw.githubusercontent.com/peteoak/ratos-vaoc-auto-xy/$REV/scripts/rc16_archive_historical_backups.py" -o /home/pi/AutoXY_Historical_Archive_Tool/rc16_archive_historical_backups.py
python3 -B /home/pi/AutoXY_Historical_Archive_Tool/rc16_archive_historical_backups.py plan
~~~

## Windows PowerShell

~~~powershell
$Revision = "<PINNED_TESTED_SHA>"
$ToolFolder = "$HOME\AutoXY_PC_Backup_Tool"
New-Item -ItemType Directory -Force -Path $ToolFolder | Out-Null
$Base = "https://raw.githubusercontent.com/peteoak/ratos-vaoc-auto-xy/$Revision/scripts"
curl.exe -fL "$Base/rc16_archive_historical_backups.py" -o "$ToolFolder\rc16_archive_historical_backups.py"
curl.exe -fL "$Base/rc16_pc_network_backup.py" -o "$ToolFolder\rc16_pc_network_backup.py"
py -3 --version
ssh -V
py -3 -m py_compile "$ToolFolder\rc16_archive_historical_backups.py" "$ToolFolder\rc16_pc_network_backup.py"
~~~

Check the Pi host fingerprint when connecting with ssh for the first time; never disable SSH host-key checking.

~~~powershell
$Destination = "$HOME\Documents\AutoXY_Printer_Backups"
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
py -3 "$ToolFolder\rc16_pc_network_backup.py" pull --host <PI_HOST_OR_IP> --user pi --destination "$Destination"
~~~

The final line must include COMPLETE PC BACKUP with a real folder path. Reverify using that exact path:

~~~powershell
py -3 "$ToolFolder\rc16_pc_network_backup.py" verify --archive-dir "<ACTUAL_BACKUP_DIRECTORY>"
~~~

If a transfer fails, an incomplete PC folder is deliberately left behind, without a completed receipt. Diagnose the cause; do not mistake partial transfer for a valid backup. Preserve all original Pi sources. Another off-PC copy is recommended before considering any subsequent narrowly scoped historical cleanup.
