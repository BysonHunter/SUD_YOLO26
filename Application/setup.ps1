param([switch]$Cuda)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
function Run-Checked { param([string]$Exe,[string[]]$Arguments); & $Exe @Arguments; if ($LASTEXITCODE -ne 0) {throw "Command failed: $Exe $Arguments"} }
if (-not (Test-Path '.venv/Scripts/python.exe')) { Run-Checked 'py' @('-3.11','-m','venv','.venv') }
$AppPython=Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
Run-Checked $AppPython @('-m','pip','install','--upgrade','pip')
$TorchIndex=if ($Cuda) {'https://download.pytorch.org/whl/cu128'} else {'https://download.pytorch.org/whl/cpu'}
Run-Checked $AppPython @('-m','pip','install','torch==2.7.1','torchvision==0.22.1','--index-url',$TorchIndex)
Run-Checked $AppPython @('-m','pip','install','-r','requirements.txt','-c','constraints.txt')
Run-Checked $AppPython @('-m','pip','check')
Run-Checked $AppPython @('run_app.py','--check')
Write-Host 'Install trained weights with install_model.py, then run start.ps1.'
