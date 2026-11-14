<#
  PubHorror build step: put the sharing sidecar (with its own node.exe) into the PyInstaller output.

    powershell -NoProfile -ExecutionPolicy Bypass -File build_tools\prepare_sidecar.ps1 -Dist dist\PubHorror

  What it does, failing the build on any problem:
    1. Downloads the pinned official Node.js Windows zip from nodejs.org (cached in .node_cache\, reused next time)
    2. Checks it against nodejs.org's SHA256SUMS, then checks node.exe's Authenticode signature (OpenJS Foundation)
    3. Copies the sidecar code to <Dist>\sidecar and installs ONLY its production npm packages from the lockfile
       (npm ci --omit=dev --ignore-scripts: exact versions, no install scripts run)
    4. Refuses any npm package that isn't permissively licensed or isn't listed in THIRD_PARTY_NOTICES.txt
    5. Copies node.exe + Node's LICENSE, and runs a quick smoke test
#>
param(
    [string]$Dist = "dist\PubHorror",
    [string]$NodeVersion = "24.11.0"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"          # Invoke-WebRequest is very slow with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$SrcDir   = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)   # "source code"
$Dist     = if ([IO.Path]::IsPathRooted($Dist)) { $Dist } else { Join-Path $SrcDir $Dist }
$Sidecar  = Join-Path $SrcDir "sidecar"
$Notices  = Join-Path $SrcDir "THIRD_PARTY_NOTICES.txt"
$Cache    = Join-Path $SrcDir ".node_cache\v$NodeVersion"
$ZipName  = "node-v$NodeVersion-win-x64.zip"
$BaseUrl  = "https://nodejs.org/dist/v$NodeVersion"
$Allowed  = @("MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "0BSD")

function Step($msg) { Write-Host "[sidecar] $msg" }

if (-not (Test-Path (Join-Path $Dist "PubHorror.exe"))) { throw "$Dist\PubHorror.exe not found - run PyInstaller first." }

# ---------------------------------------------------------------- 1+2. official Node.js, verified
New-Item -ItemType Directory -Force $Cache | Out-Null
$Sums = Join-Path $Cache "SHASUMS256.txt"
$Zip  = Join-Path $Cache $ZipName
if (-not (Test-Path $Sums)) { Step "Downloading SHASUMS256.txt for Node $NodeVersion"; Invoke-WebRequest "$BaseUrl/SHASUMS256.txt" -OutFile $Sums -UseBasicParsing }
$line = Get-Content $Sums | Where-Object { $_ -match ("\s" + [regex]::Escape($ZipName) + "$") } | Select-Object -First 1
if (-not $line) { throw "SHASUMS256.txt has no entry for $ZipName" }
$expected = ($line -split "\s+")[0].ToLower()
if (-not (Test-Path $Zip)) { Step "Downloading $ZipName (~30 MB)"; Invoke-WebRequest "$BaseUrl/$ZipName" -OutFile $Zip -UseBasicParsing }
$actual = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) {
    Remove-Item $Zip -Force
    throw "Checksum mismatch for $ZipName (expected $expected, got $actual). The bad download was deleted; run the build again."
}
Step "Checksum OK ($expected)"

$Extract = Join-Path $Cache "extract"
if (Test-Path $Extract) { Remove-Item $Extract -Recurse -Force }
New-Item -ItemType Directory -Force $Extract | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [IO.Compression.ZipFile]::OpenRead($Zip)
try {
    foreach ($name in @("node.exe", "LICENSE")) {
        $entry = $z.Entries | Where-Object { $_.FullName -eq "node-v$NodeVersion-win-x64/$name" }
        if (-not $entry) { throw "$name missing from $ZipName" }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $Extract $name), $true)
    }
} finally { $z.Dispose() }
$NodeExe = Join-Path $Extract "node.exe"
$sig = Get-AuthenticodeSignature $NodeExe
if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "O=OpenJS Foundation") {
    throw "node.exe signature check failed: $($sig.Status) / $($sig.SignerCertificate.Subject)"
}
Step "Signature OK (OpenJS Foundation)"

# ---------------------------------------------------------------- 3. stage the sidecar
$Out = Join-Path $Dist "sidecar"
if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
New-Item -ItemType Directory -Force $Out | Out-Null
foreach ($f in @("server.js", "package.json", "package-lock.json")) { Copy-Item (Join-Path $Sidecar $f) $Out }
foreach ($d in @("src", "public")) { Copy-Item (Join-Path $Sidecar $d) (Join-Path $Out $d) -Recurse }

Step "Installing production npm packages from package-lock.json"
Push-Location $Out
try {
    & npm.cmd ci --omit=dev --ignore-scripts --no-audit --no-fund --loglevel=error
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed (exit $LASTEXITCODE)" }
} finally { Pop-Location }

# ---------------------------------------------------------------- 4. license gate
$noticeText = Get-Content $Notices -Raw
$problems = @()
$pkgs = Get-ChildItem (Join-Path $Out "node_modules") -Recurse -Filter package.json |
        Where-Object { $_.Directory.Parent.Name -eq "node_modules" -or
                       ($_.Directory.Parent.Name -like "@*" -and $_.Directory.Parent.Parent.Name -eq "node_modules") }
foreach ($p in $pkgs) {
    $j = Get-Content $p.FullName -Raw | ConvertFrom-Json
    if (-not $j.name -or -not $j.version) { continue }                        # nested non-package json
    $lic = "$($j.license)"
    if ($Allowed -notcontains $lic) { $problems += "$($j.name)@$($j.version): license '$lic' is not on the allowed list" }
    if ($noticeText -notmatch [regex]::Escape("$($j.name) $($j.version)")) { $problems += "$($j.name)@$($j.version): not listed in THIRD_PARTY_NOTICES.txt" }
    $hasLicenseFile = Get-ChildItem $p.Directory.FullName -File | Where-Object { $_.Name -match "^licen[cs]e" }
    if (-not $hasLicenseFile) { $problems += "$($j.name)@$($j.version): no LICENSE file to ship" }
    Step ("  {0,-28} {1}" -f "$($j.name)@$($j.version)", $lic)
}
if ($problems.Count) { throw ("License check failed:`n  " + ($problems -join "`n  ")) }

# ---------------------------------------------------------------- 5. node.exe + license, smoke test
Copy-Item $NodeExe (Join-Path $Out "node.exe")
$Lic = Join-Path $Dist "licenses"
New-Item -ItemType Directory -Force $Lic | Out-Null
Copy-Item (Join-Path $Extract "LICENSE") (Join-Path $Lic "node-LICENSE.txt")

$smoke = & (Join-Path $Out "node.exe") (Join-Path $Out "server.js") --discover --timeout 500
if ($LASTEXITCODE -ne 0 -or "$smoke" -notmatch '"hosts"') { throw "Smoke test failed: $smoke" }
$mb = [math]::Round(((Get-ChildItem $Out -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
Step "Done: $Out ($mb MB, Node $NodeVersion)"
