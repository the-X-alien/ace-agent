# Ace installer for Windows (PowerShell 5.1 or 7). Installs a pinned version, is safe to run twice, never asks for keys or admin rights.
# One line:  irm https://raw.githubusercontent.com/the-X-alien/ace-agent/v0.1.5/scripts/install.ps1 | iex
# Remove:    $env:ACE_UNINSTALL = "1"; irm <same url> | iex
$ErrorActionPreference = "Stop"
$ref = if ($env:ACE_REF) { $env:ACE_REF } else { "v0.1.5" }
$source = if ($env:ACE_SOURCE) { $env:ACE_SOURCE } else { "https://github.com/the-X-alien/ace-agent/archive/refs/tags/$ref.zip" }
$base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME "AppData\Local" }
$home_dir = if ($env:ACE_HOME) { $env:ACE_HOME } else { Join-Path $base "ace-agent" }
$scripts = Join-Path $home_dir "venv\Scripts"

function Remove-FromUserPath($dir) {
  $cur = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($cur) {
    $keep = @($cur.Split(";") | Where-Object { $_ -and ($_.TrimEnd("\") -ne $dir.TrimEnd("\")) })
    [Environment]::SetEnvironmentVariable("Path", ($keep -join ";"), "User")
  }
}

if ($env:ACE_UNINSTALL) {
  Remove-FromUserPath $scripts
  if (Test-Path $home_dir) { Remove-Item -Recurse -Force $home_dir }
  Write-Host "Ace removed. Open a new terminal for PATH to refresh."
  return
}

function Find-Python {
  foreach ($c in @("py", "python", "python3")) {
    if (Get-Command $c -ErrorAction SilentlyContinue) {
      # Native stderr must not stop the script: the Microsoft Store "python" stub prints an error and exits non-zero.
      $prev = $ErrorActionPreference
      $ErrorActionPreference = "Continue"
      try { & $c -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" *> $null } catch { $global:LASTEXITCODE = 1 }
      $ErrorActionPreference = $prev
      if ($LASTEXITCODE -eq 0) { return $c }
    }
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host "Python 3.9+ not found. Installing Python 3.12 for your user account with winget (no admin needed)..."
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    winget install --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    $ErrorActionPreference = $prev
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    $py = Find-Python
  }
  if (-not $py) {
    Write-Error "Python 3.9 or newer is needed and could not be installed automatically. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this command again."
  }
}

Write-Host "Installing Ace $ref into $home_dir"
& $py -m venv (Join-Path $home_dir "venv")
if ($LASTEXITCODE -ne 0) { Write-Error "Could not create a Python virtual environment." }
$vpy = Join-Path $home_dir "venv\Scripts\python.exe"
& $vpy -m pip install --quiet --disable-pip-version-check --upgrade pip
& $vpy -m pip install --quiet --disable-pip-version-check --force-reinstall $source
if ($LASTEXITCODE -ne 0) { Write-Error "pip could not install Ace from $source. Check your internet connection and try again." }

# Put Ace on PATH for this session and for every future terminal (your user PATH only, no duplicates).
if (-not (($env:Path.Split(";") | ForEach-Object { $_.TrimEnd("\") }) -contains $scripts.TrimEnd("\"))) { $env:Path = "$scripts;$env:Path" }
$user = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $user) { $user = "" }
if (-not (($user.Split(";") | ForEach-Object { $_.TrimEnd("\") }) -contains $scripts.TrimEnd("\"))) {
  $new = if ($user) { "$user;$scripts" } else { $scripts }
  [Environment]::SetEnvironmentVariable("Path", $new, "User")
}

& (Join-Path $scripts "ace-agent.exe") --version
if ($LASTEXITCODE -ne 0) { Write-Error "Ace installed but did not start. Run: $scripts\ace-agent.exe doctor" }
# Another program may already own the name "ace" (for example a different tool installed with npm or bun). Never touch it; "ace-agent" always means this one.
$others = @(Get-Command ace -All -ErrorAction SilentlyContinue | Where-Object { $_.Source -and (Split-Path $_.Source -Parent).TrimEnd("\") -ne $scripts.TrimEnd("\") })
if ($others.Count -gt 0) {
  Write-Host "Note: a different program named 'ace' already exists on this PC: $($others[0].Source). Ace did not change it. Use 'ace-agent' to run this Ace."
}
Write-Host "Done. Type: ace-agent doctor   (new terminal windows pick this up automatically; this one already has it)"
