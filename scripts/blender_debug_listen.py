"""
Starts a debugpy listener inside Blender so VS Code can attach to the add-on.

Normally run for you by scripts/launch_blender_debug.ps1:

    blender.exe --python scripts/blender_debug_listen.py

You can also run it by hand from Blender's Text Editor (Text > Open, then Run
Script) to attach to an already-running Blender.

What it does, in order:
  1. imports debugpy (see scripts/install_debugpy.ps1 if that fails),
  2. opens a listening socket VS Code's "attach" configuration connects to,
  3. optionally waits a few seconds for VS Code to attach, so that breakpoints
     placed in module-level code or register() are hit,
  4. enables io_scene_valvesource *straight from this repository*, so that the
     .py files Blender executes are the very files open in the editor and no
     launch.json pathMappings entry is needed.

Environment variables (all optional, set by the launcher script):
  BST_DEBUG_PORT      port to listen on                        (default 5678)
  BST_DEBUG_WAIT      seconds to wait for VS Code to attach,
                      0 to continue starting Blender at once   (default 10)
  BST_DEBUG_ADDON     "repo"      load the add-on from this repository,
                      "installed" leave Blender's own copy alone
                                                               (default repo)
  BST_DEBUGPY_PATH    extra sys.path entry to import debugpy from, for when it
                      was pip-installed with --target somewhere unusual
  BST_REPO_ROOT       repository root, only needed when Blender cannot work it
                      out from this file's location
"""

import os
import sys
import time

ADDON = "io_scene_valvesource"


def _log(message):
	# The launcher's VS Code task watches for the "listening on" line below to
	# know when it is safe to attach, so keep this prefix stable.
	print("BST debugpy: {}".format(message), flush=True)


def _repo_root():
	from_env = os.environ.get("BST_REPO_ROOT")
	if from_env:
		return os.path.abspath(from_env)
	# __file__ is undefined when this is pasted into Blender's Python console.
	here = globals().get("__file__")
	if not here:
		raise RuntimeError("Cannot locate the repository: set BST_REPO_ROOT.")
	return os.path.dirname(os.path.dirname(os.path.abspath(here)))


def _adapter_python():
	"""Path to a plain interpreter debugpy can run its adapter process with.

	debugpy spawns that process using sys.executable, which inside Blender is
	blender.exe -- launching a second Blender instead of an adapter. Point it
	at the interpreter Blender ships with instead.
	"""
	if sys.platform == "win32":
		names = ("python.exe",)
	else:
		names = ("python{}.{}".format(*sys.version_info[:2]), "python3", "python")
	for directory in (os.path.join(sys.prefix, "bin"), os.path.join(sys.prefix, "Scripts"), sys.prefix):
		for name in names:
			candidate = os.path.join(directory, name)
			if os.path.isfile(candidate):
				return candidate
	return None


def _import_debugpy():
	# Blender's stdlib is partly frozen, which makes pydevd print a warning
	# about possibly missing breakpoints. It does not apply to add-on code.
	os.environ.setdefault("PYDEVD_DISABLE_FILE_VALIDATION", "1")
	extra_path = os.environ.get("BST_DEBUGPY_PATH")
	if extra_path and os.path.isdir(extra_path) and extra_path not in sys.path:
		sys.path.append(extra_path)
	try:
		import debugpy
	except ImportError:
		_log("debugpy is not importable from Blender's Python ({}).".format(sys.executable))
		_log("Install it with: powershell -File scripts/install_debugpy.ps1")
		return None
	return debugpy


def start_listener(port=None, wait_seconds=None):
	"""Open the debug port. Returns True once Blender is listening."""
	debugpy = _import_debugpy()
	if debugpy is None:
		return False

	if port is None:
		port = int(os.environ.get("BST_DEBUG_PORT", 5678))
	if wait_seconds is None:
		wait_seconds = float(os.environ.get("BST_DEBUG_WAIT", 10))

	adapter_python = _adapter_python()
	if adapter_python:
		debugpy.configure(python=adapter_python)
	else:
		_log("WARNING: no plain interpreter found next to {}; debugpy may try to "
			 "start the adapter with blender.exe.".format(sys.prefix))

	try:
		debugpy.listen(("127.0.0.1", port))
	except RuntimeError as ex:
		# Almost always "Can't listen for clients: already listening", i.e. this
		# script ran twice in one Blender session. Harmless.
		_log("already listening, or could not listen: {}".format(ex))
		return debugpy.is_client_connected() or True

	_log("listening on 127.0.0.1:{}".format(port))

	if wait_seconds > 0 and not debugpy.is_client_connected():
		_log("waiting up to {:g}s for VS Code to attach...".format(wait_seconds))
		# Not debugpy.wait_for_client(): that blocks Blender forever if you
		# never attach. Poll instead so startup always finishes.
		deadline = time.monotonic() + wait_seconds
		while not debugpy.is_client_connected() and time.monotonic() < deadline:
			time.sleep(0.05)
		_log("attached." if debugpy.is_client_connected() else "no debugger attached, carrying on.")

	return True


def enable_addon_from_repo():
	"""Enable io_scene_valvesource from this repository rather than from
	Blender's add-ons folder, so breakpoints bind without path mappings."""
	import bpy
	import addon_utils

	repo_root = _repo_root()
	source = os.path.join(repo_root, ADDON)
	if not os.path.isdir(source):
		_log("no {} folder in {}; leaving the add-on alone.".format(ADDON, repo_root))
		return

	if sys.path[:1] != [repo_root]:
		if repo_root in sys.path:
			sys.path.remove(repo_root)
		sys.path.insert(0, repo_root)

	loaded = sys.modules.get(ADDON)
	loaded_dir = os.path.dirname(getattr(loaded, "__file__", "")) if loaded else ""
	if loaded and os.path.normcase(loaded_dir) != os.path.normcase(source):
		# A copy previously installed into Blender's add-ons folder won the
		# import; unload it so the repository version takes over.
		_log("unloading the add-on already imported from {}".format(loaded_dir))
		try:
			addon_utils.disable(ADDON, default_set=False)
		except Exception as ex:
			_log("could not disable it cleanly: {}".format(ex))
		for name in [n for n in sys.modules if n == ADDON or n.startswith(ADDON + ".")]:
			del sys.modules[name]

	addon_utils.enable(ADDON, default_set=False, persistent=True)
	module = sys.modules.get(ADDON)
	if module:
		_log("add-on running from {}".format(os.path.dirname(module.__file__)))
	else:
		_log("failed to enable {} -- see the errors above.".format(ADDON))


def main():
	if not start_listener():
		return
	if os.environ.get("BST_DEBUG_ADDON", "repo").lower() == "repo":
		enable_addon_from_repo()


if __name__ == "__main__":
	main()
