<#
.SYNOPSIS
    Starts Blender with a debugpy listener open, ready for VS Code to attach.

.DESCRIPTION
    Runs scripts/blender_debug_listen.py inside Blender via --python. That
    script opens the debug port, waits a few seconds for VS Code to attach,
    and then enables io_scene_valvesource straight from this repository, so
    breakpoints set in the editor bind to the code Blender actually runs.

    Blender's console output (including anything the add-on prints) is
    streamed into this terminal. Close Blender to end the script.

    Requires debugpy in Blender's Python: run scripts/install_debugpy.ps1 once.

.PARAMETER BlenderExe
    Explicit path to blender.exe, for portable or non-standard installs.

.PARAMETER BlenderVersion
    Blender version to launch, e.g. "5.2". Defaults to the newest installed.

.PARAMETER Port
    Port to listen on. Must match .vscode/launch.json. Defaults to 5678.

.PARAMETER WaitSeconds
    How long Blender waits for VS Code to attach before finishing startup.
    Only matters for breakpoints in module-level code and register().

.PARAMETER NoWait
    Don't wait for VS Code at all; same as -WaitSeconds 0.

.PARAMETER Installed
    Leave Blender's own copy of the add-on in charge instead of loading it
    from this repository. Use the matching "installed copy" configuration in
    launch.json, which maps the paths back to the repository.

.PARAMETER BlendFile
    .blend file to open on startup.

.PARAMETER BlenderArgs
    Anything else is forwarded to Blender verbatim.

.EXAMPLE
    .\scripts\launch_blender_debug.ps1

.EXAMPLE
    .\scripts\launch_blender_debug.ps1 -BlendFile .\Tests\scene.blend -Port 5679
#>
[CmdletBinding()]
param(
    [string]$BlenderExe,
    [string]$BlenderVersion,
    [int]$Port = 5678,
    [double]$WaitSeconds = 10,
    [switch]$NoWait,
    [switch]$Installed,
    [string]$BlendFile,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$BlenderArgs
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "blender_paths.ps1")

$repoRoot = Split-Path -Parent $PSScriptRoot
$bootstrap = Join-Path $PSScriptRoot "blender_debug_listen.py"

if (-not (Test-Path $bootstrap)) {
    throw "Could not find $bootstrap."
}

if (-not $BlenderExe) {
    $BlenderExe = (Get-BlenderInstall -BlenderVersion $BlenderVersion).Exe
}
if (-not (Test-Path $BlenderExe)) {
    throw "blender.exe was not found at $BlenderExe. Pass -BlenderExe explicitly."
}

if ($NoWait) { $WaitSeconds = 0 }

$env:BST_REPO_ROOT = $repoRoot
$env:BST_DEBUG_PORT = $Port
$env:BST_DEBUG_WAIT = $WaitSeconds
$env:BST_DEBUG_ADDON = if ($Installed) { "installed" } else { "repo" }

$argList = @()
if ($BlendFile) {
    if (-not (Test-Path $BlendFile)) { throw "Blend file not found: $BlendFile" }
    $argList += (Resolve-Path $BlendFile).Path
}
$argList += @("--python", $bootstrap)
if ($BlenderArgs) { $argList += $BlenderArgs }

# The VS Code task watches for this line to know the launch has begun.
Write-Host "Blender debug launcher: starting $BlenderExe (debug port $Port)"
& $BlenderExe @argList
$exitCode = $LASTEXITCODE

Write-Host "Blender exited with code $exitCode"
exit $exitCode
