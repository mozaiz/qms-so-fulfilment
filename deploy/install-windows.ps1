<#
  QMS installer for Windows.

  Double-click "Install QMS (Windows).cmd" instead of running this by hand.

  It deliberately avoids needing an administrator: Python is taken from the
  official *embeddable* zip, which unpacks into a folder and registers nothing.
  Nothing is written outside $InstallDir.

  NOTE: this path has not been exercised on a real Windows box. If anything
  fails, install.sh (Linux/macOS) is the tested path, and INSTALL.md documents
  the manual steps.
#>

[CmdletBinding()]
param(
  [string]$InstallDir = "C:\QMS",
  [int]$Port = 8099,
  [string]$StoreCode = "MCSQ01",
  [string]$StoreName = "Machines Store",
  [string]$PythonVersion = "3.12.7"
)

$ErrorActionPreference = "Stop"
function Say($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m)  { Write-Host "    [ok] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "    [!]  $m" -ForegroundColor Yellow }

$Source = Split-Path -Parent $MyInvocation.MyCommand.Path
# when run from deploy/, the app lives one level up
if (-not (Test-Path (Join-Path $Source "app.py"))) {
  $Source = Split-Path -Parent $Source
}
if (-not (Test-Path (Join-Path $Source "app.py"))) {
  throw "Cannot find app.py. Run this from the QMS folder."
}

Say "Checking Python"
$PyExe = $null
try {
  $c = Get-Command python -ErrorAction Stop
  $v = & $c.Source -c "import sys; print(sys.version_info[0], sys.version_info[1])" 2>$null
  if ($v -match "3 1[0-9]") { $PyExe = $c.Source; Ok "using system python: $PyExe" }
} catch { }
if (-not $PyExe) {
  $ver = ($PythonVersion -split '\.')[0..1] -join '.'
  $embed = Join-Path $env:TEMP "python-embed.zip"
  $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
  Warn "no usable system python - downloading the portable build"
  Write-Host "    $url"
  Invoke-WebRequest -Uri $url -OutFile $embed -UseBasicParsing
  $pyDir = Join-Path $InstallDir "python"
  New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
  Expand-Archive -Path $embed -DestinationPath $pyDir -Force
  # the embeddable build disables site-packages until you uncomment this
  Get-ChildItem $pyDir -Filter "*._pth" | ForEach-Object {
    (Get-Content $_.FullName) -replace "^#\s*import site", "import site" |
      Set-Content $_.FullName
  }
  $PyExe = Join-Path $pyDir "python.exe"
  Ok "portable python at $PyExe"
}

Say "Copying files to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
foreach ($item in @("app.py", "requirements.txt", "run.sh", "README.md")) {
  $p = Join-Path $Source $item
  if (Test-Path $p) { Copy-Item $p $InstallDir -Force }
}
Copy-Item (Join-Path $Source "static") $InstallDir -Recurse -Force
Ok "files copied"

Say "Installing Python packages"
Push-Location $InstallDir
& $PyExe -m pip install --quiet --upgrade pip 2>$null
if ($LASTEXITCODE -ne 0) {
  Warn "pip missing - bootstrapping it"
  $gp = Join-Path $env:TEMP "get-pip.py"
  Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $gp -UseBasicParsing
  & $PyExe $gp
}
& $PyExe -m pip install --quiet -r requirements.txt
Pop-Location
Ok "packages installed"

$EnvFile = Join-Path $InstallDir "qms.env"
if (-not (Test-Path $EnvFile)) {
  @"
QMS_PORT=$Port
QMS_DB=$InstallDir\qms.db
QMS_STORE_CODE=$StoreCode
QMS_STORE_NAME=$StoreName
QMS_TZ=Asia/Kuala_Lumpur
QMS_STALE_MIN=15
QMS_SLA_MIN=10
QMS_POS_COUNT=4
"@ | Set-Content -Encoding ASCII $EnvFile
  Ok "wrote $EnvFile"
} else {
  Ok "kept existing qms.env"
}

Say "Creating the launcher"
$Bat = Join-Path $InstallDir "Start QMS.bat"
@"
@echo off
title QMS - SO Fulfilment
cd /d "$InstallDir"
echo Starting QMS... a browser window will open. Keep this window open.
echo Close this window to stop QMS.
start "" "http://localhost:$Port"
"$PyExe" -m uvicorn app:app --host 0.0.0.0 --port $Port
pause
"@ | Set-Content -Encoding ASCII $Bat
Ok "created $Bat"

# desktop shortcut
try {
  $desktop = [Environment]::GetFolderPath("Desktop")
  $ws = New-Object -ComObject WScript.Shell
  $lnk = $ws.CreateShortcut((Join-Path $desktop "QMS.lnk"))
  $lnk.TargetPath = $Bat
  $lnk.WorkingDirectory = $InstallDir
  $lnk.Description = "QMS - SO Fulfilment"
  $lnk.Save()
  Ok "desktop shortcut created"
} catch { Warn "could not create the desktop shortcut (not fatal)" }

Say "Done"
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
       Select-Object -First 1).IPAddress
Write-Host "    Start QMS      :  double-click 'Start QMS' on the Desktop, or $Bat"
Write-Host "    This computer  :  http://localhost:$Port      (camera works here)"
Write-Host "    Other devices  :  http://${ip}:$Port          (no camera - use Manual Entry)"
Write-Host "    Settings       :  $EnvFile"
Write-Host ""
Write-Host "    To start automatically at login, run:"
Write-Host "      shell:startup   and put a shortcut to 'Start QMS.bat' in the folder that opens."
