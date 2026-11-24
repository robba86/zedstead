@echo off
setlocal EnableDelayedExpansion

REM zsm installer for Windows (uv-managed)

set "REPO_URL=https://github.com/robba86/zedstead.git"
set "INSTALL_DIR=%LOCALAPPDATA%\zsm\source"
set "BRANCH=main"

REM The ZEDSTEAD logo in the web UI's accent blue (#5b8cff) with "SERVER
REM MANAGER" below it. ESC is the escape character, which cmd has no literal
REM for. install.sh and install.ps1 carry the same rows
REM (tests/test_install_banner.py).
for /f %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "BLUE=%ESC%[38;2;91;140;255m"
set "R=%ESC%[0m"
echo.
echo %BLUE%   _____    ______  ____   _____  ______  ______  ___     ____%R%
echo %BLUE%  /__  /   / ____/ / __ \ / ___/ /_  __/ / ____/ /   ^|   / __ \%R%
echo %BLUE%    / /   / __/   / / / / \__ \   / /   / __/   / /^| ^|  / / / /%R%
echo %BLUE%   / /__ / /___  / /_/ / ___/ /  / /   / /___  / ___ ^| / /_/ /%R%
echo %BLUE%  /____//_____/ /_____/ /____/  /_/   /_____/ /_/  ^|_^|/_____/%R%
echo.
echo                          SERVER MANAGER
echo.
echo [zsm installer (uv)]
echo.

<nul set /p "=This will install Zedstead (zsm) and its dependencies. Continue? [Y/n] "
choice /c YN /n >nul
if errorlevel 2 (
    echo N
    echo Installation cancelled.
    exit /b 0
)
echo Y

REM ------------------------------------------------------------
REM Step 1: Ensure uv is installed
REM ------------------------------------------------------------
where uv >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Installing uv...

    powershell -ExecutionPolicy Bypass -NoProfile -Command ^
      "irm https://astral.sh/uv/install.ps1 | iex"

    REM Add uv default install location to PATH for this session
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

where uv >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] uv is still not available.
    echo   Restart your terminal and try again.
    exit /b 1
)

echo Found uv

REM ------------------------------------------------------------
REM Step 2: Ensure Python 3.11+ exists (uv manages it)
REM ------------------------------------------------------------
echo Ensuring Python 3.11+ is available...

uv python install 3.11

if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to install Python via uv.
    exit /b 1
)

REM Use uv-managed python from now on
echo Python installed/available via uv.

REM ------------------------------------------------------------
REM Step 3: Determine package source
REM ------------------------------------------------------------
set "SCRIPT_DIR=%~dp0"

REM Remove trailing backslash
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

if exist "%SCRIPT_DIR%\pyproject.toml" if exist "%SCRIPT_DIR%\app" (
    set "PACKAGE_DIR=%SCRIPT_DIR%"
    echo Installing from local source: !PACKAGE_DIR!
    goto :install
)

REM ------------------------------------------------------------
REM Step 4: Clone/update repo
REM ------------------------------------------------------------
where git >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] git is required but not found.
    echo   Download from https://git-scm.com/download/win
    exit /b 1
)

if exist "%INSTALL_DIR%\.git" (
    echo Updating existing installation...
    git -C "%INSTALL_DIR%" fetch origin %BRANCH%
    git -C "%INSTALL_DIR%" reset --hard origin/%BRANCH%
) else (
    echo Cloning zsm...
    if exist "%INSTALL_DIR%" rmdir /s /q "%INSTALL_DIR%"
    git clone --branch %BRANCH% --single-branch "%REPO_URL%" "%INSTALL_DIR%"
)

set "PACKAGE_DIR=%INSTALL_DIR%"

REM ------------------------------------------------------------
REM Step 5: Install tool with uv
REM ------------------------------------------------------------
:install
echo Installing zsm with uv...

uv tool install --force --reinstall "%PACKAGE_DIR%"

REM ------------------------------------------------------------
REM Step 6: Verify
REM ------------------------------------------------------------
echo.
where zsm >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] zsm installed successfully.
    echo.
    echo   Run:
    echo     zsm
) else (
    echo [WARNING] Installed, but not found in PATH yet.
    echo.
    echo   Add this folder to PATH:
    echo     %USERPROFILE%\.local\bin
)

echo.
echo -----------------------------------------------------------
echo  IMPORTANT: Restart your terminal before running the app.
echo -----------------------------------------------------------
echo.
echo  The DayZ server may require the following prerequisites:
echo.
echo  1. Visual C++ Redistributable (in your DayZ Server folder):
echo       _CommonRedist\vcredist\2019\VC_redist.x64.exe
echo.
echo  2. .NET Framework 4.5.2 (in your DayZ Server folder):
echo       _CommonRedist\DotNet\4.5.2\Microsoft .NET Framework 4.5.2.cmd
echo.
echo  3. DirectX End-User Runtimes (June 2010) - needed if you
echo     get XAPOFX1_5.dll errors:
echo       https://www.microsoft.com/en-us/download/details.aspx?id=8109
echo.

REM ------------------------------------------------------------
REM Step 7: Offer desktop shortcut
REM ------------------------------------------------------------
<nul set /p "=Create a desktop shortcut? [Y/n] "
choice /c YN /n >nul
if errorlevel 2 (
    echo N
    goto :done
)
echo Y

set "DESKTOP=%USERPROFILE%\Desktop"
set "EXE_PATH=%USERPROFILE%\.local\bin\zsm.exe"

powershell -ExecutionPolicy Bypass -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$s = $ws.CreateShortcut('%DESKTOP%\zsm.lnk');" ^
  "$s.TargetPath = 'cmd.exe';" ^
  "$s.Arguments = '/k \"%EXE_PATH%\"';" ^
  "$s.WorkingDirectory = '%USERPROFILE%';" ^
  "$s.Description = 'Zedstead';" ^
  "$s.Save()"

if exist "%DESKTOP%\zsm.lnk" (
    echo [OK] Desktop shortcut created.
) else (
    echo [WARNING] Could not create desktop shortcut.
)

:done
endlocal
