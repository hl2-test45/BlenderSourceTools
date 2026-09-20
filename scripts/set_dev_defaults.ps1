<#
.SYNOPSIS
    Writes io_scene_valvesource/dev_defaults.json, which the add-on reads at
    startup to prefill Scene Properties > Export Path / Engine Path / Game
    Path / Material Path and Link VMT Textures > Game Content / Converted Textures.

.DESCRIPTION
    Those paths are machine- or mod-specific (Steam library location, local
    mod folder, export destination, material subfolder, ...), so they must
    never be hardcoded in the tracked Python source or end up in the release
    zip. This script writes them to a JSON file inside io_scene_valvesource/
    instead. That file is gitignored and is explicitly skipped by
    make_zip.py; it's only picked up by sync_to_blender.ps1 for local
    testing.

    Run it again any time you want to change your local defaults. Omitted
    parameters keep their previous value in the file (empty if none set).

.PARAMETER ExportPath
    Default value for Scene Properties > Export Path.

.PARAMETER EnginePath
    Default value for Scene Properties > Engine Path.

.PARAMETER GamePath
    Default value for Scene Properties > Game Path.

.PARAMETER MaterialPath
    Default value for Scene Properties > Material Path.

.PARAMETER VmtGameRoot
    Default value for Link VMT Textures > Game Content (extracted VPKs).

.PARAMETER VmtPngRoot
    Default value for Link VMT Textures > Converted Textures (VTFEdit output).

.EXAMPLE
    .\scripts\set_dev_defaults.ps1 `
        -ExportPath "C:\Users\Thomas\Documents\Projets\test_45\source-sdk-2013\sp\game\mod_episodic\modelsrc" `
        -EnginePath "C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin" `
        -GamePath "C:\Program Files (x86)\Steam\steamapps\sourcemods\mapbase_hl2" `
        -MaterialPath "C:\Users\Thomas\Documents\Projets\test_45\source_ref\game\materials\models"
#>
[CmdletBinding()]
param(
    [string]$ExportPath,
    [string]$EnginePath,
    [string]$GamePath,
    [string]$MaterialPath,
    [string]$VmtGameRoot,
    [string]$VmtPngRoot
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$target = Join-Path $repoRoot "io_scene_valvesource\dev_defaults.json"

$config = [ordered]@{
    export_path   = ""
    engine_path   = ""
    game_path     = ""
    material_path = ""
    vmt_game_root = ""
    vmt_png_root  = ""
}

if (Test-Path $target) {
    $existing = Get-Content $target -Raw | ConvertFrom-Json
    foreach ($key in $config.Keys | ForEach-Object { $_ }) {
        if ($existing.PSObject.Properties.Name -contains $key) {
            $config[$key] = $existing.$key
        }
    }
}

if ($PSBoundParameters.ContainsKey('ExportPath')) { $config.export_path = $ExportPath }
if ($PSBoundParameters.ContainsKey('EnginePath')) { $config.engine_path = $EnginePath }
if ($PSBoundParameters.ContainsKey('GamePath')) { $config.game_path = $GamePath }
if ($PSBoundParameters.ContainsKey('MaterialPath')) { $config.material_path = $MaterialPath }
if ($PSBoundParameters.ContainsKey('VmtGameRoot')) { $config.vmt_game_root = $VmtGameRoot }
if ($PSBoundParameters.ContainsKey('VmtPngRoot')) { $config.vmt_png_root = $VmtPngRoot }

$config | ConvertTo-Json | Set-Content -Path $target -Encoding utf8

Write-Host "Wrote $target"
Write-Host "Re-run the 'Sync to Blender' task to push it to your Blender add-ons folder."
