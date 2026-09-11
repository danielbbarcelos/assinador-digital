@echo off
rem Atalho para quem prefere clicar duas vezes em vez de abrir o PowerShell.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
