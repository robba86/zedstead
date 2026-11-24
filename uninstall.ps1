#Requires -Version 5.1
# zsm uninstaller for Windows (uv-managed)
# Usage (one-liner):
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/robba86/zedstead/main/uninstall.ps1 | iex"

$ErrorActionPreference = 'Stop'

$InstallDir = "$env:LOCALAPPDATA\zsm\source"
$ConfigDir  = "$env:LOCALAPPDATA\zsm"
$Shortcut   = Join-Path ([Environment]::GetFolderPath('Desktop')) 'zsm.lnk'

Write-Host ''
Write-Host '[zsm uninstaller (uv)]'
Write-Host ''

function Read-YesNoKey {
    param([string]$Prompt, [bool]$DefaultYes = $true)
    Write-Host -NoNewline "$Prompt "
    if ([Environment]::UserInteractive -and $Host.UI.RawUI -and -not [Console]::IsInputRedirected) {
        while ($true) {
            $key = [Console]::ReadKey($true)
            switch ($key.Key) {
                'Y'      { Write-Host 'Y'; return $true }
                'N'      { Write-Host 'N'; return $false }
                'Enter'  { Write-Host '';  return $DefaultYes }
            }
        }
    } else {
        $r = Read-Host
        return ($r -in @('', 'Y', 'y'))
    }
}

# The one step that deletes the admin's data takes the word "delete" and
# Enter, nothing less. On Linux a single keypress deleted a config dir by a
# slip, and so did "y" and Enter straight after the first question, whose
# answer is also y (first-hour test, 2026 Q4). -eq ignores case.
function Read-TypedDelete {
    param([string]$Prompt)
    Write-Host -NoNewline "$Prompt "
    $r = "$(Read-Host)".Trim()
    return ($r -eq 'delete')
}

if (-not (Read-YesNoKey 'This will uninstall zsm. Continue? [Y/n]')) {
    Write-Host 'Uninstall cancelled.'
    return
}

# ------------------------------------------------------------------
# Step 1: Remove the uv tool
# ------------------------------------------------------------------
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCmd) {
    # Where uv keeps the tool. Unknown on a uv too old to say: then uv is
    # simply asked to remove it, as this script always did.
    $toolDir = $null
    $toolsRoot = uv tool dir | Select-Object -First 1
    if ($LASTEXITCODE -eq 0 -and $toolsRoot) {
        $toolDir = Join-Path "$toolsRoot".Trim() 'zsm'
    }

    if ($toolDir -and -not (Test-Path $toolDir)) {
        Write-Host 'zsm is not installed as a uv tool, skipping.'
    } else {
        # zsm must not be running. Windows does not delete the files of a
        # running program: uv then stops half way with "Access is denied", and
        # what is left is a panel that answers "Not Found" and a zsm command
        # that is broken.
        $running = @()
        if ($toolDir) {
            $prefix = $toolDir + [System.IO.Path]::DirectorySeparatorChar
            try {
                $running = @(Get-CimInstance Win32_Process | Where-Object {
                    $_.ExecutablePath -and
                    $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
                })
            } catch {
                $running = @()
            }
        }
        if ($running.Count -gt 0) {
            Write-Host '[ERROR] zsm is still running.'
            Write-Host '  Close it first: press Ctrl+C in the window it runs in, then run this again.'
            Write-Host '  Nothing was removed.'
            return
        }

        Write-Host 'Removing zsm with uv...'
        uv tool uninstall zsm
        if ($LASTEXITCODE -ne 0) {
            if ($toolDir) {
                Write-Host '[ERROR] uv could not remove zsm. Its message is above.'
                Write-Host '  Nothing else was removed. If a zsm window is still open, close it and run this again.'
                return
            }
            Write-Host '[WARNING] uv did not remove zsm. Its message is above.'
        }
    }
} else {
    Write-Host '[WARNING] uv not found on PATH; skipping uv tool uninstall.'
    Write-Host '  If zsm is still on your PATH, install uv and run: uv tool uninstall zsm'
}

# ------------------------------------------------------------------
# Step 2: Remove the cloned source (remote-install location)
# ------------------------------------------------------------------
if (Test-Path $InstallDir) {
    Write-Host "Removing cloned source: $InstallDir"
    Remove-Item $InstallDir -Recurse -Force
}

# ------------------------------------------------------------------
# Step 3: Remove desktop shortcut
# ------------------------------------------------------------------
if (Test-Path $Shortcut) {
    Write-Host 'Removing desktop shortcut...'
    Remove-Item $Shortcut -Force
}

# ------------------------------------------------------------------
# Step 4: Optionally remove user config + cached map images
# ------------------------------------------------------------------
if (Test-Path $ConfigDir) {
    Write-Host ''
    Write-Host '[!] Config dir holds your server settings, presets and cached maps:'
    Write-Host "      $ConfigDir"
    if (Read-TypedDelete 'Press Enter to keep it. To delete it as well, type delete and press Enter:') {
        Remove-Item $ConfigDir -Recurse -Force
        Write-Host '[OK] Config removed.'
    } else {
        Write-Host "Config kept at $ConfigDir"
    }
}

# ------------------------------------------------------------------
# Step 5: Done
# ------------------------------------------------------------------
Write-Host ''
$installed = Get-Command zsm -ErrorAction SilentlyContinue
if ($installed) {
    Write-Host "[WARNING] 'zsm' is still on your PATH. Restart your terminal;"
    Write-Host '  if it persists, run: uv tool uninstall zsm'
} else {
    Write-Host '[OK] zsm uninstalled.'
}
Write-Host ''
