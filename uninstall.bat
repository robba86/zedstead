@echo off
setlocal EnableDelayedExpansion

REM zsm uninstaller for Windows (uv-managed)

set "INSTALL_DIR=%LOCALAPPDATA%\zsm\source"
set "CONFIG_DIR=%LOCALAPPDATA%\zsm"
set "DESKTOP=%USERPROFILE%\Desktop"

echo.
echo [zsm uninstaller (uv)]
echo.

<nul set /p "=This will uninstall zsm. Continue? [Y/n] "
choice /c YN /n >nul
if errorlevel 2 (
    echo N
    echo Uninstall cancelled.
    exit /b 0
)
echo Y

REM ------------------------------------------------------------
REM Step 1: Remove the uv tool
REM ------------------------------------------------------------
REM No jump and no label in this file: it is stored with LF line endings,
REM and cmd can miss a label in such a file.
set "UV_STATE=missing"
where uv >nul 2>&1
if %ERRORLEVEL% equ 0 set "UV_STATE=found"

REM Where uv keeps the tool. Unknown on a uv too old to say: then uv is
REM simply asked to remove it, as this script always did.
set "TOOL_DIR="
if "%UV_STATE%"=="found" for /f "usebackq delims=" %%d in (`uv tool dir`) do set "TOOL_DIR=%%d\zsm"
if "%UV_STATE%"=="found" if defined TOOL_DIR if not exist "%TOOL_DIR%" (
    echo zsm is not installed as a uv tool, skipping.
    set "UV_STATE=skip"
)

REM zsm must not be running. Windows does not delete the files of a running
REM program: uv then stops half way with "Access is denied", and what is left
REM is a panel that answers "Not Found" and a zsm command that is broken.
set "ZSM_TOOL_DIR=%TOOL_DIR%"
set "ZSM_RUNNING=0"
if "%UV_STATE%"=="found" if defined TOOL_DIR powershell -NoProfile -NonInteractive -Command "$dir = $env:ZSM_TOOL_DIR + [System.IO.Path]::DirectorySeparatorChar; if (Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($dir, [System.StringComparison]::OrdinalIgnoreCase) }) { exit 3 } else { exit 0 }"
if "%UV_STATE%"=="found" if defined TOOL_DIR if %ERRORLEVEL% equ 3 set "ZSM_RUNNING=1"
if "%ZSM_RUNNING%"=="1" (
    echo [ERROR] zsm is still running.
    echo   Close it first: press Ctrl+C in the window it runs in, then run this again.
    echo   Nothing was removed.
    exit /b 1
)

if "%UV_STATE%"=="missing" (
    echo [WARNING] uv not found on PATH; skipping uv tool uninstall.
    echo   If zsm is still on your PATH, install uv and run: uv tool uninstall zsm
)
if "%UV_STATE%"=="found" (
    echo Removing zsm with uv...
    uv tool uninstall zsm
    if errorlevel 1 (
        if defined TOOL_DIR (
            echo [ERROR] uv could not remove zsm. Its message is above.
            echo   Nothing else was removed. If a zsm window is still open, close it and run this again.
            exit /b 1
        )
        echo [WARNING] uv did not remove zsm. Its message is above.
    )
)

REM ------------------------------------------------------------
REM Step 2: Remove the cloned source (remote-install location)
REM ------------------------------------------------------------
if exist "%INSTALL_DIR%" (
    echo Removing cloned source: %INSTALL_DIR%
    rmdir /s /q "%INSTALL_DIR%"
)

REM ------------------------------------------------------------
REM Step 3: Remove desktop shortcut
REM ------------------------------------------------------------
if exist "%DESKTOP%\zsm.lnk" (
    echo Removing desktop shortcut...
    del /q "%DESKTOP%\zsm.lnk"
)

REM ------------------------------------------------------------
REM Step 4: Optionally remove user config + cached map images
REM ------------------------------------------------------------
if exist "%CONFIG_DIR%" (
    echo.
    REM No exclamation mark in a line this file prints: it runs with delayed
    REM expansion, where a lone one swallows the rest of the line. This line
    REM used to start with one in brackets and printed as a single bracket.
    echo [NOTE] Config dir holds your server settings, presets and cached maps:
    echo       %CONFIG_DIR%
    REM The word "delete" and Enter deletes, anything else keeps. On Linux a
    REM single keypress deleted a config dir by a slip, and so did "y" and Enter.
    set "ANSWER="
    set /p "ANSWER=Press Enter to keep it. To delete it as well, type delete and press Enter: "
    if /i "!ANSWER!"=="delete" (
        rmdir /s /q "%CONFIG_DIR%"
        echo [OK] Config removed.
    ) else (
        echo Config kept at %CONFIG_DIR%
    )
)

REM ------------------------------------------------------------
REM Step 5: Done
REM ------------------------------------------------------------
echo.
where zsm >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [WARNING] 'zsm' is still on your PATH. Restart your terminal;
    echo   if it persists, run: uv tool uninstall zsm
) else (
    echo [OK] zsm uninstalled.
)
echo.

endlocal
