$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) {throw 'Run setup.ps1 first.'}
& '.venv/Scripts/python.exe' 'run_workspace.py'
if ($LASTEXITCODE -ne 0) {throw 'Workspace exited with an error.'}
