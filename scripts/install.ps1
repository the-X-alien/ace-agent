# Ace installer for Windows (PowerShell). Installs a pinned version, is safe to run twice, and never asks for keys.
# Usage: $env:ACE_REF = "v0.1.2"; powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$ref = if ($env:ACE_REF) { $env:ACE_REF } else { "v0.1.2" }
$source = if ($env:ACE_SOURCE) { $env:ACE_SOURCE } else { "git+https://github.com/the-X-alien/ace-agent@$ref" }

$py = $null
foreach ($c in @("py", "python", "python3")) {
  if (Get-Command $c -ErrorAction SilentlyContinue) {
    # Native stderr must not stop the script: the Microsoft Store "python" stub prints an error and exits non-zero.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $c -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" *> $null } catch { $global:LASTEXITCODE = 1 }
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -eq 0) { $py = $c; break }
  }
}
if (-not $py) { Write-Error "Python 3.9 or newer is required. Install it from https://www.python.org/downloads/ and run this again." }

if ($source -like "git+*" -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Error "git is required to install from the private repository. Install Git for Windows (https://git-scm.com/download/win), sign in to GitHub, and run this again."
}
if ((Get-Command pipx -ErrorAction SilentlyContinue) -and -not $env:ACE_NO_PIPX) {
  Write-Host "Installing ace-agent with pipx from $source"
  pipx install --force $source
  if ($LASTEXITCODE -ne 0) { Write-Error "pipx could not install from $source. For a private repo, sign in with git first and check you have access." }
  Write-Host "If 'ace' is not found, run: pipx ensurepath  and open a new terminal."
} else {
  $home_dir = if ($env:ACE_HOME) { $env:ACE_HOME } else { Join-Path $env:LOCALAPPDATA "ace-agent" }
  Write-Host "pipx not found, using a private virtual environment at $home_dir"
  & $py -m venv (Join-Path $home_dir "venv")
  $vpy = Join-Path $home_dir "venv\Scripts\python.exe"
  & $vpy -m pip install --quiet --upgrade pip
  & $vpy -m pip install --quiet --force-reinstall $source
  if ($LASTEXITCODE -ne 0) { Write-Error "pip could not install from $source. For a private repo, sign in with git first (git credential manager) and check you have access." }
  $scripts = Join-Path $home_dir "venv\Scripts"
  Write-Host "Add this folder to your PATH so 'ace' is found: $scripts"
}
Write-Host "Done. Check it with: ace --version  then: ace doctor"
