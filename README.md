# Blender Source Tools

A Blender add-on for importing and exporting Valve's Source Engine formats: SMD/VTA and DMX, plus QC-driven compiling.

Original project by Tom Edwards ([steamreview.org](http://steamreview.org)), distributed under GPL-2.0-or-later. This is a personal working copy of the add-on source.

## Installing the add-on (as an end user)

1. Build (see below) or download `blender_source_tools_<version>.zip`.
2. In Blender: **Edit > Preferences > Add-ons > Install...**, pick the zip.
3. Enable "Import-Export: Blender Source Tools" in the add-on list.

## Building the release zip

```
python make_zip.py
```

Reads the version from `io_scene_valvesource/__init__.py`'s `bl_info`, and writes `blender_source_tools_<version>.zip` **one directory above the repo root**. This is the same zip you'd hand to Preferences > Add-ons > Install.

## Deploying straight to your local Blender install (development)

For iterating on the code, `scripts/sync_to_blender.ps1` copies (or symlinks) `io_scene_valvesource/` directly into your Blender add-ons folder, so you don't need to rebuild a zip and reinstall it after every change.

```powershell
# Mirror into the newest installed Blender version's add-ons folder
.\scripts\sync_to_blender.ps1

# Target a specific version
.\scripts\sync_to_blender.ps1 -BlenderVersion 5.2

# Symlink instead of copy (recommended for active dev: edits show up after
# Blender's "Reload Scripts", no need to re-run the script every time).
# Creating a symlink needs an elevated prompt or Windows Developer Mode.
.\scripts\sync_to_blender.ps1 -Symlink
```

It auto-detects the newest version under `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons`. Use `-AddonsPath` to point at a portable/custom Blender install instead.

If you're using VS Code, the same script is wired up as tasks (**Terminal > Run Task**): *Sync to Blender*, *Sync to Blender (symlink)*, and *Build Release Zip*.

After syncing, enable the add-on once in Preferences > Add-ons, then use **F8 (Reload Scripts)** in Blender to pick up further changes without restarting.

## Repository layout

```
io_scene_valvesource/   the add-on itself (this folder is what gets zipped/installed)
  __init__.py              add-on registration, bl_info, scene properties
  import_smd.py            SMD/VTA/DMX importer
  export_smd.py            SMD/VTA/DMX exporter, QC compiling
  datamodel.py             DMX (Valve Datamodel) reader/writer
  flex.py                  shape key / flex controller tools
  GUI.py                   panels and menus
  update.py                built-in "check for update" operator (see note below)
  utils.py                 shared helpers
Tests/                   unit tests, run against Blender-as-a-Python-module
scripts/
  sync_to_blender.ps1      deploys io_scene_valvesource/ into a local Blender install
make_zip.py              builds a versioned release zip from io_scene_valvesource/
pyrightconfig.json       Pylance/Pyright settings (bpy's dynamic typing needs some checks disabled)
requirements.txt         dev-only deps: bpy stubs for the language server, bpy itself for tests
```

## Editor / language server setup

The add-on itself has no third-party runtime dependencies (only stdlib plus `bpy`/`bmesh`/`mathutils`, which Blender provides). `requirements.txt` covers the dev-only tooling:

```
pip install -r requirements.txt
```

This installs `fake-bpy-module-5.2` (type stubs so Pyright/Pylance can resolve Blender's API — bump the version number when you upgrade Blender) and `bpy` (Blender as a Python module, needed to run the tests below).

## Running the tests

Tests run against ["Blender as a Python module"](https://wiki.blender.org/wiki/Building_Blender/Other/BlenderAsPyModule) (the `bpy` pip package from `requirements.txt`) rather than a full Blender install, so they can run headless from a plain Python interpreter:

```
python -m unittest discover -s Tests
```

Some tests additionally compare against Source SDK content and only run if the `SOURCESDK` environment variable is set.

## Compatibility

- Blender 4.1+

## Drift from original repository

I removed the pyproject from VS, and an useless script which uses a hard coded path in `interactive_startup.py`. The project isn't bounded to any IDE.
