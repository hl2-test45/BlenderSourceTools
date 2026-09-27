"""Run the test suite inside a Blender executable instead of the bpy pip module.

    blender -b --factory-startup --python Tests/run_in_blender.py -- [test name filters...]

The repository is put first on sys.path so that it wins over any copy of the
add-on installed in Blender's user scripts folder. Filters are matched as
substrings of the test ids, e.g. "QcImport" or "Blender.test_Export_AllTypes".
"""
import os, sys, unittest

root = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, root)
for name in [n for n in sys.modules if n == "io_scene_valvesource" or n.startswith("io_scene_valvesource.")]:
	del sys.modules[name]

filters = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
sys.argv = [sys.argv[0]]

from Tests import test_addon

def iter_tests(suite):
	for test in suite:
		if isinstance(test, unittest.TestSuite):
			yield from iter_tests(test)
		else:
			yield test

loaded = unittest.TestLoader().loadTestsFromTestCase(test_addon.Blender)
selected = unittest.TestSuite(t for t in iter_tests(loaded) if not filters or any(f in t.id() for f in filters))
result = unittest.TextTestRunner(verbosity=2).run(selected)
sys.exit(0 if result.wasSuccessful() else 1)
