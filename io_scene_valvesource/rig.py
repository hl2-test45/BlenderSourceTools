# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

# Adds IK controls to the arms and legs of an imported character. The limbs are found from the QC's
# $ikchain entries, then from its $hbox hit groups, then from bone names (ValveBiped or generic).
#
# The deforming skeleton is not changed, so exports and existing animations stay valid. For each limb
# (upper bone G, middle bone P, end bone E):
#
#   MCH-G, MCH-P   a chain pointing down the limb, which the IK constraint solves
#   OFS-G, OFS-P   children of the MCH bones with the exact rest pose of G and P
#   <limb>_ik      the IK target, with the rest pose of E
#   <limb>_pole    the pole target, placed in the direction the knee or elbow bends
#
# G and P copy the transforms of their OFS bones and E copies the rotation of <limb>_ik, with an
# influence driven by the "ik_fk" property of <limb>_ik: 0 plays the FK keys, 1 follows the IK controls.
# Every added bone is flagged with RIG_BONE_PROP, which keeps it out of exports.

import bpy, json, math, re
from bpy.props import StringProperty, BoolProperty
from mathutils import Vector, Matrix
from .utils import *

IK_FK_PROP = "ik_fk"
CHAIN_PROP = "bst_chain" # on <limb>_ik: "G;P;E;pole"
CONSTRAINT_NAME = "BST IK"
COLLECTION_CONTROLS = "IK Controls"
COLLECTION_MECHANISM = "IK Mechanism"

# Valve's human skeleton; the knee vectors are those of the HL2 character QCs
_valvebiped_limbs = (
	("rhand", "ValveBiped.Bip01_R_Hand", (0.707, 0.707, 0)),
	("lhand", "ValveBiped.Bip01_L_Hand", (0.707, 0.707, 0)),
	("rfoot", "ValveBiped.Bip01_R_Foot", (0.707, -0.707, 0)),
	("lfoot", "ValveBiped.Bip01_L_Foot", (0.707, -0.707, 0)),
)
_hitgroup_limbs = { 4: "lhand", 5: "rhand", 6: "lfoot", 7: "rfoot" }
_hitgroup_collections = { 1: "Head", 2: "Torso", 3: "Torso", 4: "Left Arm", 5: "Right Arm", 6: "Left Leg", 7: "Right Leg" }

class Chain:
	def __init__(self, name : str, end : str, knee : Vector | None):
		self.name = name
		self.end = end
		self.knee = knee # the direction the knee or elbow points, in the upper bone's space (like $ikchain)

#
# Finding the limbs
#

def _find_bone(bones, name : str):
	return bones.get(name) or next((bone for bone in bones if bone.name.lower() == name.lower()), None)

def _has_chain(bone):
	return bool(bone and bone.parent and bone.parent.parent)

def _name_tokens(name : str):
	return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]

def _side(name : str):
	for token in _name_tokens(name):
		if token in ("l", "left") or token.startswith("left"): return "l"
		if token in ("r", "right") or token.startswith("right"): return "r"
	return None

def find_chains(arm : bpy.types.Object, hints : dict) -> list[Chain]:
	bones = arm.data.bones
	chains = []

	def add(name, bone, knee):
		if _has_chain(bone) and not any(c.end == bone.name or c.name == name for c in chains):
			chains.append(Chain(name, bone.name, Vector(knee) if knee else None))

	# 1. the QC's IK chains
	for hint in hints.get("ikchains", []):
		add(hint["name"], _find_bone(bones, hint["bone"]), hint.get("knee"))
	if chains:
		return chains

	# 2. hit groups: a limb's end is the third bone of its group, counting from the torso
	hitgroups = { name: group for name, group in hints.get("hitgroups", {}).items() }
	for group, limb in _hitgroup_limbs.items():
		for bone in bones:
			if hitgroups.get(bone.name) != group or not _has_chain(bone):
				continue
			upper = bone.parent.parent
			if hitgroups.get(bone.parent.name) == group and hitgroups.get(upper.name) == group and (not upper.parent or hitgroups.get(upper.parent.name) != group):
				knee = next((k for n, b, k in _valvebiped_limbs if b == bone.name), None)
				add(limb, bone, knee)
				break
	if chains:
		return chains

	# 3. bone names
	for limb, name, knee in _valvebiped_limbs:
		add(limb, bones.get(name), knee)
	if chains:
		return chains

	for bone in bones:
		tokens = _name_tokens(bone.name)
		if any(t.startswith(("finger", "thumb", "toe", "index", "middle", "ring", "pinky")) for t in tokens):
			continue
		side = _side(bone.name)
		if not side:
			continue
		if any("hand" in t or "wrist" in t for t in tokens):
			add(side + "hand", bone, None)
		elif any("foot" in t or "ankle" in t for t in tokens):
			add(side + "foot", bone, None)
	return chains

def has_rig(arm : bpy.types.Object):
	return any(CHAIN_PROP in pb for pb in arm.pose.bones)

def rig_controls(arm : bpy.types.Object):
	"""(control pose bone, [G, P, E, pole]) for each limb of the rig."""
	for pb in arm.pose.bones:
		if CHAIN_PROP in pb:
			yield pb, str(pb[CHAIN_PROP]).split(";")

def set_ik_fk(arm : bpy.types.Object, control : bpy.types.PoseBone, value : float):
	control[IK_FK_PROP] = value
	arm.update_tag() # custom property changes made from Python don't re-run the drivers which read them

def reset_ik_fk(arm : bpy.types.Object):
	for pb, _ in rig_controls(arm):
		set_ik_fk(arm, pb, 0.0)

#
# Building
#

def _bend_direction(root : Vector, mid : Vector, tip : Vector, knee : Vector | None) -> Vector:
	"""The direction the middle joint points, perpendicular to the limb. The rest pose is used when it is visibly
	bent the way the QC says, so that switching to IK doesn't twist the limb."""
	axis = (tip - root).normalized()
	offset = mid - root
	bent = offset - axis * offset.dot(axis)
	knee_perp = None
	if knee is not None:
		knee_perp = knee - axis * knee.dot(axis)
		knee_perp = knee_perp.normalized() if knee_perp.length > 1e-6 else None
	if bent.length > 1e-3 * (tip - root).length and (knee_perp is None or bent.normalized().dot(knee_perp) > 0):
		return bent.normalized()
	if knee_perp is not None:
		return knee_perp
	for fallback in (Vector((0,-1,0)), Vector((1,0,0))): # a straight limb and no hints: guess forward
		perp = fallback - axis * fallback.dot(axis)
		if perp.length > 1e-3:
			return perp.normalized()
	return Vector((0,0,1))

def _signed_angle(u : Vector, v : Vector, normal : Vector):
	angle = u.angle(v)
	if u.cross(v).angle(normal) < 1:
		angle = -angle
	return angle

def _pole_angle(base : bpy.types.EditBone, tip : bpy.types.EditBone, pole_location : Vector):
	pole_normal = (tip.tail - base.head).cross(pole_location - base.head)
	projected_pole_axis = pole_normal.cross(base.tail - base.head)
	return _signed_angle(base.x_axis, projected_pole_axis, base.tail - base.head)

def _widget(name : str, kind : str) -> bpy.types.Object:
	ob = bpy.data.objects.get(name)
	if ob and ob.type == 'MESH':
		return ob
	if kind == 'CUBE':
		verts = [(x, y, z) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]
		edges = [(0,1),(2,3),(4,5),(6,7),(0,2),(1,3),(4,6),(5,7),(0,4),(1,5),(2,6),(3,7)]
	else: # octahedron
		verts = [(0.5,0,0),(-0.5,0,0),(0,0.5,0),(0,-0.5,0),(0,0,0.5),(0,0,-0.5)]
		edges = [(a, b) for a in (0,1) for b in (2,3,4,5)] + [(a, b) for a in (2,3) for b in (4,5)]
	mesh = bpy.data.meshes.new(name)
	mesh.from_pydata(verts, edges, [])
	ob = bpy.data.objects.new(name, mesh)
	ob.use_fake_user = True
	return ob

def _knee_in_armature_space(arm : bpy.types.Object, upper : bpy.types.Bone, knee : Vector | None):
	if knee is None:
		return None
	matrix = upper.matrix_local @ mat_BlenderToSMD if arm.data.vs.legacy_rotation else upper.matrix_local
	return (matrix.to_3x3() @ knee).normalized()

def _bone_collection(arm : bpy.types.Object, name : str, visible = True):
	collection = arm.data.collections.get(name) or arm.data.collections.new(name)
	collection.is_visible = visible
	return collection

def remove_rig(arm : bpy.types.Object):
	"""Deletes the helper bones and the constraints and drivers which the rig added to the deforming bones."""
	ad = arm.animation_data
	if ad:
		for fcurve in [fc for fc in ad.drivers if 'constraints["{}"]'.format(CONSTRAINT_NAME) in fc.data_path]:
			ad.drivers.remove(fcurve)
	for pb in arm.pose.bones:
		for constraint in [c for c in pb.constraints if c.name == CONSTRAINT_NAME]:
			pb.constraints.remove(constraint)

	helpers = [bone.name for bone in arm.data.bones if isRigBone(bone)]
	if helpers:
		with _edit_mode(arm):
			for name in helpers:
				edit_bone = arm.data.edit_bones.get(name)
				if edit_bone:
					arm.data.edit_bones.remove(edit_bone)
	return len(helpers)

class _edit_mode:
	"""Puts the armature in edit mode, then back in object mode, restoring the active object."""
	def __init__(self, arm):
		self.arm = arm
	def __enter__(self):
		view_layer = bpy.context.view_layer
		self.prev_active = view_layer.objects.active
		self.prev_mode = self.arm.mode
		if self.prev_active and self.prev_active != self.arm and self.prev_active.mode != 'OBJECT':
			bpy.ops.object.mode_set(mode='OBJECT')
		view_layer.objects.active = self.arm
		if self.arm.mode != 'EDIT':
			bpy.ops.object.mode_set(mode='EDIT')
	def __exit__(self, *args):
		bpy.ops.object.mode_set(mode='POSE' if self.prev_mode == 'POSE' else 'OBJECT')
		if self.prev_active and self.prev_active.name in bpy.context.view_layer.objects:
			bpy.context.view_layer.objects.active = self.prev_active

def generate_rig(arm : bpy.types.Object, hints : dict, logger : Logger | None = None, only_if_found = False) -> int:
	"""Adds IK controls to the arm and leg chains of arm. Returns the number of limbs rigged."""
	chains = find_chains(arm, hints or {})
	if not chains:
		if logger and not only_if_found:
			logger.warning(get_id("rig_err_nochains", True).format(arm.name))
		return 0

	remove_rig(arm)
	data = arm.data
	built = [] # (chain, G, P, E, control, pole, pole angle, control length)

	with _edit_mode(arm):
		edit_bones = data.edit_bones
		def new_bone(name, head, tail, roll, parent, connect = False):
			bone = edit_bones.new(name)
			bone.head = head
			bone.tail = tail
			bone.roll = roll
			bone.parent = parent
			bone.use_connect = connect
			bone.use_deform = False
			return bone

		for chain in chains:
			end = edit_bones[chain.end]
			middle = end.parent
			upper = middle.parent
			root, mid, tip = upper.head.copy(), middle.head.copy(), end.head.copy()
			limb_length = (mid - root).length + (tip - mid).length
			if (mid - root).length < 1e-4 or (tip - mid).length < 1e-4:
				if logger: logger.warning(get_id("rig_err_badchain", True).format(chain.name))
				continue

			bend = _bend_direction(root, mid, tip, _knee_in_armature_space(arm, data.bones[upper.name], chain.knee))

			mch_upper = new_bone("MCH-" + upper.name, root, mid, 0, upper.parent)
			mch_upper.align_roll(bend)
			mch_middle = new_bone("MCH-" + middle.name, mid, tip, 0, mch_upper, connect=True)
			mch_middle.align_roll(bend)
			new_bone("OFS-" + upper.name, upper.head, upper.tail, upper.roll, mch_upper)
			new_bone("OFS-" + middle.name, middle.head, middle.tail, middle.roll, mch_middle)

			# the IK control has the end bone's orientation, so that the end bone can copy its rotation
			control_length = limb_length * 0.15
			direction = (end.tail - end.head).normalized()
			control = new_bone(chain.name + "_ik", tip, tip + direction * control_length, end.roll, None)
			pole_location = mid + bend * limb_length * 0.6
			pole = new_bone(chain.name + "_pole", pole_location, pole_location + bend * control_length * 0.5, 0, None)

			built.append((chain, upper.name, middle.name, end.name, control.name, pole.name, _pole_angle(mch_upper, mch_middle, pole_location), control_length))

	if not built:
		return 0

	# flag the helper bones, and sort them into bone collections
	controls = _bone_collection(arm, COLLECTION_CONTROLS)
	mechanism = _bone_collection(arm, COLLECTION_MECHANISM, visible=False)
	cube = _widget("WGT-bst_ik", 'CUBE')
	octahedron = _widget("WGT-bst_pole", 'OCTAHEDRON')

	for chain, upper, middle, end, control, pole, pole_angle, control_length in built:
		for name in ("MCH-" + upper, "MCH-" + middle, "OFS-" + upper, "OFS-" + middle, control, pole):
			bone = data.bones[name]
			bone[RIG_BONE_PROP] = 1
			for collection in list(bone.collections):
				collection.unassign(bone)
			(controls if name in (control, pole) else mechanism).assign(bone)

		pose_bones = arm.pose.bones
		mch_middle = pose_bones["MCH-" + middle]
		mch_middle.lock_ik_y = mch_middle.lock_ik_z = True # a hinge, bending around X, towards the pole
		ik = mch_middle.constraints.new('IK')
		ik.name = CONSTRAINT_NAME
		ik.target = arm
		ik.subtarget = control
		ik.pole_target = arm
		ik.pole_subtarget = pole
		ik.pole_angle = pole_angle
		ik.chain_count = 2

		control_pb = pose_bones[control]
		control_pb[IK_FK_PROP] = 0.0
		control_pb.id_properties_ui(IK_FK_PROP).update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, description=get_id("rig_ikfk_tip"))
		control_pb[CHAIN_PROP] = ";".join((upper, middle, end, pole))
		control_pb.custom_shape = cube
		pose_bones[pole].custom_shape = octahedron
		for pb in (control_pb, pose_bones[pole]):
			pb.bone.color.palette = 'THEME09'
			pb.rotation_mode = pose_bones[end].rotation_mode

		constraints = []
		for deform, source in ((upper, "OFS-" + upper), (middle, "OFS-" + middle)):
			constraint = pose_bones[deform].constraints.new('COPY_TRANSFORMS')
			constraint.subtarget = source
			constraints.append(constraint)
		constraint = pose_bones[end].constraints.new('COPY_ROTATION')
		constraint.subtarget = control
		constraints.append(constraint)

		for constraint in constraints:
			constraint.name = CONSTRAINT_NAME
			constraint.target = arm
			constraint.influence = 0
			driver = constraint.driver_add("influence").driver
			driver.type = 'AVERAGE'
			variable = driver.variables.new()
			variable.name = IK_FK_PROP
			variable.type = 'SINGLE_PROP'
			variable.targets[0].id_type = 'OBJECT' # not the armature data: the exporter's copy of the object must drive itself
			variable.targets[0].id = arm
			variable.targets[0].data_path = 'pose.bones["{}"]["{}"]'.format(bpy.utils.escape_identifier(control), IK_FK_PROP)

	_sort_deform_bones(arm, hints or {})
	print("- Rigged {} limbs of \"{}\": {}".format(len(built), arm.name, ", ".join(b[0].name for b in built)))
	return len(built)

def _sort_deform_bones(arm : bpy.types.Object, hints : dict):
	"""Puts the deforming bones in body part collections, using the QC's hit groups and bone names."""
	hitgroups = hints.get("hitgroups", {})
	categories = {}
	for bone in arm.data.bones: # parents come before their children
		if isRigBone(bone):
			continue
		tokens = _name_tokens(bone.name)
		category = _hitgroup_collections.get(hitgroups.get(bone.name))
		if not category and any(t.startswith(("finger", "thumb")) for t in tokens):
			category = "Fingers"
		if not category:
			category = categories.get(bone.parent.name) if bone.parent else None
		if category == "Fingers" and not any(t.startswith(("finger", "thumb")) for t in tokens):
			category = categories.get(bone.parent.parent.name) if bone.parent and bone.parent.parent else None
		categories[bone.name] = category or "Torso"
	if not hitgroups and not any(c != "Torso" for c in categories.values()):
		return # nothing to go on
	for name, category in categories.items():
		collection = _bone_collection(arm, category)
		bone = arm.data.bones[name]
		if collection not in list(bone.collections):
			collection.assign(bone)

#
# Snapping
#

def _chain_bones(arm, control_name):
	control = arm.pose.bones[control_name]
	upper, middle, end, pole = str(control[CHAIN_PROP]).split(";")
	pb = arm.pose.bones
	return control, pb[upper], pb[middle], pb[end], pb[pole]

def _pole_position(upper, middle, end, pole_rest : Vector, limb_length : float):
	root, mid, tip = upper.head, middle.head, end.head
	axis = (tip - root).normalized()
	offset = mid - root
	bent = offset - axis * offset.dot(axis)
	if bent.length < 1e-3 * limb_length: # straight: keep the pole where the rest pose puts it, relative to the upper bone
		return upper.matrix @ upper.bone.matrix_local.inverted() @ pole_rest
	return mid + bent.normalized() * limb_length * 0.6

def _key(pb, frame, rotation = True):
	pb.keyframe_insert("location", frame=frame, group=pb.name)
	if rotation:
		path = "rotation_quaternion" if pb.rotation_mode == 'QUATERNION' else ("rotation_axis_angle" if pb.rotation_mode == 'AXIS_ANGLE' else "rotation_euler")
		pb.keyframe_insert(path, frame=frame, group=pb.name)

def snap_ik_to_fk(context, arm, control_name, keyframe = False, frames = None):
	"""Moves a limb's IK controls to where its FK pose is, then switches the limb to IK."""
	control, upper, middle, end, pole = _chain_bones(arm, control_name)
	limb_length = (middle.bone.head_local - upper.bone.head_local).length + (end.bone.head_local - middle.bone.head_local).length
	pole_rest = pole.bone.head_local.copy()
	scene = context.scene
	frames = list(frames) if frames else [scene.frame_current]

	# read the FK pose, with the IK switch muted if it is animated
	switch_path = 'pose.bones["{}"]["{}"]'.format(bpy.utils.escape_identifier(control.name), IK_FK_PROP)
	switch_curves = [fc for fc in _animation_fcurves(arm) if fc.data_path == switch_path and not fc.mute]
	for fc in switch_curves: fc.mute = True
	set_ik_fk(arm, control, 0.0)
	poses = []
	current = scene.frame_current
	try:
		for frame in frames:
			scene.frame_set(frame) # also evaluates the switch change
			poses.append((frame, end.matrix.copy(), _pole_position(upper, middle, end, pole_rest, limb_length)))
	finally:
		for fc in switch_curves: fc.mute = False
		if len(frames) > 1:
			scene.frame_set(current)

	for frame, end_matrix, pole_location in poses:
		control.matrix = end_matrix
		pole.matrix = Matrix.Translation(pole_location) @ pole.bone.matrix_local.to_3x3().to_4x4()
		if keyframe:
			_key(control, frame)
			_key(pole, frame, rotation=False)

	set_ik_fk(arm, control, 1.0)
	if keyframe:
		control.keyframe_insert('["{}"]'.format(IK_FK_PROP), frame=frames[0], group=control.name)
	context.view_layer.update()

def snap_fk_to_ik(context, arm, control_name, keyframe = False):
	"""Poses a limb's FK bones where its IK controls put it, then switches the limb to FK."""
	control, upper, middle, end, pole = _chain_bones(arm, control_name)
	matrices = [(pb, pb.matrix.copy()) for pb in (upper, middle, end)]
	set_ik_fk(arm, control, 0.0)
	context.view_layer.update()
	for pb, matrix in matrices: # parent first: each assignment is relative to the parent's current pose
		pb.matrix = matrix
		context.view_layer.update()
		if keyframe:
			_key(pb, context.scene.frame_current)
	if keyframe:
		control.keyframe_insert('["{}"]'.format(IK_FK_PROP), group=control.name)

def _animation_fcurves(arm):
	ad = arm.animation_data
	if not ad or not ad.action:
		return []
	if State.useActionSlots:
		if not ad.action_slot or not ad.action.layers or not ad.action.layers[0].strips:
			return []
		channelbag = ad.action.layers[0].strips[0].channelbag(ad.action_slot)
		return list(channelbag.fcurves) if channelbag else []
	return list(ad.action.fcurves)

#
# UI
#

class _RigOperator:
	@classmethod
	def poll(cls, context):
		from .anim_list import get_armature
		arm = get_armature(context)
		return bool(arm and context.mode in ('OBJECT', 'POSE'))

class SMD_OT_GenerateSourceRig(_RigOperator, bpy.types.Operator):
	bl_idname = "smd.generate_source_rig"
	bl_label = get_id("rig_generate")
	bl_description = get_id("rig_generate_tip")
	bl_options = {'REGISTER', 'UNDO'}

	def execute(self, context):
		from .anim_list import get_armature
		arm = get_armature(context)
		try:
			hints = json.loads(arm.data.vs.rig_hints) if arm.data.vs.rig_hints else {}
		except ValueError:
			hints = {}
		logger = Logger()
		count = generate_rig(arm, hints, logger)
		for message in logger.log_warnings + logger.log_errors:
			self.report({'WARNING'}, message)
		if not count:
			return {'CANCELLED'}
		self.report({'INFO'}, get_id("rig_generated", True).format(count, arm.name))
		return {'FINISHED'}

class SMD_OT_RemoveSourceRig(_RigOperator, bpy.types.Operator):
	bl_idname = "smd.remove_source_rig"
	bl_label = get_id("rig_remove")
	bl_description = get_id("rig_remove_tip")
	bl_options = {'REGISTER', 'UNDO'}

	def execute(self, context):
		from .anim_list import get_armature
		remove_rig(get_armature(context))
		return {'FINISHED'}

class SMD_OT_RigSnapIkToFk(_RigOperator, bpy.types.Operator):
	bl_idname = "smd.rig_snap_ik_to_fk"
	bl_label = get_id("rig_snap_ik")
	bl_description = get_id("rig_snap_ik_tip")
	bl_options = {'REGISTER', 'UNDO'}

	control : StringProperty(options={'HIDDEN'})
	keyframe : BoolProperty(name=get_id("rig_keyframe"), description=get_id("rig_keyframe_tip"), default=False)
	all_frames : BoolProperty(name=get_id("rig_all_frames"), description=get_id("rig_all_frames_tip"), default=False)

	def execute(self, context):
		from .anim_list import get_armature
		arm = get_armature(context)
		controls = [pb.name for pb, _ in rig_controls(arm)] if not self.control else [self.control]
		frames = range(context.scene.frame_start, context.scene.frame_end + 1) if self.all_frames else None
		for control in controls:
			snap_ik_to_fk(context, arm, control, keyframe=self.keyframe or self.all_frames, frames=frames)
		return {'FINISHED'}

class SMD_OT_RigSnapFkToIk(_RigOperator, bpy.types.Operator):
	bl_idname = "smd.rig_snap_fk_to_ik"
	bl_label = get_id("rig_snap_fk")
	bl_description = get_id("rig_snap_fk_tip")
	bl_options = {'REGISTER', 'UNDO'}

	control : StringProperty(options={'HIDDEN'})
	keyframe : BoolProperty(name=get_id("rig_keyframe"), description=get_id("rig_keyframe_tip"), default=False)

	def execute(self, context):
		from .anim_list import get_armature
		arm = get_armature(context)
		controls = [pb.name for pb, _ in rig_controls(arm)] if not self.control else [self.control]
		for control in controls:
			snap_fk_to_ik(context, arm, control, keyframe=self.keyframe)
		return {'FINISHED'}

class SMD_PT_SourceRig(bpy.types.Panel):
	bl_label = get_id("rig_title")
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'UI'
	bl_category = "Source"

	@classmethod
	def poll(cls, context):
		from .anim_list import get_armature
		return get_armature(context) is not None

	def draw(self, context):
		from .anim_list import get_armature
		l = self.layout
		arm = get_armature(context)
		controls = list(rig_controls(arm))
		if not controls:
			l.operator(SMD_OT_GenerateSourceRig.bl_idname, icon='CON_KINEMATIC')
			return

		col = l.column(align=True)
		for control, _ in controls:
			row = col.row(align=True)
			row.prop(control, '["{}"]'.format(IK_FK_PROP), text=control.name.removesuffix("_ik"), slider=True)
			row.operator(SMD_OT_RigSnapIkToFk.bl_idname, text="", icon='CON_KINEMATIC').control = control.name
			row.operator(SMD_OT_RigSnapFkToIk.bl_idname, text="", icon='BONE_DATA').control = control.name

		row = l.row(align=True)
		row.operator(SMD_OT_RigSnapIkToFk.bl_idname, text=get_id("rig_bake_ik", True), icon='KEYINGSET').all_frames = True
		row = l.row(align=True)
		row.operator(SMD_OT_GenerateSourceRig.bl_idname, text=get_id("rig_regenerate", True), icon='FILE_REFRESH')
		row.operator(SMD_OT_RemoveSourceRig.bl_idname, text="", icon='TRASH')

classes = (
	SMD_OT_GenerateSourceRig,
	SMD_OT_RemoveSourceRig,
	SMD_OT_RigSnapIkToFk,
	SMD_OT_RigSnapFkToIk,
	SMD_PT_SourceRig,
)
