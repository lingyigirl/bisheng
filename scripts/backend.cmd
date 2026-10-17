@echo off
REM ---------------------------------------------------------------------------
REM Wrapper around scripts\backend.ps1 -- bypasses PowerShell execution policy
REM so the script can be run from cmd / double-clicked without extra setup.
REM
REM Usage is identical to the .ps1, e.g.:
REM   scripts\backend.cmd                     restart all backend services
REM   scripts\backend.cmd -Stop               stop everything
REM   scripts\backend.cmd -Status             show status
REM   scripts\backend.cmd -Logs api -Follow   follow the API log
REM   scripts\backend.cmd -Only api,workflow  start a subset
REM   scripts\backend.cmd -DryRun             print commands only
REM   scripts\backend.cmd -SelfTest           verify the launch mechanism
REM
REM NOTE: this file is intentionally ASCII-only. cmd.exe decodes batch files
REM using the OEM code page (GBK on Chinese Windows), so UTF-8 Chinese comments
REM here would show up as mojibake. Chinese messages live in backend.ps1,
REM which is UTF-8 with BOM and read correctly by PowerShell.
REM ---------------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0backend.ps1" %*
exit /b %ERRORLEVEL%
