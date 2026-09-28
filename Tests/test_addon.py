# see https://wiki.blender.org/wiki/Building_Blender/Other/BlenderAsPyModule
import os, shutil, unittest, sys
from os.path import join
from importlib import import_module

src_path = os.path.dirname(__file__)
results_path = join(src_path,"..","TestResults")
if not os.path.exists(results_path): os.makedirs(results_path)

sys.path.append(join(src_path, '..'))
_addon_path = join(src_path, '..', "io_scene_valvesource")
sys.path.append(_addon_path)
datamodel = import_module("datamodel")
sys.path.remove(_addon_path)

def stableRound(f):
	r = round(f, 4)
	return 0 if r == 0 else r # eliminate -0.0

baseVectorInit = datamodel._Vector.__init__
def _VectorRoundedInit(self, l):
	l = [stableRound(i) for i in l]
	baseVectorInit(self,l)
datamodel._Vector.__init__ = _VectorRoundedInit

baseMatrixInit = datamodel.Matrix.__init__
def _MatrixRoundedInit(self, matrix=None):
	if matrix:
		matrix = [[stableRound(i) for i in l] for l in matrix]
	baseMatrixInit(self,matrix)
datamodel.Matrix.__init__ = _MatrixRoundedInit

import math

# A minimal ValveBiped leg: (name, parent, rest position, rest rotation). Each child sits along its parent's X axis.
_biped_bones = [
	("ValveBiped.Bip01_Pelvis", -1, (0, 0, 40), (0, 0, 0)),
	("ValveBiped.Bip01_L_Thigh", 0, (0, 4, 0), (0, math.pi / 2, 0)),
	("ValveBiped.Bip01_L_Calf", 1, (18, 0, 0), (0, 0, 0.1)),
	("ValveBiped.Bip01_L_Foot", 2, (16, 0, 0), (0, 0, 0)),
	("ValveBiped.Bip01_L_Toe0", 3, (5, 0, 0), (0, 0, 0)),
]
_biped_rest = [(pos, rot) for _, _, pos, rot in _biped_bones]

def _biped_walk_frame(t):
	"""A pose of the synthetic leg: root motion, a swinging thigh, a bending knee; the toe never moves."""
	pose = [list(map(list, bone)) for bone in _biped_rest]
	pose[0][0][0] = t * 2.0
	pose[1][1][1] = math.pi / 2 + 0.3 * math.sin(t)
	pose[2][1][2] = 0.1 + 0.4 * (1 + math.sin(t)) / 2
	return pose

def _write_biped_smd(path, frames, triangles = False, bones = None):
	"""bones: the indices of the bones which the frames key, if not all of them."""
	lines = ["version 1", "nodes"]
	lines += ['{} "{}" {}'.format(i, name, parent) for i, (name, parent, _, _) in enumerate(_biped_bones)]
	lines += ["end", "skeleton"]
	for f, pose in enumerate(frames):
		lines.append("time {}".format(f))
		lines += ["{} {:.6f} {:.6f} {:.6f} {:.6f} {:.6f} {:.6f}".format(bones[i] if bones else i, *pos, *rot) for i, (pos, rot) in enumerate(pose)]
	lines.append("end")
	if triangles:
		lines.append("triangles")
		for i in range(len(_biped_bones)):
			lines.append("skin")
			z = 40 - i * 5
			lines += ["{0} {1} {2} {3} 0 0 1 0 0 1 {0} 1".format(i, *v) for v in ((0, 0, z), (1, 0, z), (0, 1, z))]
		lines.append("end")
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w") as f:
		f.write("\n".join(lines) + "\n")

_biped_qc = """$modelname "test/biped.mdl"
$cdmaterials "models\\biped\\"
$body body "biped_ref.smd"
$lod 10
{
	replacemodel "biped_ref.smd" "biped_lod1.smd"
	replacebone "ValveBiped.Bip01_L_Toe0" "ValveBiped.Bip01_L_Foot"
}
$shadowlod
{
	replacemodel "biped_ref.smd" "biped_lod2.smd"
}
$collisionmodel "biped_phys.smd" {
	$mass 10
}
$ikchain "lfoot" "ValveBiped.Bip01_L_Foot" knee 0.707 -0.707 0
$hbox 6 "ValveBiped.Bip01_L_Thigh" 0 0 0 1 1 1
$includemodel "biped_gestures.mdl"
$poseparameter "move_yaw" -180 180 loop 360
$poseparameter "aim_yaw" -45 45
$poseparameter "aim_pitch" -45 45
$declaresequence "late"
$declaresequence "never"
$animation "a_walk" "anims\\walk.smd" {
	fps 24
}
$sequence "walk_all" {
	"a_walk"
	blend "move_yaw" -180 180
	{ event 1000 1 "step" }
	loop
}
$sequence "idle" "anims\\idle.smd" loop fps 30
$sequence "jump"
{
	"anims\\jump.smd"
	activity "ACT_JUMP" 1
	keyvalues
	{
		foo "bar"
	}
}
/* $sequence "commented" "anims\\commented.smd" */
$sequence "missing" "anims\\does_not_exist.smd"
$animation "a_lean_left" "anims\\lean_left.smd"
$animation "a_lean_center" "anims\\lean_center.smd"
$animation "a_lean_right" "anims\\lean_right.smd"
$sequence "lean" {
	"a_lean_left" "a_lean_center" "a_lean_right"
	blend "move_yaw" -90 90
	blendwidth 3
}
$sequence "aim" {
	"anims\\aim_00.smd"
	"anims\\aim_10.smd"
	"anims\\aim_01.smd"
	"anims\\aim_11.smd"
	blend "aim_yaw" -45 45
	blend "aim_pitch" -45 45
}
$animation "@gesture_corrective" "anims\\gesture_corrective.smd"
$sequence "gesture" {
	"anims\\gesture.smd"
	delta
	subtract "@gesture_corrective" 0
	hidden
}
$sequence "gesture_pre" {
	"anims\\gesture.smd"
	predelta
	presubtract "@gesture_corrective" 0
}
$includemodel "biped_anims.mdl"
$includemodel "not_decompiled.mdl"
"""

# An $includemodel QC which the model above names before its own sequences. The engine still lists the model's own
# sequences first: "late" fills the model's placeholder, and "idle" is a duplicate which is ignored.
_biped_gestures_qc = """$modelname "biped_gestures.mdl"
$sequence "late" "anims\\idle.smd" fps 15
$sequence "idle" "anims\\jump.smd"
"""

# An $includemodel QC. It has its own "a_walk", includes the model above (a cycle), and its meshes must be ignored.
_biped_anims_qc = """$modelname "biped_anims.mdl"
$cdmaterials "should_not_be_registered\\"
$body ignored "biped_ref.smd"
$animation "a_walk" "anims\\walk_other.smd"
$sequence "run" { "a_walk" fps 30 }
$includemodel "biped.mdl"
"""

sdk_content_path = os.getenv("SOURCESDK")
steam_common_path = None
if sdk_content_path:
	sdk_content_path += "_content\\"
	steam_common_path = os.path.realpath(join(sdk_content_path,"..","..","common"))

class _AddonTests():
	compare_results = True
	blend : str | None = None
	module_subdir : str | None = None

	@property
	def sceneSettings(self):
		return self.bpy.context.scene.vs
	@property
	def expectedResultsPath(self):
		assert(self.blend)
		return join(src_path,"ExpectedResults",self.blend)

	@property
	def outputPath(self):
		assert(self.blend)
		return os.path.realpath(join(results_path,self.bpy_version,os.path.splitext(self.blend)[0]))

	def setUp(self):
		from importlib import import_module
		try:
			self.bpy = import_module(".bpy", self.module_subdir) if self.module_subdir else import_module("bpy")
		except ImportError as ex:
			self.skipTest("Could not import {}: {}".format(self.module_subdir, ex))
			return

		try:
			self.bpy.context.preferences.filepaths.file_preview_type = 'NONE'
		except:
			pass

		self.bpy.ops.preferences.addon_enable(module='io_scene_valvesource')
		self.bpy.app.debug_value = 1
		self.bpy_version = ".".join(str(i) for i in self.bpy.app.version)			
		print("Blender version",self.bpy_version)

	def compareResults(self, outputDir):
		if self.compare_results:
			if os.path.exists(self.expectedResultsPath):
				self.maxDiff = None
				for dirpath,dirnames,filenames in os.walk(outputDir):
					for f in filenames:
						self.compareFiles(join(dirpath,f), join(self.expectedResultsPath,f))
				print("Output matches expected results")

	def compareFiles(self, output, expected):
		error_message = "Export did not match expected output @ {}.".format(output)
		with open(output,'rb') as out_file:
			with open(expected,'rb') as expected_file:
				if expected_file.read() != out_file.read():
					out_file.seek(0)
					expected_file.seek(0)
					if output.endswith(".dmx"):
						self.assertEqual(datamodel.load(in_file=expected_file).echo("keyvalues2", 1), datamodel.load(in_file=out_file).echo("keyvalues2", 1), error_message)
					else:
						def to_string(file): file.read().decode('utf-8').replace('\r\n','\n')
						self.assertEqual(to_string(expected_file), to_string(out_file), error_message)

	def setupExportTest(self, blend):
		self.blend = blend
		self.bpy.ops.wm.open_mainfile(filepath=join(src_path, blend + ".blend"))
		blend_name = os.path.splitext(blend)[0]
		self.setupExport(blend_name)
		return blend_name

	def setupExport(self, dir):
		self.sceneSettings.export_path = os.path.realpath(join(results_path,self.bpy_version, dir))
		if os.path.isdir(self.sceneSettings.export_path):
			shutil.rmtree(self.sceneSettings.export_path)

	def runExportTest(self,blend):
		blend_name = self.setupExportTest(blend)

		def ex(do_scene):
			result = self.bpy.ops.export_scene.smd(export_scene=do_scene)
			self.assertTrue(result == {'FINISHED'})
		
		def section(*args):
			print("\n\n********\n********  {} {}".format(self.bpy.context.scene.name,*args),"\n********")

		export_path_base = self.sceneSettings.export_path
		section("DMX Source 2")
		self.sceneSettings.export_path = join(export_path_base, "Source2")
		self.sceneSettings.export_format = 'DMX'
		self.sceneSettings.dmx_encoding = '9'
		self.sceneSettings.dmx_format = '22'
		ex(True)

		section("DMX Source 1")
		self.sceneSettings.export_path = join(export_path_base, "Source")
		self.sceneSettings.export_format = 'DMX'
		self.sceneSettings.dmx_encoding = '5'
		self.sceneSettings.dmx_format = '18'
		self.sceneSettings.engine_path = ''
		ex(True)

		section("SMD GoldSrc")
		self.sceneSettings.export_path = join(export_path_base, "GoldSrc")
		self.sceneSettings.export_format = 'SMD'
		self.sceneSettings.smd_format = 'GOLDSOURCE'
		ex(True)

		section("SMD Source")
		self.sceneSettings.export_path = join(export_path_base, "SourceSMD")
		self.sceneSettings.smd_format = 'SOURCE'
		ex(True)

		qc_name = self.bpy.path.abspath("//" + blend_name + ".qc")

		if steam_common_path and os.path.exists(qc_name):
			sfm_usermod = join(steam_common_path,"SourceFilmmaker","game","usermod")
			if os.path.exists(sfm_usermod):
				shutil.copy2(qc_name, self.sceneSettings.export_path)
				self.sceneSettings.game_path = sfm_usermod
				self.sceneSettings.engine_path = os.path.realpath(join(self.sceneSettings.game_path,"..","bin"))
				self.assertEqual(self.bpy.ops.smd.compile_qc(filepath=join(self.sceneSettings.export_path, blend_name + ".qc")), {'FINISHED'})
			else:
				print("WARNING: Could not locate Source Filmmaker; skipped QC compile test.")

		self.compareResults(join(export_path_base, "Source2"))
		self.compareResults(join(export_path_base, "SourceSMD"))	

	def runExportTest_Single(self,ob_name):
		self.bpy.ops.object.mode_set(mode='OBJECT')
		self.bpy.ops.object.select_all(action='DESELECT')
		self.bpy.data.objects[ob_name].select_set(True)
		
		for fmt in ['DMX','SMD']:
			self.sceneSettings.export_format = fmt
			self.bpy.ops.export_scene.smd()

		self.compareResults(self.sceneSettings.export_path)

	def test_Export_Armature_Mesh(self):
		self.runExportTest("Cube_Armature")
	def test_Export_Armature_Text(self):
		self.runExportTest("Text_Armature")
	def test_Export_Armature_Curve(self):
		self.runExportTest("Curve_Armature")

	def test_Export_NoArmature_Mesh(self):
		self.runExportTest("Cube_NoArmature")
	def test_Export_NoArmature_Text(self):
		self.runExportTest("Text_NoArmature")
	def test_Export_NoArmature_Curve(self):
		self.runExportTest("Curve_NoArmature")

	def test_Export_NoBones(self):
		self.runExportTest("Armature_NoBones")
	def test_Export_AllTypes(self):
		self.runExportTest("AllTypes_Armature")
		jointList = datamodel.load(join(self.outputPath,"Source2", "AllTypes.dmx")).root["skeleton"]["jointList"]
		if any(elem.name == "Bone_NonDeforming" for elem in jointList):
			self.fail("Export contained 'Bone_NonDeforming'. This should have been excluded.")

		self.runExportTest_Single("Armature")

	def test_Export_ActionFilter(self):
		self.runExportTest("ActionFilter")

	def test_Export_TF2(self):
		self.runExportTest("Scout")
		self.runExportTest_Single("vsDmxIO Scene")

	def test_Export_VertexAnimation(self):
		self.runExportTest("VertexAnimation")

	def test_Generate_FlexControllers(self):
		self.bpy.ops.wm.open_mainfile(filepath=join(src_path, "Scout.blend"))
		self.bpy.context.view_layer.objects.active = self.bpy.data.objects['head=zero']
		self.assertEqual(self.bpy.ops.export_scene.dmx_flex_controller(), {'FINISHED'})

		with open(join(src_path, "flex_scout_morphs_low.dmx"),encoding='ASCII') as f:
			target_dmx = f.read()

		self.maxDiff = None
		self.assertEqual(target_dmx.strip(),self.bpy.data.texts[-1].as_string().strip())

	def _setupCorrectiveShapes(self):
		self.bpy.ops.mesh.primitive_cube_add(enter_editmode=False)
		ob = self.bpy.context.active_object
		ob.shape_key_add(name="Basis")
		ob.shape_key_add(name="k1")
		ob.shape_key_add(name="k2")
		separator = "__" if "modeldoc" in self.sceneSettings.dmx_format else "_"
		ob.shape_key_add(name=separator.join(["k1","k2"]))
		ob.active_shape_key_index = 0

	def test_GenerateCorrectiveDrivers(self):
		self._setupCorrectiveShapes()
		self.assertEqual(self.bpy.ops.object.sourcetools_generate_corrective_drivers(), {'FINISHED'})

		driver = self.bpy.context.active_object.data.shape_keys.animation_data.drivers[0].driver
		self.assertTrue(driver.is_valid)
		self.assertEqual(driver.type, 'MIN')
		self.assertEqual(len(driver.variables), 2)

	def test_RenameShapesToMatchCorrectiveDrivers(self):
		self.sceneSettings.dmx_format = '22'
		try:
			self.test_GenerateCorrectiveDrivers()
		except:
			self.skipTest("GenerateCorrectiveDrivers test failed")

		corrective_key = self.bpy.context.active_object.data.shape_keys.key_blocks[-1]

		corrective_key.name = "badname"
		self.bpy.ops.object.sourcetools_rename_to_corrective_drivers()
		self.assertEqual(corrective_key.name, "k1_k2")

	def test_Import_SMD_Overlapping_DifferentWeights(self):
		self.assertEqual(self.bpy.ops.import_scene.smd(filepath=join(src_path, "Overlapping_DifferentWeights.smd")), {'FINISHED'})
		self.assertEqual(6, len(self.bpy.data.meshes['Overlapping_DifferentWeights'].vertices), "Incorrect vertex count")

	def runImportTest(self, test_name, *files):
		self.bpy.ops.wm.read_homefile(app_template="")
		out_dir = join(results_path,self.bpy_version,test_name)
		if os.path.isdir(out_dir):
			shutil.rmtree(out_dir)
		os.makedirs(out_dir)
		
		for f in files:
			self.assertEqual(self.bpy.ops.import_scene.smd(filepath=join(src_path,f)), {'FINISHED'})
		
		self.bpy.ops.wm.save_mainfile(filepath=join(out_dir,test_name + ".blend"),check_existing=False)

	@unittest.skipUnless(sdk_content_path, "Source SDK not found")
	def test_import_SMD(self):
		assert(sdk_content_path)
		self.runImportTest("import_smd",
					 sdk_content_path + "hl2/modelsrc/humans_sdk/Male_sdk/Male_06_reference.smd",
					 sdk_content_path + "hl2/modelsrc/humans_sdk/Male_sdk/Male_06_expressions.vta",
					 sdk_content_path + "hl2/modelsrc/humans_sdk/Male_Animations_sdk/ShootSMG1.smd")
		self.assertEqual(len(self.bpy.data.meshes["Male_06_reference"].shape_keys.key_blocks), 33)

	@unittest.skipUnless(sdk_content_path, "Source SDK not found")
	def test_import_DMX(self):
		assert(sdk_content_path)
		self.runImportTest("import_dmx",
					 sdk_content_path + "tf/modelsrc/player/heavy/scripts/heavy_low.qc",
					 sdk_content_path + "tf/modelsrc/player/heavy/animations/dmx/Die_HeadShot_Deployed.dmx")
		self.assertEqual(len(self.bpy.data.meshes["head=zero"].shape_keys.key_blocks), 43)

	def test_Source2VertexData_RoundTrips(self):
		filename = "cloth_test_simple.dmx"
		testname = "VertexDataRoundTrip"
		self.runImportTest(testname, filename)
		self.setupExport(testname)
		self.bpy.context.view_layer.objects.active = self.bpy.data.objects['cloth_test_simple']
		self.sceneSettings.export_format = 'DMX'
		self.sceneSettings.use_kv2 = True

		result = self.bpy.ops.export_scene.smd(collection='cloth_test_simple')
		self.assertTrue(result == {'FINISHED'})
		self.compareFiles(join(self.sceneSettings.export_path, filename), join(src_path, filename))

	def test_ModelDocCorrectiveShapes_RoundTrip(self):
		testname = "ModelDocCorrectives"
		self.sceneSettings.export_format = 'DMX'
		self.sceneSettings.dmx_format = '22_modeldoc'
		self.sceneSettings.use_kv2 = True

		self.test_GenerateCorrectiveDrivers()
		
		self.setupExport(testname)
		self.assertEqual(self.bpy.ops.export_scene.smd(collection="Collection"), {'FINISHED'})

		self.assertEqual(self.bpy.ops.import_scene.smd(filepath=join(self.sceneSettings.export_path, self.bpy.context.active_object.name + ".dmx")), {'FINISHED'})		

		imported_keys = self.bpy.context.active_object.data.shape_keys.key_blocks
		self.assertEqual(len(imported_keys), 4)
		self.assertEqual(imported_keys[-1].name, "k1__k2")

	def test_export_SMD_GoldSrc(self):
		self.setupExportTest("Cube_Armature")

		# override setupTest's values
		self.blend = "Cube_Armature_GoldSource"
		self.sceneSettings.export_path = os.path.realpath(join(results_path, self.bpy_version, self.blend))

		self.sceneSettings.export_format = 'SMD'
		self.sceneSettings.smd_format = 'GOLDSOURCE'

		result = self.bpy.ops.export_scene.smd(export_scene=True)
		self.assertTrue(result == {'FINISHED'})

		self.compareResults(self.sceneSettings.export_path)

	def test_Object_Collection_SameNameExport(self):
		self.setupExportTest("Scout")
		
		for ob in self.bpy.data.objects:
			if ob.data == self.bpy.data.armatures[0]:
				ob.name = self.bpy.data.collections[0].name
				break

		import inspect
		exportablesGenerator = inspect.getmodule(self.bpy.types.SMD_UL_ExportItems).getSelectedExportables()
		
		list(exportablesGenerator)

	#
	# QC import: LODs, $includemodel, the animation list, the IK rig
	#

	def _setupBipedQc(self):
		self.bpy.ops.wm.read_homefile(app_template="")
		folder = join(results_path, self.bpy_version, "biped_qc")
		if os.path.isdir(folder):
			shutil.rmtree(folder)
		for name in ("biped_ref", "biped_lod1", "biped_lod2", "biped_phys"):
			_write_biped_smd(join(folder, name + ".smd"), [_biped_rest], triangles=True)
		self.walk_frames = [_biped_walk_frame(t * 0.5) for t in range(11)]
		_write_biped_smd(join(folder, "anims", "walk.smd"), self.walk_frames)
		_write_biped_smd(join(folder, "anims", "idle.smd"), [_biped_rest] * 5)
		_write_biped_smd(join(folder, "anims", "jump.smd"), [_biped_walk_frame(t) for t in range(3)])
		_write_biped_smd(join(folder, "anims", "walk_other.smd"), [_biped_walk_frame(-t) for t in range(4)])
		for name, offset in (("left", -0.4), ("center", 0), ("right", 0.4)):
			_write_biped_smd(join(folder, "anims", "lean_{}.smd".format(name)), [self._bipedPose(thigh_y=math.pi / 2 + offset)] * 2)
		for x in (0, 1):
			for y in (0, 1):
				_write_biped_smd(join(folder, "anims", "aim_{}{}.smd".format(x, y)), [self._bipedPose(pelvis=(x * 10, y * 10, 40))])
		# a Crowbar-style delta: raw offsets, and a corrective animation which only keys the root
		_write_biped_smd(join(folder, "anims", "gesture.smd"), [self.gesture_delta] * 3)
		_write_biped_smd(join(folder, "anims", "gesture_corrective.smd"), [[((0, 0, 0), (0, 0, 0.3))]], bones=[0])
		with open(join(folder, "biped.qc"), "w") as f: f.write(_biped_qc)
		with open(join(folder, "biped_anims.qc"), "w") as f: f.write(_biped_anims_qc)
		with open(join(folder, "biped_gestures.qc"), "w") as f: f.write(_biped_gestures_qc)
		self.biped_folder = folder
		return join(folder, "biped.qc")

	gesture_delta = [((0.5, 0, 0), (0, 0, 0.3)), ((0, 0, 0), (0.4, 0, 0))] + [((0, 0, 0), (0, 0, 0))] * (len(_biped_bones) - 2)

	@staticmethod
	def _bipedPose(pelvis = None, thigh_y = None):
		pose = [list(map(list, bone)) for bone in _biped_rest]
		if pelvis is not None:
			pose[0][0] = list(pelvis)
		if thigh_y is not None:
			pose[1][1][1] = thigh_y
		return pose

	def _importBipedQc(self, **kwargs):
		qc = self._setupBipedQc()
		self.assertEqual(self.bpy.ops.import_scene.smd(filepath=qc, **kwargs), {'FINISHED'})
		arm = next(ob for ob in self.bpy.context.scene.objects if ob.type == 'ARMATURE')
		self.bpy.context.view_layer.objects.active = arm
		return arm

	@property
	def anim_list(self):
		return import_module("io_scene_valvesource").anim_list

	def _qcAnimIndex(self, arm, name):
		return next(i for i, item in enumerate(arm.vs.qc_anims) if item.name == name)

	def _expectedBipedPose(self, pose):
		"""Armature space matrices of a pose of the synthetic leg: each bone's parent's matrix times its SMD transform."""
		from mathutils import Matrix, Euler
		matrices = []
		for (_, parent, _, _), (pos, rot) in zip(_biped_bones, pose):
			local = Matrix.Translation(pos) @ Euler(rot).to_matrix().to_4x4()
			matrices.append(matrices[parent] @ local if parent >= 0 else local)
		return { bone[0]: matrix for bone, matrix in zip(_biped_bones, matrices) }

	def assertMatricesAlmostEqual(self, a, b, places = 3, msg = None):
		for i in range(4):
			for j in range(4):
				self.assertAlmostEqual(a[i][j], b[i][j], places=places, msg="{} [{}][{}]".format(msg, i, j))

	def _findLayerCollection(self, collection, layer_collection = None):
		layer_collection = layer_collection or self.bpy.context.view_layer.layer_collection
		if layer_collection.collection == collection:
			return layer_collection
		for child in layer_collection.children:
			found = self._findLayerCollection(collection, child)
			if found:
				return found

	def test_QcImport_LodsAndPhysicsExcluded(self):
		self._importBipedQc()
		bpy = self.bpy
		lods = bpy.data.collections["biped LODs"]
		physics = bpy.data.collections["biped Physics"]
		self.assertTrue(self._findLayerCollection(lods).exclude)
		self.assertTrue(self._findLayerCollection(physics).exclude)

		self.assertEqual({c.name for c in lods.children}, {"biped_lod1", "biped_lod2"})
		self.assertEqual(lods.children["biped_lod1"]["lod_threshold"], 10.0)
		self.assertTrue(lods.children["biped_lod2"]["lod_shadow"])
		self.assertEqual(len(physics.children), 1)

		view_objects = bpy.context.view_layer.objects
		self.assertIn("biped_ref", view_objects)
		self.assertTrue(bpy.data.objects["biped_ref"].select_get())
		self.assertNotIn("biped_lod1", view_objects)
		self.assertNotIn("biped_phys", view_objects)

	def test_QcImport_ListsSequences(self):
		arm = self._importBipedQc()
		bpy = self.bpy
		vs = arm.vs
		# the model's own sequences first, then those of its $includemodels, in order. Not "commented", not the one whose
		# file is missing, not the placeholder which no model defines.
		self.assertEqual([item.name for item in vs.qc_anims], ["late", "walk_all", "idle", "jump", "lean", "aim", "gesture", "gesture_pre", "run"])
		self.assertEqual(len(bpy.data.actions), 0, "sequences must only be listed")
		seqs = { item.name: item for item in vs.qc_anims }

		walk = seqs["walk_all"]
		self.assertEqual(walk.source_qc, "biped")
		self.assertEqual([(c.name, os.path.basename(c.filepath), c.fps) for c in walk.components], [("a_walk", "walk.smd", 24)])
		self.assertEqual(walk.fps, 24)
		self.assertTrue(walk.is_loop)
		self.assertFalse(self.anim_list.needs_compose(walk), "a blend of one animation plays it as it is")

		# a placeholder is filled by the included model; the included model's "idle" doesn't replace the model's
		self.assertEqual(seqs["late"].source_qc, "biped_gestures")
		self.assertEqual(seqs["late"].fps, 15)
		self.assertEqual(os.path.basename(seqs["idle"].components[0].filepath), "idle.smd")
		self.assertTrue(seqs["idle"].is_loop)
		self.assertEqual(seqs["idle"].fps, 30)
		self.assertEqual(seqs["jump"].activity, "ACT_JUMP")

		# the included model's "a_walk" is its own file
		self.assertEqual(seqs["run"].source_qc, "biped_anims")
		self.assertEqual(os.path.basename(seqs["run"].components[0].filepath), "walk_other.smd")

		lean = seqs["lean"]
		self.assertEqual([c.name for c in lean.components], ["a_lean_left", "a_lean_center", "a_lean_right"])
		self.assertEqual(self.anim_list.blend_size(lean), (3, 1))
		self.assertEqual(self.anim_list.blend_params(lean), [("move_yaw", -90, 90)])
		self.assertEqual(self.anim_list.blend_size(seqs["aim"]), (2, 2)) # square, without blendwidth
		self.assertEqual([p.name for p in seqs["aim"].blend_params], ["aim_yaw", "aim_pitch"])

		gesture = seqs["gesture"]
		self.assertTrue(gesture.is_delta and gesture.is_post and gesture.is_hidden)
		self.assertEqual(os.path.basename(gesture.components[0].subtract_filepath), "gesture_corrective.smd")
		self.assertTrue(gesture.components[0].subtract_post)
		pre = seqs["gesture_pre"]
		self.assertTrue(pre.is_delta)
		self.assertFalse(pre.is_post or pre.components[0].subtract_post)

		self.assertEqual({p.name: (p.min_value, p.max_value, p.value) for p in vs.qc_pose_params},
			{"move_yaw": (-180, 180, 0), "aim_yaw": (-45, 45, 0), "aim_pitch": (-45, 45, 0)})

		# included models only contribute sequences
		self.assertNotIn("biped_ref.001", bpy.data.objects)
		self.assertNotIn("should_not_be_registered", bpy.context.scene.vs.vmt_cdmaterials)
		self.assertEqual(vs.qc_missing_includes, "not_decompiled.mdl")

		# deltas are hidden until asked for; the filter matches activities and the animations sequences play
		index = lambda name: self._qcAnimIndex(arm, name)
		self.assertEqual(self.anim_list.filtered_indices(vs), [index(n) for n in ("late", "walk_all", "idle", "jump", "lean", "aim", "run")])
		vs.qc_anims_show_delta = True
		self.assertEqual(len(self.anim_list.filtered_indices(vs)), 9)
		vs.qc_anims_filter = "a_lean"
		self.assertEqual(self.anim_list.filtered_indices(vs), [index("lean")])
		vs.qc_anims_filter = "act_jump"
		self.assertEqual(self.anim_list.filtered_indices(vs), [index("jump")])

		import json
		hints = json.loads(arm.data.vs.rig_hints)
		self.assertEqual(hints["ikchains"], [{"name": "lfoot", "bone": "ValveBiped.Bip01_L_Foot", "knee": [0.707, -0.707, 0.0]}])
		self.assertEqual(hints["hitgroups"], {"ValveBiped.Bip01_L_Thigh": 6})

	def test_QcImport_LoadAnimation(self):
		arm = self._importBipedQc(generateRig=False)
		bpy = self.bpy
		scene = bpy.context.scene
		index = self._qcAnimIndex(arm, "walk_all")
		self.assertEqual(bpy.ops.smd.qc_anim_load(index=index), {'FINISHED'})

		item = arm.vs.qc_anims[index]
		self.assertTrue(self.anim_list.is_loaded(item))
		self.assertEqual(item.num_frames, 11)
		self.assertEqual((scene.frame_start, scene.frame_end), (0, 10))
		self.assertEqual(scene.render.fps, 24)

		for frame in (0, 5, 10):
			scene.frame_set(frame)
			expected = self._expectedBipedPose(self.walk_frames[frame])
			for name, matrix in expected.items():
				self.assertMatricesAlmostEqual(arm.pose.bones[name].matrix, matrix, msg="{} frame {}".format(name, frame))

		# constant channels are keyed once
		channelbag = arm.animation_data.action.layers[0].strips[0].channelbag(arm.animation_data.action_slot)
		def keys(bone, path, index):
			return len(channelbag.fcurves.find('pose.bones["{}"].{}'.format(bone, path), index=index).keyframe_points)
		self.assertEqual(keys("ValveBiped.Bip01_L_Toe0", "rotation_euler", 0), 1)
		self.assertEqual(keys("ValveBiped.Bip01_L_Thigh", "rotation_euler", 1), 11)
		self.assertEqual(keys("ValveBiped.Bip01_Pelvis", "location", 0), 11)

		# a static animation keeps its length
		idle = self._qcAnimIndex(arm, "idle")
		self.assertEqual(bpy.ops.smd.qc_anim_load(index=idle), {'FINISHED'})
		utils = import_module("io_scene_valvesource").utils
		self.assertEqual(utils.animationLength(arm.animation_data), 4)
		self.assertEqual(scene.frame_end, 4)

		# switching back to a loaded animation
		arm.vs.qc_anims_active = index
		self.assertEqual(arm.animation_data.action_slot.handle, item.slot_handle)
		self.assertEqual(scene.frame_end, 10)

		self.assertEqual(bpy.ops.smd.qc_anim_unload(index=index), {'FINISHED'})
		self.assertFalse(self.anim_list.is_loaded(item))
		self.assertEqual(len(arm.vs.qc_action.slots), 1)

	def test_QcImport_EagerAnimations(self):
		arm = self._importBipedQc(lazyAnims=False, generateRig=False)
		self.assertEqual([item.name for item in arm.vs.qc_anims if self.anim_list.is_loaded(item)], ["late", "walk_all", "idle", "jump", "lean", "aim", "run"])
		self.assertEqual(len(arm.vs.qc_action.slots), 7) # not the deltas

	def _expectedFromLocals(self, locals):
		"""Armature space matrices of the synthetic leg from each bone's matrix relative to its parent."""
		matrices = []
		for (_, parent, _, _), local in zip(_biped_bones, locals):
			matrices.append(matrices[parent] @ local if parent >= 0 else local)
		return { bone[0]: matrix for bone, matrix in zip(_biped_bones, matrices) }

	def _poseLocals(self, pose):
		from mathutils import Matrix, Euler
		return [Matrix.Translation(pos) @ Euler(rot).to_matrix().to_4x4() for pos, rot in pose]

	def _assertPose(self, arm, expected, msg):
		self.bpy.context.view_layer.update()
		for name, matrix in expected.items():
			self.assertMatricesAlmostEqual(arm.pose.bones[name].matrix, matrix, msg="{}: {}".format(msg, name))

	def _skipWithoutSlots(self):
		if not import_module("io_scene_valvesource").utils.State.useActionSlots:
			self.skipTest("delta and blend sequences are composed in Blender 4.4+")

	def test_QcCompose_Math(self):
		self._skipWithoutSlots()
		from mathutils import Vector, Quaternion
		qc_compose = import_module("io_scene_valvesource").qc_compose
		a = (Vector((1, 2, 3)), Quaternion((1, 0, 0), 0.7))
		b = (Vector((0.5, 0, 1)), Quaternion((0, 1, 0), 0.4))
		for post in (True, False):
			loc, rot = qc_compose.apply_delta(b, qc_compose.subtract(a, b, post), post)
			self.assertAlmostEqual((loc - a[0]).length, 0, places=5)
			self.assertAlmostEqual(abs(rot.dot(a[1])), 1, places=5)
		self.assertLess(abs(qc_compose.subtract(a, b, True)[1].dot(qc_compose.subtract(a, b, False)[1])), 0.9999, "pre and post deltas differ")

		self.assertEqual(qc_compose.blend_grid(9, 9, 1), (9, 1))
		self.assertEqual(qc_compose.blend_grid(9, 0, 2), (3, 3))
		self.assertEqual(qc_compose.blend_grid(12, 3, 2), (3, 4))
		self.assertEqual(qc_compose.blend_grid(3, 0, 1), (3, 1))
		self.assertEqual(qc_compose.blend_axis(0, -180, 180, 9), (4, 0))
		self.assertEqual(qc_compose.blend_axis(180, -180, 180, 9), (7, 1))
		self.assertEqual(qc_compose.blend_axis(-500, -180, 180, 9), (0, 0))
		self.assertEqual(qc_compose.blend_axis(0.25, 0, 1, 2), (0, 0.25))
		weights = qc_compose.blend_weights(4, 2, [("x", -1, 1), ("y", -1, 1)], {"x": 0.5, "y": -1})
		self.assertEqual(sorted(weights), [(0, 0.25), (1, 0.75)])

	def test_QcImport_DeltaOnRest(self):
		self._skipWithoutSlots()
		from mathutils import Matrix
		arm = self._importBipedQc(generateRig=False)
		bpy = self.bpy
		self.assertEqual(bpy.ops.smd.qc_anim_load(index=self._qcAnimIndex(arm, "gesture")), {'FINISHED'})
		item = arm.vs.qc_anims[self._qcAnimIndex(arm, "gesture")]
		self.assertEqual(item.num_frames, 3)

		# the corrective animation cancels the root's rotation; the offsets are added to the rest pose, rotating each bone
		# in its own space
		rest = self._poseLocals(_biped_rest)
		expected = list(rest)
		expected[0] = Matrix.Translation((0.5, 0, 0)) @ rest[0]
		expected[1] = rest[1] @ Matrix.Rotation(0.4, 4, 'X')
		self._assertPose(arm, self._expectedFromLocals(expected), "post delta on the rest pose")

		# a pre delta rotates in the parent's space
		self.assertEqual(bpy.ops.smd.qc_anim_load(index=self._qcAnimIndex(arm, "gesture_pre")), {'FINISHED'})
		expected[1] = Matrix.Translation(rest[1].to_translation()) @ Matrix.Rotation(0.4, 4, 'X') @ rest[1].to_3x3().to_4x4()
		self._assertPose(arm, self._expectedFromLocals(expected), "pre delta on the rest pose")

	def test_QcImport_DeltaOnBase(self):
		self._skipWithoutSlots()
		from mathutils import Matrix
		arm = self._importBipedQc(generateRig=False)
		bpy = self.bpy
		scene = bpy.context.scene
		vs = arm.vs
		index = self._qcAnimIndex(arm, "gesture")
		self.assertEqual(bpy.ops.smd.qc_anim_load(index=index), {'FINISHED'})
		handle = vs.qc_anims[index].slot_handle

		# changing the base makes the loaded delta out of date; it is composed again when played
		vs.qc_delta_base = "jump"
		self.assertTrue(self.anim_list.is_stale(vs, vs.qc_anims[index]))
		self.anim_list.refresh_active(bpy.context, arm)
		item = vs.qc_anims[index]
		self.assertNotEqual(item.slot_handle, handle)
		self.assertEqual(item.baked_key, "base=jump")
		for frame in (0, 1, 2):
			scene.frame_set(frame)
			expected = self._poseLocals(_biped_walk_frame(frame))
			expected[0] = Matrix.Translation((0.5, 0, 0)) @ expected[0]
			expected[1] = expected[1] @ Matrix.Rotation(0.4, 4, 'X')
			self._assertPose(arm, self._expectedFromLocals(expected), "delta on jump, frame {}".format(frame))

		# a base which can't be one falls back to the rest pose
		vs.qc_delta_base = "gesture_pre"
		self.assertEqual(self.anim_list.qc_compose.delta_base(vs, item), None)

	def test_QcImport_BlendAndPoseParams(self):
		self._skipWithoutSlots()
		import math
		arm = self._importBipedQc(generateRig=False)
		bpy = self.bpy
		vs = arm.vs
		params = { p.name: p for p in vs.qc_pose_params }

		# at the default value, the middle animation plays as it is
		index = self._qcAnimIndex(arm, "lean")
		self.assertEqual(bpy.ops.smd.qc_anim_load(index=index), {'FINISHED'})
		self._assertPose(arm, self._expectedBipedPose(self._bipedPose(thigh_y=math.pi / 2)), "lean at move_yaw 0")
		handle = vs.qc_anims[index].slot_handle

		# halfway between the middle and the right animations
		params["move_yaw"].value = 45
		self.assertTrue(self.anim_list.refresh_active(bpy.context, arm))
		self.assertNotEqual(vs.qc_anims[index].slot_handle, handle)
		self._assertPose(arm, self._expectedBipedPose(self._bipedPose(thigh_y=math.pi / 2 + 0.2)), "lean at move_yaw 45")

		# values are clamped to the pose parameter's range
		params["move_yaw"].value = 1000
		self.assertEqual(params["move_yaw"].value, 180)
		self.anim_list.refresh_active(bpy.context, arm)
		self._assertPose(arm, self._expectedBipedPose(self._bipedPose(thigh_y=math.pi / 2 + 0.4)), "lean at the end of its range")

		# a 2D blend
		self.assertEqual(bpy.ops.smd.qc_anim_load(index=self._qcAnimIndex(arm, "aim")), {'FINISHED'})
		self._assertPose(arm, self._expectedBipedPose(self._bipedPose(pelvis=(5, 5, 40))), "aim at the centre")
		params["aim_yaw"].value = 45
		self.anim_list.refresh_active(bpy.context, arm)
		self._assertPose(arm, self._expectedBipedPose(self._bipedPose(pelvis=(10, 5, 40))), "aim right")

		# the other sequences are refreshed when they are played
		lean = vs.qc_anims[index]
		params["move_yaw"].value = 0
		self.assertTrue(self.anim_list.is_stale(vs, lean))
		vs.qc_anims_active = index
		self._assertPose(arm, self._expectedBipedPose(self._bipedPose(thigh_y=math.pi / 2)), "lean after changing move_yaw")

	def test_Rig_Generated(self):
		arm = self._importBipedQc()
		bpy = self.bpy
		scene = bpy.context.scene
		utils = import_module("io_scene_valvesource").utils
		rig = import_module("io_scene_valvesource").rig

		helpers = ["MCH-ValveBiped.Bip01_L_Thigh", "MCH-ValveBiped.Bip01_L_Calf", "OFS-ValveBiped.Bip01_L_Thigh", "OFS-ValveBiped.Bip01_L_Calf", "lfoot_ik", "lfoot_pole"]
		for name in helpers:
			bone = arm.data.bones[name]
			self.assertTrue(utils.isRigBone(bone), name)
			self.assertFalse(bone.use_deform, name)
		self.assertFalse(any(utils.isRigBone(arm.data.bones[b[0]]) for b in _biped_bones))

		# IK at rest reproduces the rest pose
		control = arm.pose.bones["lfoot_ik"]
		control[rig.IK_FK_PROP] = 1.0
		bpy.context.view_layer.update()
		for name, _, _, _ in _biped_bones:
			self.assertMatricesAlmostEqual(arm.pose.bones[name].matrix, arm.data.bones[name].matrix_local, places=2, msg=name + " in IK at rest")
		control[rig.IK_FK_PROP] = 0.0

		# snapping IK to an FK pose keeps the pose, frame after frame
		leg = ("ValveBiped.Bip01_L_Thigh", "ValveBiped.Bip01_L_Calf", "ValveBiped.Bip01_L_Foot")
		self.assertEqual(bpy.ops.smd.qc_anim_load(index=self._qcAnimIndex(arm, "walk_all")), {'FINISHED'})
		for frame in (5, 8, 2):
			rig.reset_ik_fk(arm)
			scene.frame_set(frame)
			expected = self._expectedBipedPose(self.walk_frames[frame])
			self.assertEqual(bpy.ops.smd.rig_snap_ik_to_fk(control="lfoot_ik"), {'FINISHED'})
			self.assertEqual(control[rig.IK_FK_PROP], 1.0)
			for name in leg:
				self.assertMatricesAlmostEqual(arm.pose.bones[name].matrix, expected[name], places=2, msg="{} after snapping IK to FK at frame {}".format(name, frame))

		# baking the FK animation to the IK controls
		rig.reset_ik_fk(arm)
		self.assertEqual(bpy.ops.smd.rig_snap_ik_to_fk(control="lfoot_ik", all_frames=True), {'FINISHED'})
		for frame in (0, 3, 10):
			scene.frame_set(frame)
			self.assertEqual(control[rig.IK_FK_PROP], 1.0)
			expected = self._expectedBipedPose(self.walk_frames[frame])
			for name in leg:
				self.assertMatricesAlmostEqual(arm.pose.bones[name].matrix, expected[name], places=2, msg="{} baked to IK, frame {}".format(name, frame))

		# the helper bones are never exported
		self.setupExport("RigExport")
		bpy.ops.object.select_all(action='DESELECT')
		arm.select_set(True)
		bpy.context.view_layer.objects.active = arm
		utils.State.update_scene(scene)
		for fmt in ('SMD', 'DMX'):
			self.sceneSettings.export_format = fmt
			self.assertEqual(bpy.ops.export_scene.smd(), {'FINISHED'})
		exported = [join(dirpath, f) for dirpath, _, files in os.walk(self.sceneSettings.export_path) for f in files]
		self.assertTrue(any(f.endswith(".smd") for f in exported) and any(f.endswith(".dmx") for f in exported), exported)
		for path in exported:
			if path.endswith(".smd"):
				with open(path) as f:
					text = f.read()
				self.assertIn("ValveBiped.Bip01_L_Foot", text)
				names = [line.split('"')[1] for line in text.split("nodes\n")[1].split("end\n")[0].splitlines()]
			else:
				names = [e.name for e in datamodel.load(path).elements if e.type in ("DmeJoint", "DmeDag")]
			for helper in helpers:
				self.assertNotIn(helper, names, path)

class Blender(_AddonTests, unittest.TestCase):
	def test_CompileQCsLoggerOverrideHack(self):
		export_smd = import_module("io_scene_valvesource").export_smd
		logger = export_smd.Logger()
		self.assertFalse(logger.log_errors)
		export_smd.SMD_OT_Compile.compileQCs(logger)
		self.assertTrue(logger.log_errors)

class Blender410(_AddonTests, unittest.TestCase):
	module_subdir = 'bpy410'

if __name__ == '__main__':
    unittest.main()
