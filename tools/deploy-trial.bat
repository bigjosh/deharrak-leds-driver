@echo off
setlocal DisableDelayedExpansion
rem Override execution policy only for this child; keep one deployment implementation.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy-trial.ps1" %*
exit /b %ERRORLEVEL%
