<#
.SYNOPSIS
    Shared helper: locates an installed Blender. Dot-source it, don't run it.

.DESCRIPTION
    Used by install_debugpy.ps1 and launch_blender_debug.ps1 so both agree on
    which Blender they are talking about.
#>

function Get-BlenderInstall {
    <#
    .SYNOPSIS
        Returns a [pscustomobject] with Version, Root, Exe and PythonExe for an
        installed Blender, or throws if none can be found.

    .PARAMETER BlenderVersion
        Version to look for, e.g. "5.2". Defaults to the newest one installed.
    #>
    [CmdletBinding()]
    param([string]$BlenderVersion)

    $roots = @(
        (Join-Path $env:ProgramFiles "Blender Foundation"),
        (Join-Path ${env:ProgramFiles(x86)} "Blender Foundation"),
        (Join-Path $env:ProgramFiles "Steam\steamapps\common"),
        (Join-Path ${env:ProgramFiles(x86)} "Steam\steamapps\common")
    ) | Where-Object { $_ -and (Test-Path $_) }

    $candidates = foreach ($root in $roots) {
        Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^Blender(\s+(?<ver>\d+\.\d+))?$' } |
            ForEach-Object {
                $exe = Join-Path $_.FullName "blender.exe"
                if (-not (Test-Path $exe)) { return }
                # The version is also the name of the folder holding Blender's
                # Python, which is more reliable than the install folder name
                # (Steam's is just "Blender").
                $versionDir = Get-ChildItem $_.FullName -Directory -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match '^\d+\.\d+$' -and (Test-Path (Join-Path $_.FullName "python")) } |
                    Sort-Object { [version]$_.Name } -Descending |
                    Select-Object -First 1
                if (-not $versionDir) { return }
                [pscustomobject]@{
                    Version   = $versionDir.Name
                    Root      = $_.FullName
                    Exe       = $exe
                    PythonExe = Join-Path $versionDir.FullName "python\bin\python.exe"
                }
            }
    }

    if ($BlenderVersion) {
        $match = $candidates | Where-Object { $_.Version -eq $BlenderVersion } | Select-Object -First 1
        if (-not $match) {
            $found = ($candidates | ForEach-Object { $_.Version }) -join ", "
            if (-not $found) { $found = "none" }
            throw "Blender $BlenderVersion was not found (installed versions: $found). Pass an explicit path instead."
        }
        return $match
    }

    $newest = $candidates | Sort-Object { [version]$_.Version } -Descending | Select-Object -First 1
    if (-not $newest) {
        throw "Could not find a Blender installation. Pass -BlenderExe / -BlenderPython explicitly."
    }
    return $newest
}
