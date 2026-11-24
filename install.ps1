#Requires -Version 5.1
# zsm installer for Windows (uv-managed)
# Usage (one-liner):
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/robba86/zedstead/main/install.ps1 | iex"

$ErrorActionPreference = 'Stop'

$RepoUrl    = 'https://github.com/robba86/zedstead.git'
$InstallDir = "$env:LOCALAPPDATA\zsm\source"
$Branch     = 'main'

# The ZEDSTEAD logo in the web UI's accent blue (#5b8cff, webui/src/index.css)
# with "SERVER MANAGER" below it. A console without escape codes gets plain
# blue. install.sh and install.bat carry the same rows
# (tests/test_install_banner.py).
$BannerRows = @(
    '   _____    ______  ____   _____  ______  ______  ___     ____'
    '  /__  /   / ____/ / __ \ / ___/ /_  __/ / ____/ /   |   / __ \'
    '    / /   / __/   / / / / \__ \   / /   / __/   / /| |  / / / /'
    '   / /__ / /___  / /_/ / ___/ /  / /   / /___  / ___ | / /_/ /'
    '  /____//_____/ /_____/ /____/  /_/   /_____/ /_/  |_|/_____/'
)
Write-Host ''
foreach ($Row in $BannerRows) {
    if ($Host.UI.SupportsVirtualTerminal) {
        Write-Host "$([char]27)[38;2;91;140;255m$Row$([char]27)[0m"
    } else {
        Write-Host -ForegroundColor Blue $Row
    }
}
Write-Host ''
Write-Host '                         SERVER MANAGER'
Write-Host ''
Write-Host '[zsm installer (uv)]'
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

if (-not (Read-YesNoKey 'This will install Zedstead (zsm) and its dependencies. Continue? [Y/n]')) {
    Write-Host 'Installation cancelled.'
    return
}

# ------------------------------------------------------------------
# Step 1: Ensure uv is installed
# ------------------------------------------------------------------
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    Write-Host 'Installing uv...'
    & ([scriptblock]::Create((Invoke-RestMethod 'https://astral.sh/uv/install.ps1')))

    # Add uv default install location to PATH for this session
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    Write-Host '[ERROR] uv is still not available.'
    Write-Host '  Restart your terminal and try again.'
    return
}

Write-Host 'Found uv'

# ------------------------------------------------------------------
# Step 2: Ensure Python 3.11+ exists (uv manages it)
# ------------------------------------------------------------------
Write-Host 'Ensuring Python 3.11+ is available...'

uv python install 3.11
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] Failed to install Python via uv.'
    return
}

Write-Host 'Python installed/available via uv.'

# ------------------------------------------------------------------
# Step 3: Determine package source
# ------------------------------------------------------------------
$PackageDir = $null

if ($PSScriptRoot -and (Test-Path "$PSScriptRoot\pyproject.toml") -and (Test-Path "$PSScriptRoot\app")) {
    $PackageDir = $PSScriptRoot
    Write-Host "Installing from local source: $PackageDir"
}

# ------------------------------------------------------------------
# Step 4: Clone/update repo (if no local source)
# ------------------------------------------------------------------
if (-not $PackageDir) {
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if (-not $gitCmd) {
        Write-Host '[ERROR] git is required but not found.'
        Write-Host '  Download from https://git-scm.com/download/win'
        return
    }

    if (Test-Path "$InstallDir\.git") {
        Write-Host 'Updating existing installation...'
        git -C $InstallDir fetch origin $Branch
        git -C $InstallDir reset --hard "origin/$Branch"
    } else {
        Write-Host 'Cloning zsm...'
        if (Test-Path $InstallDir) {
            Remove-Item $InstallDir -Recurse -Force
        }
        git clone --branch $Branch --single-branch $RepoUrl $InstallDir
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Host '[ERROR] git operation failed.'
        return
    }

    $PackageDir = $InstallDir
}

# ------------------------------------------------------------------
# Step 5: Install tool with uv
# ------------------------------------------------------------------
Write-Host 'Installing zsm with uv...'

uv tool install --force --reinstall $PackageDir
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] uv tool install failed.'
    return
}

# ------------------------------------------------------------------
# Step 6: Verify
# ------------------------------------------------------------------
Write-Host ''

$installed = Get-Command zsm -ErrorAction SilentlyContinue
if ($installed) {
    Write-Host '[OK] zsm installed successfully!'
    Write-Host ''
    Write-Host '  Run:'
    Write-Host '    zsm'
} else {
    Write-Host '[WARNING] Installed, but not found in PATH yet.'
    Write-Host ''
    Write-Host '  Add this folder to PATH:'
    Write-Host "    $env:USERPROFILE\.local\bin"
}

Write-Host ''
Write-Host '-----------------------------------------------------------'
Write-Host ' IMPORTANT: Restart your terminal before running the app.'
Write-Host '-----------------------------------------------------------'
Write-Host ''
Write-Host ' The DayZ server may require the following prerequisites:'
Write-Host ''
Write-Host ' 1. Visual C++ Redistributable (in your DayZ Server folder):'
Write-Host '      _CommonRedist\vcredist\2019\VC_redist.x64.exe'
Write-Host ''
Write-Host ' 2. .NET Framework 4.5.2 (in your DayZ Server folder):'
Write-Host '      _CommonRedist\DotNet\4.5.2\Microsoft .NET Framework 4.5.2.cmd'
Write-Host ''
Write-Host ' 3. DirectX End-User Runtimes (June 2010) - needed if you'
Write-Host '    get XAPOFX1_5.dll errors:'
Write-Host '      https://www.microsoft.com/en-us/download/details.aspx?id=8109'
Write-Host ''

# ------------------------------------------------------------------
# Step 7: Offer desktop shortcut
# ------------------------------------------------------------------
if (-not (Read-YesNoKey 'Create a desktop shortcut? [Y/n]')) {
    return
}

$Desktop = [Environment]::GetFolderPath('Desktop')
$ExePath = "$env:USERPROFILE\.local\bin\zsm.exe"

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("$Desktop\zsm.lnk")
$sc.TargetPath       = 'cmd.exe'
$sc.Arguments         = "/k `"$ExePath`""
$sc.WorkingDirectory  = $env:USERPROFILE
$sc.Description       = 'Zedstead'
$sc.Save()

if (Test-Path "$Desktop\zsm.lnk") {
    Write-Host '[OK] Desktop shortcut created.'
} else {
    Write-Host '[WARNING] Could not create desktop shortcut.'
}
