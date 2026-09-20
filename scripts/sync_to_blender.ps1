<#
.SYNOPSIS
    Deploys the io_scene_valvesource add-on straight from this repo into a local
    Blender install, so you don't have to build a zip and use Install from Disk
    every time you want to test a change.

.PARAMETER BlenderVersion
    Blender version folder to target, e.g. "5.2". Defaults to the newest version
    folder found under %APPDATA%\Blender Foundation\Blender.

.PARAMETER AddonsPath
    Explicit target add-ons folder. Overrides -BlenderVersion auto-detection;
    use this for portable installs or non-default config locations.

.PARAMETER Symlink
    Link instead of copy. Requires an elevated prompt or Windows Developer Mode.
    Recommended while actively developing: once linked, Blender picks up file
    edits after Edit > Preferences > Add-ons > Reload (or Blender restart) with
    no need to re-run this script. Without this switch the add-on folder is
    mirrored with robocopy, which needs no special privileges but has to be
    re-run after every change.

.EXAMPLE
    .\scripts\sync_to_blender.ps1
    Mirrors into the newest installed Blender version's add-ons folder.

.EXAMPLE
    .\scripts\sync_to_blender.ps1 -BlenderVersion 5.2 -Symlink
    Symlinks into Blender 5.2's add-ons folder specifically.
#>
[CmdletBinding()]
param(
    [string]$BlenderVersion,
    [string]$AddonsPath,
    [switch]$Symlink
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot "io_scene_valvesource"

if (-not (Test-Path $source)) {
    throw "Could not find io_scene_valvesource next to this script (looked in $source)."
}

if (-not $AddonsPath) {
    $blenderConfigRoot = Join-Path $env:APPDATA "Blender Foundation\Blender"
    if (-not (Test-Path $blenderConfigRoot)) {
        throw "No Blender configuration folder found at $blenderConfigRoot. Pass -AddonsPath explicitly."
    }

    if ($BlenderVersion) {
        $versionDir = Join-Path $blenderConfigRoot $BlenderVersion
        if (-not (Test-Path $versionDir)) {
            throw "Blender version folder not found: $versionDir"
        }
    } else {
        $newest = Get-ChildItem $blenderConfigRoot -Directory |
            Where-Object { $_.Name -match '^\d+\.\d+$' } |
            Sort-Object { [version]$_.Name } -Descending |
            Select-Object -First 1
        if (-not $newest) {
            throw "Could not auto-detect a Blender version under $blenderConfigRoot. Pass -BlenderVersion or -AddonsPath explicitly."
        }
        $versionDir = $newest.FullName
    }

    $AddonsPath = Join-Path $versionDir "scripts\addons"
}

New-Item -ItemType Directory -Force -Path $AddonsPath | Out-Null
$target = Join-Path $AddonsPath "io_scene_valvesource"

if ($Symlink) {
    $existing = Get-Item $target -ErrorAction SilentlyContinue
    if ($existing -and -not $existing.LinkType) {
        throw "$target already exists as a real folder, not a symlink. Remove it manually first (it may be a leftover copy from a previous non-symlink sync)."
    }
    if ($existing) { Remove-Item $target -Force }
    New-Item -ItemType SymbolicLink -Path $target -Target $source | Out-Null
    Write-Host "Linked $target -> $source"
} else {
    robocopy $source $target /MIR /XD __pycache__ /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }
    Write-Host "Mirrored $source -> $target"
}

Write-Host "Done. In Blender: Edit > Preferences > Add-ons > search 'Source Tools' > enable it, or press F8 (Reload Scripts) if it's already enabled."
