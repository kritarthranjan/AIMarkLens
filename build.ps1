# Builds AIMarkLens release files into .dist\<Edition>\ :
#
#   AIMarkLens-<version>-<Edition>-Setup.exe      standard installer with uninstaller (needs Inno Setup 6)
#   AIMarkLens-<version>-<Edition>-Portable.exe   single self-contained file that runs from any folder
#   AIMarkLens\                                   the application folder the installer packages
#
# and .dist\SHA256SUMS.txt for every release file.
#
# Usage (from this folder):
#   powershell.exe -ExecutionPolicy Bypass -File .\build.ps1                       # both editions, everything
#   powershell.exe -ExecutionPolicy Bypass -File .\build.ps1 -Edition Public       # one edition
#   powershell.exe -ExecutionPolicy Bypass -File .\build.ps1 -SkipTests -NoPortable
param(
    [ValidateSet("All", "Public", "Personal")][string]$Edition = "All",
    [switch]$SkipTests,
    [switch]$NoPortable,
    [switch]$NoInstaller
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Creating virtual environment (.venv)..."
    py -3.12 -m venv .venv
    if (-not $?) { throw "Python 3.12 is required (install it from python.org)." }
}
& $python -m pip install --disable-pip-version-check -q -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Installing dependencies failed." }

$version = (Select-String -Path "AIMarkLens\__init__.py" -Pattern '^APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
$editions = if ($Edition -eq "All") { @("Public", "Personal") } else { @($Edition) }

$iscc = $null
if (-not $NoInstaller) {
    $candidates = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")
    $iscc = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $iscc) {
        $command = Get-Command iscc.exe -ErrorAction SilentlyContinue
        if ($command) { $iscc = $command.Source }
    }
    if (-not $iscc) {
        throw "Inno Setup 6 is needed for the installers (winget install JRSoftware.InnoSetup), or pass -NoInstaller."
    }
}

if (-not $SkipTests) {
    Write-Host "Running tests..."
    & $python -m pytest tests -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; the build was stopped." }
}

Write-Host "Deriving icons, splash screens and installer artwork from assets\..."
& $python scripts\make_assets.py
if ($LASTEXITCODE -ne 0) { throw "Asset generation failed." }

function Test-Build([string]$exe) {
    $check = Start-Process -FilePath $exe -ArgumentList "--self-test" -Wait -PassThru
    if ($check.ExitCode -ne 0) { throw "$exe failed its self-test (exit code $($check.ExitCode))." }
}

$buildInfo = "AIMarkLens\_build_info.py"
try {
    foreach ($ed in $editions) {
        $lower = $ed.ToLower()
        $out = Join-Path $PSScriptRoot ".dist\$ed"
        if (Test-Path $out) {
            try { Remove-Item -Recurse -Force $out }
            catch { throw "Could not clear $out. Close AIMarkLens if it is running from there, then build again." }
        }
        New-Item -ItemType Directory -Force $out | Out-Null
        Set-Content -Path $buildInfo -Encoding ASCII -Value @(
            '"""Written by build.ps1; records which edition this build is."""',
            "EDITION = `"$lower`"",
            "BUILD_DATE = `"$(Get-Date -Format 'yyyy-MM-dd HH:mm')`"")
        $env:AIMarkLens_EDITION = $lower

        Write-Host "[$ed] Building the application folder..."
        Remove-Item Env:AIMarkLens_PORTABLE -ErrorAction SilentlyContinue
        & $python -m PyInstaller --noconfirm --clean --log-level WARN --distpath $out --workpath "build\$lower\folder" AIMarkLens.spec
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($ed folder build)." }
        Test-Build "$out\AIMarkLens\AIMarkLens.exe"

        if (-not $NoPortable) {
            Write-Host "[$ed] Building the portable file..."
            $env:AIMarkLens_PORTABLE = "1"
            & $python -m PyInstaller --noconfirm --log-level WARN --distpath $out --workpath "build\$lower\portable" AIMarkLens.spec
            $code = $LASTEXITCODE
            Remove-Item Env:AIMarkLens_PORTABLE -ErrorAction SilentlyContinue
            if ($code -ne 0) { throw "PyInstaller failed ($ed portable build)." }
            $built = if ($ed -eq "Public") { "AIMarkLens-Portable.exe" } else { "AIMarkLens-Personal-Portable.exe" }
            $portable = "$out\AIMarkLens-$version-$ed-Portable.exe"
            Move-Item -Force "$out\$built" $portable
            Test-Build $portable
        }

        if (-not $NoInstaller) {
            Write-Host "[$ed] Compiling the installer..."
            & $iscc /Q "/DEdition=$ed" "/DAppVersion=$version" "/DSourceDir=$out\AIMarkLens" "/DOutputDir=$out" installer\AIMarkLens.iss
            if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed ($ed installer)." }
        }
    }
}
finally {
    Remove-Item $buildInfo -ErrorAction SilentlyContinue
    Remove-Item Env:AIMarkLens_EDITION, Env:AIMarkLens_PORTABLE -ErrorAction SilentlyContinue
}

$releases = Get-ChildItem .dist -Recurse -File -Include "AIMarkLens-*-Setup.exe", "AIMarkLens-*-Portable.exe"
$sums = $releases | ForEach-Object { "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name }
Set-Content -Path ".dist\SHA256SUMS.txt" -Encoding ASCII -Value $sums

Write-Host ""
Write-Host "Done. Release files:"
$releases | ForEach-Object { Write-Host ("  {0,-48} {1,8:N1} MB" -f $_.Name, ($_.Length / 1MB)) }
Write-Host "Checksums: $(Resolve-Path .dist\SHA256SUMS.txt)"
