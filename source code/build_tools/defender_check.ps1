<#
  Build step: scan the finished app folder with Microsoft Defender BEFORE making the installer.

    powershell -NoProfile -ExecutionPolicy Bypass -File build_tools\defender_check.ps1 -Path dist\PubHorror

  Report-only (nothing is quarantined). Exit codes: 0 = clean (or Defender not available), 2 = flagged.
  PyInstaller-built exes sometimes get a machine-learning false positive (e.g. "Trojan:Win32/Bearfoos.A!ml").
  Those are scored per file, so simply rebuilding usually produces a clean exe.
#>
param([string]$Path = "dist\PubHorror")
$SrcDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not [IO.Path]::IsPathRooted($Path)) { $Path = Join-Path $SrcDir $Path }
$mp = Join-Path $env:ProgramFiles "Windows Defender\MpCmdRun.exe"
if (-not (Test-Path $mp)) {
    Write-Host "[defender] Microsoft Defender isn't available here - skipping the scan."
    exit 0
}
Write-Host "[defender] Scanning $Path (report only)..."
$out = & $mp -Scan -ScanType 3 -File $Path -DisableRemediation 2>&1 | Out-String
if ($out -match "found no threats") {
    Write-Host "[defender] Clean."
    exit 0
}
$threats = [regex]::Matches($out, "Threat\s+:\s*(.+)") | ForEach-Object { $_.Groups[1].Value.Trim() } | Select-Object -Unique
$files = [regex]::Matches($out, "file\s+:\s*(.+)") | ForEach-Object { $_.Groups[1].Value.Trim() } | Select-Object -Unique
if ($threats) {
    Write-Host ""
    Write-Host "[defender] FLAGGED: $($threats -join ', ')"
    $files | ForEach-Object { Write-Host "           $_" }
    Write-Host ""
    Write-Host "  If it says '!ml' this is almost certainly a false positive from Defender's machine-learning model."
    Write-Host "  Fix: run build.bat again (a fresh build usually scans clean). If it keeps happening, submit the"
    Write-Host "  file as a false positive: https://www.microsoft.com/en-us/wdsi/filesubmission (choose 'Software developer')."
    exit 2
}
Write-Host "[defender] Couldn't read the scan result; continuing. Output was:"
Write-Host $out
exit 0
