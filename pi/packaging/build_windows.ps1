# Builds a Windows .exe of the enrolment app for DEMOS and UI work.
#
#   cd pi ; powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# This build has no NFC hardware drivers: run it with --sim (the built-in tag simulator).
# The real Raspberry Pi build is packaging\build_pi.sh - a .exe cannot run on a Raspberry Pi.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

python -m venv .build-venv
& .\.build-venv\Scripts\python.exe -m pip install --upgrade pip wheel
& .\.build-venv\Scripts\python.exe -m pip install customtkinter Pillow cryptography argon2-cffi requests pyinstaller

& .\.build-venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name dso-enroller `
    --collect-all customtkinter pi_entry.py

Write-Host ""
Write-Host "Built dist\dso-enroller\dso-enroller.exe"
Write-Host "Run:  `$env:DSO_ENROLLER_HOME='<config folder>'; dist\dso-enroller\dso-enroller.exe --sim"
