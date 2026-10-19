@echo off
REM ---------------------------------------------------------------------------
REM Wrapper around scripts\frontend.ps1 -- bypasses PowerShell execution policy
REM so the script can be run from cmd / double-clicked without extra setup.
REM
REM Usage is identical to the .ps1, e.g.:
REM   scripts\frontend.cmd                 restart client + platform dev servers
REM   scripts\frontend.cmd -Stop           stop everything
REM   scripts\frontend.cmd -Status         show status
REM   scripts\frontend.cmd -Logs client -Follow   follow the client log
REM   scripts\frontend.cmd -Only client    start a subset
REM   scripts\frontend.cmd -Only ui        start the docs site
REM   scripts\frontend.cmd -DryRun         print commands only
REM
REM NOTE: this file is intentionally ASCII-only. cmd.exe decodes batch files
REM using the OEM code page (GBK on Chinese Windows), so UTF-8 Chinese comments
REM here would show up as mojibake. Chinese messages live in frontend.ps1,
REM which is UTF-8 with BOM and read correctly by PowerShell.
REM ---------------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0frontend.ps1" %*
exit /b %ERRORLEVEL%
