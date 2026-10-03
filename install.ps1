# Ace installer for Windows (PowerShell). Installs a pinned version, is safe to run twice, and never asks for keys.
# Usage: $env:ACE_REF = "v0.1.0"; powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$ref = if ($env:ACE_REF) { $env:ACE_REF } else { "v0.1.0" }
$source = if ($env:ACE_SOURCE) { $env:ACE_SOURCE } else { "git+https://github.com/the-X-alien/ace-agent@$ref" }

$py = $null
foreach ($c in @("py", "python", "python3")) {
  if (Get-Command $c -ErrorAction SilentlyContinue) {
    & $c -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = $c; break }
  }
}
if (-not $py) { Write-Error "Python 3.9 or newer is required. Install it from https://www.python.org/downloads/ and run this again." }

if (Get-Command pipx -ErrorAction SilentlyContinue) {
  Write-Host "Installing ace-agent with pipx from $source"
  pipx install --force $source
} else {
  $home_dir = if ($env:ACE_HOME) { $env:ACE_HOME } else { Join-Path $env:LOCALAPPDATA "ace-agent" }
  Write-Host "pipx not found, using a private virtual environment at $home_dir"
  & $py -m venv (Join-Path $home_dir "venv")
  $vpy = Join-Path $home_dir "venv\Scripts\python.exe"
  & $vpy -m pip install --quiet --upgrade pip
  & $vpy -m pip install --quiet --force-reinstall $source
  $scripts = Join-Path $home_dir "venv\Scripts"
  Write-Host "Add this folder to your PATH so 'ace' is found: $scripts"
}
Write-Host "Done. Check it with: ace --version  then: ace doctor"
