[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $SourceRoot,
    [Parameter(Mandatory = $true)] [string] $Template,
    [Parameter(Mandatory = $true)] [string] $Output,
    [ValidateSet('Release', 'SyncOnlyRelease')] [string] $Configuration = 'Release'
)

$ErrorActionPreference = 'Stop'

function Require-File([string] $Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file is missing: $Path"
    }
}

function Require-Command([string] $Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Required command is unavailable: $Name"
    }
    return $command.Source
}

$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$template = (Resolve-Path -LiteralPath $Template).Path
$output = [System.IO.Path]::GetFullPath($Output)
$solution = Join-Path $source 'ValheimVRMod\ValheimVRMod.sln'
$project = Join-Path $source 'ValheimVRMod\ValheimVRMod.csproj'
$controlPatches = Join-Path $source 'ValheimVRMod\Patches\ControlPatches.cs'

Require-File $solution
Require-File $project
Require-File $controlPatches
Require-File $template
$msbuild = Require-Command 'MSBuild.exe'

$guard = Get-Content -LiteralPath $controlPatches -Raw
if ($guard -notmatch '\[HarmonyPrepare\]' -or $guard -notmatch 'return\s+!VHVRConfig\.NonVrPlayer\(\)') {
    throw 'Flat dodge guard is absent from ControlPatches.cs.'
}

& $msbuild $solution "/p:Configuration=$Configuration" '/p:SkipReleasePackaging=true' '/p:Platform=AnyCPU' '/m'
if ($LASTEXITCODE -ne 0) {
    throw "MSBuild failed with exit code $LASTEXITCODE."
}

$dll = Join-Path $source "ValheimVRMod\bin\$Configuration\net46\ValheimVRMod.dll"
Require-File $dll

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("valheimvr-flat-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    [System.IO.Compression.ZipFile]::ExtractToDirectory($template, $temp)
    $pluginDir = Join-Path $temp 'BepInEx\plugins'
    $config = Join-Path $temp 'BepInEx\config\org.bepinex.plugins.valheimvrmod.cfg'
    Require-File $config
    if ((Get-Content -LiteralPath $config -Raw) -notmatch 'nonVrPlayer\s*=\s*true') {
        throw 'Template is not a Flat ValheimVR companion: nonVrPlayer must be true.'
    }
    Copy-Item -LiteralPath $dll -Destination (Join-Path $pluginDir 'ValheimVRMod.dll') -Force
    Remove-Item -LiteralPath (Join-Path $pluginDir 'ValheimVRFlatDodgePatchFix.dll') -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Output -Force -ErrorAction SilentlyContinue
    [System.IO.Compression.ZipFile]::CreateFromDirectory($temp, $Output, [System.IO.Compression.CompressionLevel]::Optimal, $false)
}
finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}

$hash = (Get-FileHash -LiteralPath $Output -Algorithm SHA256).Hash.ToLowerInvariant()
[pscustomobject]@{
    artifact = $Output
    sha256 = $hash
    valheimvr_dll_sha256 = (Get-FileHash -LiteralPath $dll -Algorithm SHA256).Hash.ToLowerInvariant()
    configuration = $Configuration
} | ConvertTo-Json -Compress
