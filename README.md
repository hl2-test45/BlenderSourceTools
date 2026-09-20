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

For active development you may not need this at all: the debugging setup below runs the add-on directly from this repository, no deployment step involved.

After syncing, enable the add-on once in Preferences > Add-ons, then use **F8 (Reload Scripts)** in Blender to pick up further changes without restarting.

## Debugging in VS Code

You can set breakpoints in `io_scene_valvesource/*.py` and have them hit while Blender runs the add-on: importing a QC, exporting a mesh, drawing a panel. Blender opens a [debugpy](https://github.com/microsoft/debugpy) listener on startup and VS Code attaches to it over a local socket.

### One-time setup

1. Install the VS Code **Python** and **Python Debugger** extensions.
2. Install debugpy into Blender's Python (*not* into `.venv` — the debugger has to live in the interpreter that actually executes the add-on, and Blender ships its own):

   ```powershell
   .\scripts\install_debugpy.ps1
   ```

   It installs into `%APPDATA%\Blender Foundation\Blender\<version>\scripts\modules`, which Blender puts on `sys.path` at startup. That needs no administrator rights and leaves the Blender installation untouched. `-System` installs into Blender's own `site-packages` instead (elevated prompt required), and `-BlenderVersion` / `-BlenderPython` target a specific install. The same thing is wired up as the *Install debugpy into Blender* task.

That's it. If the add-on is also installed and enabled in Blender the usual way (zip or *Sync to Blender*), leave it that way: the debug launcher unloads that copy and swaps in the repository version **for the debug session only**. Your preferences are not modified, so the next time you start Blender normally you get the installed copy back as before.

### Debugging

Press **F5** and pick **Blender: Launch & Attach**. That runs `scripts/launch_blender_debug.ps1`, which:

- starts Blender with `--python scripts/blender_debug_listen.py`,
- opens the debug port (5678 by default) and waits up to 10 seconds for VS Code to attach, so breakpoints in module-level code and `register()` are hit too,
- enables `io_scene_valvesource` **straight from this repository**.

That last point is what makes breakpoints work with no extra configuration: Blender executes the very files you have open, so their paths match and the debugger binds to them directly. It also means `dev_defaults.json` and any edit you make are picked up without running *Sync to Blender* at all.

Blender's console output is streamed into the VS Code task terminal, so `print()` and tracebacks show up there.

To pick up code changes without restarting Blender, use **F8 (Reload Scripts)**. The debugger stays attached and breakpoints rebind to the reloaded modules.

### The other launch configurations

| Configuration | When to use it |
| --- | --- |
| **Blender: Launch & Attach** | The usual one. Starts Blender for you and attaches. |
| **Blender: Attach to running instance** | A Blender you started yourself. Open `scripts/blender_debug_listen.py` in its Text Editor (Text > Open) and press Run Script, then attach. Use this if the launch task ever fails to hand over. |
| **Blender: Attach (installed copy)** | Debug the copy deployed by *Sync to Blender* rather than the repository. Launch with `.\scripts\launch_blender_debug.ps1 -Installed`. Because Blender reports the add-ons folder path, this configuration maps it back to the repository via `pathMappings` — **update the Blender version in that path** to match your install. |
| **Python: Unit tests** | Unrelated to Blender-the-application: debugs the tests below against the `bpy` pip module. |

### Options and troubleshooting

`scripts/launch_blender_debug.ps1` takes `-Port` (match it in `launch.json`), `-BlendFile` to open a scene on startup, `-BlenderVersion` / `-BlenderExe` to pick an install, `-NoWait` to skip the attach wait, and forwards anything else to Blender.

- **"debugpy is not importable from Blender's Python"** — step 2 above was skipped, or it installed for a different Blender version than the one being launched. Pass the same `-BlenderVersion` to both scripts.
- **Breakpoints show as hollow / "not verified"** — Blender is running a different copy of the file than the one you set the breakpoint in. Check the `add-on running from ...` line in the task terminal; it should point at this repository.
- **F5 hangs on "Blender: Launch with debug listener"** — VS Code is waiting for the listener's startup line and never saw it. Look at the task terminal for the real error, then fall back to *Blender: Attach to running instance*.
- **Port already in use** — a previous Blender is still running, or still holding the socket. Close it, or use a different `-Port`.
- **A burst of `has been registered before, unregistering previous` messages at startup** — the copy installed in Blender predates the fix that lets the add-on unregister completely (vertex-map operators used to be registered on import). Harmless, but run *Sync to Blender* once to update the installed copy and it goes away.

> `.vscode/` is gitignored in this repository, so `launch.json` and `tasks.json` are local to your checkout and won't come along with a fresh clone.

## Linking Source textures to imported materials

Importing an SMD/DMX/QC only creates Blender materials *named* after the Source material; the add-on doesn't read VMT or VTF files during import. **Link VMT Textures** (Scene Properties > Source Engine Export, or File > Import > Source Engine Textures) fills them in afterwards. It resolves each material name through the game's VMT (`materials/<$cdmaterials>/<name>.vmt` → `$basetexture`, following `patch` includes) and loads a PNG/TGA conversion of the VTF into an Image Texture node, plus `$bumpmap` through a Normal Map and `$translucent`/`$alphatest` into the alpha channel.

The dialog asks for:

- **Game Content** — the extracted game content with the `.vmt` files (e.g. a Crowbar unpack of the VPKs). Defaults to Game Path.
- **Converted Textures** — the VTFs converted to PNG, keeping their folder structure. With VTFEdit Reloaded: **Tools > Convert Folder** on the `materials` folder, PNG, recursive. Leave empty if you converted in place.

Either field may point at the folder that contains `materials\`, at `materials\` itself, or at `materials\models` — the tool figures out which.
- **$cdmaterials** — filled in automatically when a QC is imported; otherwise the `$cdmaterials` lines of the model, separated by semicolons. Leave empty to search all of `materials/models` by file name.

Materials that already have an Image Texture node are left alone unless **Overwrite** is ticked. Anything that couldn't be resolved (VMT not found, PNG not found) is listed in a popup and in the System Console.

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
  link_vmt.py              "Link VMT Textures": assigns VMT/$basetexture images to imported materials
Tests/                   unit tests, run against Blender-as-a-Python-module
scripts/
  sync_to_blender.ps1      deploys io_scene_valvesource/ into a local Blender install
  set_dev_defaults.ps1     writes machine-specific path defaults (Export/Engine/Game/Material/VMT) (gitignored)
  install_debugpy.ps1      installs debugpy into Blender's Python, for VS Code debugging
  launch_blender_debug.ps1 starts Blender with the debug listener open
  blender_debug_listen.py  runs inside Blender: opens the debug port, loads the add-on
                           from this repository
  blender_paths.ps1        shared helper that locates an installed Blender
.vscode/
  launch.json              VS Code debug configurations (gitignored, see above)
  tasks.json               sync / build / debug tasks (gitignored)
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
