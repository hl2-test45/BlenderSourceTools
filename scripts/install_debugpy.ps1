<#
.SYNOPSIS
    Installs debugpy into Blender's Python, which is what lets VS Code attach
    to the add-on while Blender runs it.

.DESCRIPTION
    debugpy has to live in the interpreter that executes the add-on, i.e.
    Blender's own Python -- not this repository's .venv (which exists only for
    the bpy type stubs and the tests, and is usually a different Python
    version entirely).

    By default it is installed with pip's --target into

        %APPDATA%\Blender Foundation\Blender\<version>\scripts\modules

    a folder Blender puts on sys.path at startup. That needs no administrator
    rights and leaves the Blender installation untouched. Use -System to
    install into Blender's own site-packages instead (elevated prompt needed
    if Blender lives in Program Files).

.PARAMETER BlenderVersion
    Blender version to install for, e.g. "5.2". Defaults to the newest one
    found in Program Files.

.PARAMETER BlenderPython
    Explicit path to Blender's python.exe, for portable or non-standard
    installs. Usually <blender>\<version>\python\bin\python.exe.

.PARAMETER TargetPath
    Explicit folder to install into. Overrides the default scripts\modules
    location. Point BST_DEBUGPY_PATH at it if it is somewhere Blender does not
    scan.

.PARAMETER System
    Install into Blender's bundled site-packages instead of scripts\modules.

.EXAMPLE
    .\scripts\install_debugpy.ps1

.EXAMPLE
    .\scripts\install_debugpy.ps1 -BlenderVersion 5.2
#>
[CmdletBinding()]
param(
    [string]$BlenderVersion,
    [string]$BlenderPython,
    [string]$TargetPath,
    [switch]$System
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "blender_paths.ps1")

if (-not $BlenderPython) {
    $install = Get-BlenderInstall -BlenderVersion $BlenderVersion
    $BlenderPython = $install.PythonExe
    if (-not $BlenderVersion) { $BlenderVersion = $install.Version }
}

if (-not (Test-Path $BlenderPython)) {
    throw "Blender's Python was not found at $BlenderPython. Pass -BlenderPython explicitly."
}

Write-Host "Using Blender's Python: $BlenderPython"

# Blender ships pip but does not always bootstrap it.
& $BlenderPython -m pip --version
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip is missing; bootstrapping it with ensurepip."
    & $BlenderPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw "ensurepip failed with exit code $LASTEXITCODE" }
}

if ($System) {
    & $BlenderPython -m pip install --upgrade debugpy
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed with exit code $LASTEXITCODE. Installing into Blender's own site-packages usually needs an elevated prompt; drop -System to install into scripts\modules instead."
    }
    Write-Host "Installed debugpy into Blender's site-packages."
} else {
    if (-not $TargetPath) {
        if (-not $BlenderVersion) { throw "Could not determine the Blender version; pass -BlenderVersion or -TargetPath." }
        $TargetPath = Join-Path $env:APPDATA "Blender Foundation\Blender\$BlenderVersion\scripts\modules"
    }
    New-Item -ItemType Directory -Force -Path $TargetPath | Out-Null
    & $BlenderPython -m pip install --upgrade --target $TargetPath debugpy
    if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit code $LASTEXITCODE" }
    Write-Host "Installed debugpy into $TargetPath"
}

Write-Host "Done. Next: run the 'Blender: Launch with debug listener' VS Code task, or press F5."
