#  Copyright (c) 2014 Tom Edwards contact@steamreview.org
#
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

import bpy, bmesh, random, collections
from bpy import ops
from bpy.app.translations import pgettext
from bpy.props import StringProperty, CollectionProperty, BoolProperty, EnumProperty
from mathutils import Quaternion, Euler
from math import ceil
from typing import cast
from .utils import *
from . import datamodel, ordered_set, flex

# QC option keywords which can follow a $sequence or $animation. Everything else at the start of a line
# inside a sequence block is a reference to an animation file or to a $animation.
_qc_sequence_options = frozenset((
	"activity", "addlayer", "align", "alignbone", "alignboneto", "alignto", "animation", "autoik", "autolay", "autoplay",
	"blend", "blendcenter", "blendcomp", "blendlayer", "blendref", "blendwidth", "bonesaveframe", "calcblend", "cmd",
	"compress", "counterrotate", "counterrotateto", "delta", "derivative", "event", "exit", "fadein", "fadeout",
	"fixuploop", "fps", "frame", "frames", "hidden", "ik", "iklock", "ikrule", "keyvalues", "localhierarchy", "loop",
	"motionrollback", "noanimation", "noanimblock", "noanimblockstall", "noanimload", "noautoik", "noforceloop", "node",
	"numframes", "origin", "posecycle", "post", "predelta", "presubtract", "realtime", "reverse", "rotate", "rotateto",
	"rtransition", "scale", "snap", "spline", "startloop", "subtract", "transision", "transition", "walkframe",
	"weightlist", "worldspace", "xfade",
	"lx", "ly", "lz", "lxr", "lyr", "lzr", "lm", "lq", "x", "y", "z", "xr", "yr", "zr",
))

# The number of arguments of the options which are read, as (minimum, maximum). The words after any other option
# are its arguments until the next option keyword.
_qc_option_arity = {
	"fps": (1, 1), "activity": (1, 2), "blend": (3, 3), "blendwidth": (1, 1), "subtract": (1, 2), "presubtract": (1, 2),
	"addlayer": (1, 1), "blendlayer": (1, 6),
}

def _normaliseQcPath(path):
	if (os.path.sep == '/'):
		path = path.replace('\\','/')
	return os.path.normpath(path)

def _qcFileKey(path):
	return os.path.normcase(os.path.realpath(path))

def _findLayerCollection(layer_collection : bpy.types.LayerCollection, collection : bpy.types.Collection):
	if layer_collection.collection == collection:
		return layer_collection
	for child in layer_collection.children:
		found = _findLayerCollection(child, collection)
		if found:
			return found

class _QcSequenceBlock:
	"""Collects the animation references and options of one $sequence or $animation while its lines are read."""
	def __init__(self, keyword : str, name : str, raw_name : str, source_qc : str):
		self.keyword = keyword
		self.name = name # lowercase on Windows, like the rest of the parsed QC
		self.raw_name = raw_name
		self.source_qc = source_qc
		self.refs : list[tuple[str, str, str]] = [] # (word, raw word, current QC folder)
		self.options : list[tuple[str, list[str], list[str]]] = [] # (option, arguments, raw arguments)
		self.depth = 0
		self.opened = False
		self.awaiting_brace = False # header line without "{": the block may open on the next line

	def read(self, words : list[str], raw_words : list[str], cd : str, start = 0):
		refs_allowed = self.keyword == "$sequence" # an $animation's only reference is the file after its name
		option = None # the option whose arguments are being read
		for i in range(start, len(words)):
			word = words[i]
			if word == "{":
				self.depth += 1
				self.opened = True
				self.awaiting_brace = False
				option = None
			elif word == "}":
				self.depth -= 1
				refs_allowed = False
				option = None
			elif self.depth > 1:
				pass # nested block, e.g. "{ event 5004 0 "sound" }" or keyvalues
			else:
				keyword = word.lower()
				if option:
					low, high = _qc_option_arity.get(option[0], (0, 1 << 16))
					if len(option[1]) < low or (len(option[1]) < high and keyword not in _qc_sequence_options):
						option[1].append(word)
						option[2].append(raw_words[i])
						continue
				if keyword in _qc_sequence_options:
					refs_allowed = False
					option = (keyword, [], [])
					self.options.append(option)
				elif refs_allowed:
					self.refs.append((word, raw_words[i], cd))

	@property
	def finished(self):
		return self.depth <= 0 and self.opened

	def has(self, option : str):
		return any(o[0] == option for o in self.options)

	def args(self, option : str) -> list[str]:
		"""The raw arguments of the last occurrence of an option."""
		return next((o[2] for o in reversed(self.options) if o[0] == option), [])

	@property
	def fps(self) -> float | None:
		try:
			return float(self.args("fps")[0])
		except (IndexError, ValueError):
			return None

	@property
	def is_delta(self):
		return self.has("delta") or self.has("predelta")

	@property
	def subtract(self) -> tuple[str, int, bool] | None:
		"""(animation, frame, post). "subtract" makes a delta which is applied after the base rotation, "presubtract" one applied before it."""
		for option, _, args in reversed(self.options):
			if option in ("subtract", "presubtract") and args:
				try:
					frame = int(float(args[1])) if len(args) > 1 else 0
				except ValueError:
					frame = 0
				return (args[0], frame, option == "subtract")
		return None

	@property
	def blend_params(self) -> list[tuple[str, float, float]]:
		params = []
		for option, _, args in self.options:
			if option == "blend" and len(args) == 3:
				try:
					params.append((args[0], float(args[1]), float(args[2])))
				except ValueError:
					pass
		return params

	@property
	def blend_width(self) -> int:
		try:
			return max(0, int(self.args("blendwidth")[0]))
		except (IndexError, ValueError):
			return 0

class SmdImportCore(Logger):
	"""The SMD/VTA/DMX/QC reading code, shared by the import operator and by AnimLoader."""
	qc : QcInfo | None = None
	smd : SmdInfo

	# Settings which the import operator exposes as properties. Plain Python users of this class set them in __init__.
	target_armature : bpy.types.Object | None = None # import into this armature instead of searching the scene for one
	target_action : bpy.types.Action | None = None # add animation slots to this action instead of the armature's current one
	assign_slot = True # make each imported animation the armature's active one
	modifyScene = True # rename the scene and change its frame range
	parentCollection : bpy.types.Collection | None = None # link new collections here instead of to the scene
	includeSearchPath = ""
	lazyAnims = False
	generateRig = False
	capture_only = False # read animations into SmdInfo.captured instead of keying them

	def ensureAnimationBonesValidated(self):
		if self.smd.jobType == ANIM and self.append == 'APPEND' and (hasattr(self.smd,"a") or self.findArmature()):
			print("- Appending bones from animations is destructive; switching Bone Append Mode to \"Validate\"")
			self.append = 'VALIDATE'

	# Datablock names are limited to 63 bytes of UTF-8
	def truncate_id_name(self, name : str, id_type):
		truncated = bytes(name,'utf8')	
		if len(truncated) < 64:
			return name

		truncated = truncated[:63]
		while truncated:
			try:
				truncated = truncated.decode('utf8')
				break
			except UnicodeDecodeError:
				truncated = truncated[:-1]
		self.error(get_id("importer_err_namelength",True).format(pgettext(id_type if isinstance(id_type,str) else id_type.__name__), name, truncated))
		return str(truncated)

	# Identifies what type of SMD this is. Cannot tell between reference/lod/collision meshes!
	def scanSMD(self):
		smd = self.smd
		for line in smd.file:
			if line == "triangles\n":
				smd.jobType = REF
				print("- This is a mesh")
				break
			if line == "vertexanimation\n":
				print("- This is a flex animation library")
				smd.jobType = FLEX
				break

		# Finished the file

		if smd.jobType == None:
			print("- This is a skeltal animation or pose") # No triangles, no flex - must be animation
			smd.jobType = ANIM
			self.ensureAnimationBonesValidated()

		smd.file.seek(0,0) # rewind to start of file
		
	# joins up "quoted values" that would otherwise be delimited, removes comments
	# With return_raw=True, also returns the same words without any case folding. Never parse a line twice:
	# block comment state is carried over from one call to the next.
	def parseQuoteBlockedLine(self,line,lower=True,return_raw=False):
		if len(line) == 0:
			return (["\n"], ["\n"]) if return_raw else ["\n"]

		qc = self.qc
		words = []
		raw_words = []
		last_word_start = 0
		in_quote = in_whitespace = False

		# The last char of the last line in the file was missed
		if line[-1] != "\n":
			line += "\n"

		for i in range(len(line)):
			char = line[i]
			nchar = pchar = None
			if i < len(line)-1:
				nchar = line[i+1]
			if i > 0:
				pchar = line[i-1]

			# line comment - precedence over block comment
			if not in_quote and ((char == "/" and nchar == "/") or char in ['#',';']):
				if i > 0:
					i = i-1 # last word will be caught after the loop
				break # nothing more this line

			if qc:
				#block comment
				if qc.in_block_comment:
					if char == "/" and pchar == "*": # done backwards so we don't have to skip two chars
						qc.in_block_comment = False
					continue
				elif char == "/" and nchar == "*": # note: nchar, not pchar
					qc.in_block_comment = True
					continue

			# quote block
			if char == "\"" and not pchar == "\\": # quotes can be escaped
				in_quote = (in_quote == False)
			if not in_quote:
				if char in [" ","\t"]:
					cur_word = line[last_word_start:i].strip("\"") # characters between last whitespace and here
					if len(cur_word) > 0:
						raw_words.append(cur_word)
						if (lower and os.name == 'nt') or cur_word[0] == "$":
							cur_word = cur_word.lower()
						words.append(cur_word)
					last_word_start = i+1 # we are in whitespace, first new char is the next one

		# catch last word and any '{'s crashing into it
		needBracket = False
		cur_word = line[last_word_start:i]
		if cur_word.endswith("{"):
			needBracket = True

		cur_word = cur_word.strip("\"{")
		if len(cur_word) > 0:
			words.append(cur_word)
			raw_words.append(cur_word)

		if needBracket:
			words.append("{")
			raw_words.append("{")

		if line.endswith("\\\\\n") and (len(words) == 0 or words[-1] != "\\\\"):
			words.append("\\\\") # macro continuation beats everything
			raw_words.append("\\\\")

		return (words, raw_words) if return_raw else words

	# Bones
	def readNodes(self):
		smd = self.smd
		boneParents = {}

		def addBone(id,name,parent):
			bone = smd.a.data.edit_bones.new(self.truncate_id_name(name,bpy.types.Bone))
			bone.tail = 0,5,0 # Blender removes zero-length bones

			smd.boneIDs[int(id)] = bone.name
			boneParents[bone.name] = int(parent)

			return bone

		if self.append != 'NEW_ARMATURE':
			smd.a = smd.a or self.findArmature()			
			if smd.a:

				append = self.append == 'APPEND' and smd.jobType in [REF,ANIM]

				if append:
					bpy.context.view_layer.objects.active = smd.a
					smd.a.hide_set(False)
					ops.object.mode_set(mode='EDIT',toggle=False)
					self.existingBones.extend([b.name for b in smd.a.data.bones])
				
				missing = validated = 0
				for line in smd.file:
					if smdBreak(line): break
					if smdContinue(line): continue
		
					id, name, parent = self.parseQuoteBlockedLine(line,lower=False)[:3]
					id = int(id)
					parent = int(parent)

					targetBone = smd.a.data.bones.get(name) # names, not IDs, are the key
			
					if targetBone: validated += 1
					elif append:
						targetBone = addBone(id,name,parent)
					else: missing += 1

					if not smd.boneIDs.get(parent):
						smd.phantomParentIDs[id] = parent

					smd.boneIDs[id] = targetBone.name if targetBone else name
		
				print("- Validated {} bones against armature \"{}\"{}".format(validated, smd.a.name, " (could not find {})".format(missing) if missing > 0 else ""))

		if not smd.a:		
			smd.a = self.createArmature(self.truncate_id_name((self.qc.jobName if self.qc else smd.jobName) + "_skeleton",bpy.types.Armature))
			if self.qc: self.qc.a = smd.a
			smd.a.data.vs.implicit_zero_bone = False # Too easy to break compatibility, plus the skeleton is probably set up already
		
			ops.object.mode_set(mode='EDIT',toggle=False)

			# Read bone definitions from disc
			for line in smd.file:		
				if smdBreak(line): break
				if smdContinue(line): continue

				id,name,parent = self.parseQuoteBlockedLine(line,lower=False)[:3]
				addBone(id,name,parent)

		# Apply parents now that all bones exist
		for bone_name,parent_id in boneParents.items():
			if parent_id != -1:
				smd.a.data.edit_bones[bone_name].parent = smd.a.data.edit_bones[ smd.boneIDs[parent_id] ]

		if smd.a.mode == 'EDIT' or smd.jobType != ANIM: # animations only need a mode switch if bones were added
			ops.object.mode_set(mode='OBJECT')
		if boneParents: print("- Imported {} new bones".format(len(boneParents)) )

		if len(smd.a.data.bones) > 128:
			self.warning(get_id("importer_err_bonelimit_smd"))

	@classmethod
	def findArmature(cls):
		# Search the current scene for an existing armature - there can only be one skeleton in a Source model
		if bpy.context.active_object and bpy.context.active_object.type == 'ARMATURE':
			return bpy.context.active_object
		
		def isArmIn(list):
			for ob in list:
				if ob.type == 'ARMATURE':
					return ob

		a = isArmIn(bpy.context.selected_objects) # armature in the selection?
		if a: return a

		for ob in bpy.context.selected_objects:
			if ob.type == 'MESH':
				a = ob.find_armature() # armature modifying a selected object?
				if a: return a
					
		return isArmIn(bpy.context.scene.objects) # armature in the scene at all?

	def createArmature(self,armature_name):
		smd = self.smd
		if bpy.context.active_object:
			ops.object.mode_set(mode='OBJECT',toggle=False)
		a = bpy.data.objects.new(armature_name,bpy.data.armatures.new(armature_name))
		a.show_in_front = True
		a.data.display_type = 'STICK'
		bpy.context.scene.collection.objects.link(a)
		for i in bpy.context.selected_objects: i.select_set(False) #deselect all objects
		a.select_set(True)
		bpy.context.view_layer.objects.active = a

		if not smd.isDMX:
			ops.object.mode_set(mode='OBJECT')

		return a

	def readFrames(self):
		smd = self.smd
		# We only care about pose data in some SMD types
		if smd.jobType not in [REF, ANIM]:
			for line in smd.file:
				line = line.strip()
				if smdBreak(line): return
				if smd.jobType == FLEX and line.startswith("time"):
					smd.shapeNames = smd.shapeNames or {}
					for c in line:
						if c in ['#',';','/']:
							pos = line.index(c)
							frame = line[:pos].split()[1]
							if c == '/': pos += 1
							smd.shapeNames[frame] = line[pos+1:].strip()

		num_frames = 0
		keyframes = collections.defaultdict(list)
		upAxisMat = getUpAxisMat(smd.upAxis)
		pose_bones = {}
		for bone_id, bone_name in smd.boneIDs.items():
			bone = smd.a.pose.bones.get(bone_name)
			if bone:
				pose_bones[bone_id] = bone

		for line in smd.file:
			if smdBreak(line):
				break
			if smdContinue(line):
				continue

			values = line.split()

			if values[0] == "time": # frame number is a dummy value, all frames are equally spaced
				if num_frames > 0:
					if smd.jobType == REF:
						self.warning(get_id("importer_err_refanim",True).format(smd.jobName))
						for line in smd.file: # skip to end of block
							if smdBreak(line):
								break
							if smdContinue(line):
								continue
				num_frames += 1
				continue

			bone = pose_bones.get(int(values[0]))
			if not bone:
				continue # not in the armature: the bone count mismatch was reported by readNodes

			keyframe = KeyFrame()
			keyframe.frame = num_frames - 1
			keyframe.matrix = Matrix.LocRotScale(Vector((float(values[1]), float(values[2]), float(values[3]))), Euler((float(values[4]), float(values[5]), float(values[6]))), None)
			keyframe.pos = keyframe.rot = True
			if smd.jobType == REF and not bone.parent:
				keyframe.matrix = upAxisMat @ keyframe.matrix
			keyframes[bone].append(keyframe)

		self.applyFrames(keyframes,num_frames)

	def applyFrames(self, keyframes : typing.Dict[bpy.types.PoseBone,list[KeyFrame]], num_frames : int):
		smd = self.smd
		assert(smd.a)
		apply_reference_pose = self.append != 'VALIDATE' and smd.jobType in [REF,ANIM] and not self.appliedReferencePose

		if apply_reference_pose or smd.jobType != ANIM:
			bpy.context.view_layer.objects.active = smd.a
			ops.object.mode_set(mode='POSE')

		if apply_reference_pose:
			self.appliedReferencePose = True

			for bone in smd.a.pose.bones:
				bone.matrix_basis.identity()
			for bone,kf in keyframes.items():
				if bone.name in self.existingBones:
					continue
				elif bone.parent and not keyframes.get(bone.parent):
					bone.matrix = bone.parent.matrix @ kf[0].matrix
				else:
					bone.matrix = kf[0].matrix
			ops.pose.armature_apply()

			bone_vis = None if self.boneMode == 'NONE' else bpy.data.objects.get("smd_bone_vis")

			if self.boneMode == 'SPHERE' and (not bone_vis or bone_vis.type != 'MESH'):
					ops.mesh.primitive_ico_sphere_add(subdivisions=3,radius=2)
					bone_vis = bpy.context.active_object
					bone_vis.data.name = bone_vis.name = "smd_bone_vis"
					bone_vis.use_fake_user = True
					for collection in bone_vis.users_collection:
						collection.objects.unlink(bone_vis) # don't want the user deleting this
					bpy.context.view_layer.objects.active = smd.a
			elif self.boneMode == 'ARROWS' and (not bone_vis or bone_vis.type != 'EMPTY'):
					bone_vis = bpy.data.objects.new("smd_bone_vis",None)
					bone_vis.use_fake_user = True
					bone_vis.empty_display_type = 'ARROWS'
					bone_vis.empty_display_size = 5

			# Calculate armature dimensions...Blender should be doing this!
			maxs = Vector()
			mins = Vector()
			for bone in smd.a.data.bones:
				for i in range(3):
					maxs[i] = max(maxs[i],bone.head_local[i])
					mins[i] = min(mins[i],bone.head_local[i])

			dimensions = []
			if self.qc: self.qc.dimensions = dimensions
			for i in range(3):
				dimensions.append(maxs[i] - mins[i])

			length = max(0.001, (dimensions[0] + dimensions[1] + dimensions[2]) / 600) # very small indeed, but a custom bone is used for display

			# Apply spheres
			ops.object.mode_set(mode='EDIT')
			for bone in [smd.a.data.edit_bones[b.name] for b in keyframes.keys()]:
				bone.tail = bone.head + (bone.tail - bone.head).normalized() * length # Resize loose bone tails based on armature size
				smd.a.pose.bones[bone.name].custom_shape = bone_vis # apply bone shape

			if smd.jobType == ANIM:
				ops.object.mode_set(mode='OBJECT') # the animation is keyed against the new rest pose, which edit mode hasn't written yet

		if smd.jobType == ANIM and self.capture_only:
			smd.captured = ({bone.name: kfs for bone, kfs in keyframes.items()}, num_frames)
		elif smd.jobType == ANIM:
			self._keyAnimation(keyframes, num_frames)
		else:
			# clear any unkeyed poses
			for bone in smd.a.pose.bones:
				bone.location.zero()
				if smd.rotMode == 'XYZ': bone.rotation_euler.zero()
				else: bone.rotation_quaternion.identity()
			scn = bpy.context.scene

			if scn.frame_current == 1: # Blender starts on 1, Source starts on 0
				scn.frame_set(0)
			else:
				scn.frame_set(scn.frame_current)
			ops.object.mode_set(mode='OBJECT')

		print( "- Imported {} frames of animation".format(num_frames) )

	def _keyAnimation(self, keyframes : typing.Dict[bpy.types.PoseBone,list[KeyFrame]], num_frames : int):
		"""Keys an animation into a new action slot (or a new action before Blender 4.4) without changing the pose,
		the object mode or the scene.

		Each keyframe holds a bone's transform relative to its parent. Setting PoseBone.matrix to the parent's pose
		matrix multiplied by that transform, then reading the bone's location and rotation back, is equivalent to
		basis = rest⁻¹ @ parent_rest @ keyframe, because the parent's pose cancels out. Computing that directly
		avoids dozens of RNA calls per bone per frame. Keys are then written to each F-Curve in a single batch."""
		smd = self.smd
		arm = smd.a
		ad = arm.animation_data or arm.animation_data_create()

		if State.useActionSlots:
			channelbag, slot = channelBagForNewActionSlot(arm, smd.jobName, action=self.target_action, assign=self.assign_slot)
			fcurves = channelbag.fcurves
			groups = channelbag.groups
			smd.created_action = self.target_action or ad.action
			smd.created_slot = slot
		else:
			action = bpy.data.actions.new(smd.jobName)
			action.use_fake_user = True
			if self.assign_slot:
				ad.action = action
			fcurves = action.fcurves
			groups = action.groups
			smd.created_action = action
			smd.created_slot = None
		smd.num_frames = num_frames

		if self.assign_slot:
			for bone in arm.pose.bones:
				bone.matrix_basis.identity() # bones which this animation doesn't key must not keep another animation's pose

		legacy = arm.data.vs.legacy_rotation
		upAxisMat = getUpAxisMat(smd.upAxis)
		use_quat = smd.rotMode != 'XYZ'
		rot_path = "rotation_quaternion" if use_quat else "rotation_euler"
		# A channel is constant if it varies less than this. Rotations are tighter as small errors move the ends of limbs.
		loc_tolerance = 0.00001
		rot_tolerance = 0.000001

		last_frame = max((kf.frame for kfs in keyframes.values() for kf in kfs), default=0)
		max_keyed_frame = None
		first_curve = None

		def keyChannel(data_path, index, group, frames, values, tolerance):
			nonlocal first_curve, max_keyed_frame
			if len(values) > 1 and max(values) - min(values) <= tolerance:
				frames = frames[:1] # a constant channel needs only one key
				values = values[:1]
			curve = fcurves.new(data_path=data_path, index=index)
			curve.group = group
			curve.keyframe_points.add(len(frames))
			curve.keyframe_points.foreach_set("co", [co for key in zip(frames, values) for co in key])
			curve.update()
			if first_curve is None:
				first_curve = curve
			max_keyed_frame = frames[-1] if max_keyed_frame is None else max(max_keyed_frame, frames[-1])

		for bone in arm.pose.bones:
			bone_keyframes = keyframes.get(bone)
			if not bone_keyframes:
				continue
			if bone.rotation_mode != smd.rotMode:
				bone.rotation_mode = smd.rotMode

			data_bone = bone.bone
			rest = data_bone.matrix_local
			parent_rest = data_bone.parent.matrix_local if data_bone.parent else None
			parent_space, right = boneParentSpace(data_bone, legacy, upAxisMat)
			default_flags = data_bone.use_inherit_rotation and data_bone.inherit_scale == 'FULL' and data_bone.use_local_location
			left = rest.inverted() @ parent_space

			loc_frames = []
			loc_values = ([], [], [])
			rot_frames = []
			rot_values = ([], [], [], []) if use_quat else ([], [], [])
			prev_rot = None

			for keyframe in bone_keyframes:
				matrix = keyframe.matrix if right is None else keyframe.matrix @ right
				if default_flags:
					basis = left @ matrix
				elif parent_rest is not None:
					basis = data_bone.convert_local_to_pose(parent_space @ matrix, rest, parent_matrix=parent_rest, parent_matrix_local=parent_rest, invert=True)
				else:
					basis = data_bone.convert_local_to_pose(parent_space @ matrix, rest, invert=True)

				if keyframe.pos:
					loc = basis.to_translation()
					loc_frames.append(keyframe.frame)
					for i in range(3):
						loc_values[i].append(loc[i])
				if keyframe.rot:
					if use_quat:
						rot = basis.to_quaternion()
						if prev_rot is not None and prev_rot.dot(rot) < 0:
							rot.negate() # stay on the same hemisphere so that curves don't flip
					else:
						rot = basis.to_euler('XYZ', prev_rot) if prev_rot is not None else basis.to_euler('XYZ')
					prev_rot = rot
					rot_frames.append(keyframe.frame)
					for i in range(len(rot_values)):
						rot_values[i].append(rot[i])

			group = groups.new(name=bone.name)
			bone_path = 'pose.bones["{}"].'.format(bpy.utils.escape_identifier(bone.name))
			if loc_frames:
				for i in range(3):
					keyChannel(bone_path + "location", i, group, loc_frames, loc_values[i], loc_tolerance)
			if rot_frames:
				for i in range(len(rot_values)):
					keyChannel(bone_path + rot_path, i, group, rot_frames, rot_values[i], rot_tolerance)

		# The length of an animation is read from its keys. Don't let a static ending shorten it.
		if first_curve and max_keyed_frame is not None and max_keyed_frame < last_frame:
			value = first_curve.evaluate(last_frame)
			first_curve.keyframe_points.add(1)
			first_curve.keyframe_points[-1].co = (last_frame, value)
			first_curve.update()

	def getMeshMaterial(self,mat_name):
		smd = self.smd
		if mat_name:
			mat_name = self.truncate_id_name(mat_name, bpy.types.Material)
		else:
			mat_name = "Material"

		md = smd.m.data
		mat = None
		for candidate in bpy.data.materials: # Do we have this material already?
			if candidate.name == mat_name:
				mat = candidate
		if mat:
			if md.materials.get(mat.name): # Look for it on this mesh
				for i in range(len(md.materials)):
					if md.materials[i].name == mat.name:
						mat_ind = i
						break
			else: # material exists, but not on this mesh
				md.materials.append(mat)
				mat_ind = len(md.materials) - 1
		else: # material does not exist
			print("- New material: {}".format(mat_name))
			mat = bpy.data.materials.new(mat_name)
			md.materials.append(mat)
			# Give it a random colour
			randCol = []
			for i in range(3):
				randCol.append(random.uniform(.4,1))
			randCol.append(1)
			mat.diffuse_color = randCol
			if smd.jobType == PHYS:
				smd.m.display_type = 'SOLID'
			mat_ind = len(md.materials) - 1

		return mat, mat_ind
	
	# triangles block
	def readPolys(self):
		smd = self.smd
		if smd.jobType not in [ REF, PHYS ]:
			return

		mesh_name = smd.jobName
		if smd.jobType == REF and not smd.jobName.lower().find("reference") and not smd.jobName.lower().endswith("ref"):
			mesh_name += " ref"
		mesh_name = self.truncate_id_name(mesh_name, bpy.types.Mesh)

		# Create a new mesh object, disable double-sided rendering, link it to the current scene
		smd.m = bpy.data.objects.new(mesh_name,bpy.data.meshes.new(mesh_name))
		smd.m.parent = smd.a
		smd.g.objects.link(smd.m)
		if smd.jobType == REF: # can only have flex on a ref mesh
			if self.qc:
				self.qc.ref_mesh = smd.m # for VTA import

		# Create weightmap groups
		for bone in smd.a.data.bones.values():
			smd.m.vertex_groups.new(name=bone.name)

		# Apply armature modifier
		modifier = smd.m.modifiers.new(type="ARMATURE",name=pgettext("Armature"))
		modifier.object = smd.a

		# Initialisation
		md = cast(bpy.types.Mesh, smd.m.data)
		# Vertex values
		norms = []

		bm = bmesh.new()
		bm.from_mesh(md)
		weightLayer = bm.verts.layers.deform.new()
		uvLayer = bm.loops.layers.uv.new()
		
		# *************************************************************************************************
		# There are two loops in this function: one for polygons which continues until the "end" keyword
		# and one for the vertices on each polygon that loops three times. We're entering the poly one now.	
		countPolys = 0
		badWeights = 0
		vertMap = {}

		for line in smd.file:
			line = line.rstrip("\n")

			if line and smdBreak(line): # normally a blank line means a break, but Milkshape can export SMDs with zero-length material names...
				break
			if smdContinue(line):
				continue

			mat, mat_ind = self.getMeshMaterial(line if line else pgettext(get_id("importer_name_nomat", data=True)))

			# ***************************************************************
			# Enter the vertex loop. This will run three times for each poly.
			vertexCount = 0
			faceUVs = []
			vertKeys = []
			for line in smd.file:
				if smdBreak(line):
					break
				if smdContinue(line):
					continue
				values = line.split()

				vertexCount+= 1
				# values[0] is the deprecated bone weight value
				co = tuple(float(v) for v in values[1:4])
				norms.append(tuple(float(v) for v in values[4:7]))

				# Can't do these in the above for loop since there's only two
				faceUVs.append( ( float(values[7]), float(values[8]) ) )

				# Read weightmap data
				vertWeights = []
				if len(values) > 10 and values[9] != "0": # got weight links?
					for i in range(10, 10 + (int(values[9]) * 2), 2): # The range between the first and last weightlinks (each of which is *two* values)
						try:
							bone = smd.a.data.bones[ smd.boneIDs[int(values[i])] ]
							vertWeights.append((smd.m.vertex_groups.find(bone.name), float(values[i+1])))
						except KeyError:
							badWeights += 1
				else: # Fall back on the deprecated value at the start of the line
					try:
						bone = smd.a.data.bones[ smd.boneIDs[int(values[0])] ]				
						vertWeights.append((smd.m.vertex_groups.find(bone.name), 1.0))
					except KeyError:
						badWeights += 1

				vertKeys.append((co, tuple(vertWeights)))

				# Three verts? It's time for a new poly
				if vertexCount == 3:
					def createFace(use_cache = True):
						bmVerts = []
						for vertKey in vertKeys:
							bmv = vertMap.get(vertKey, None) if use_cache else None # if a vertex in this position with these bone weights exists, re-use it.
							if bmv is None:
								bmv = bm.verts.new(vertKey[0])
								for (bone,weight) in vertKey[1]:
									bmv[weightLayer][bone] = weight
								vertMap[vertKey] = bmv
							bmVerts.append(bmv)

						face = bm.faces.new(bmVerts)
						face.material_index = mat_ind
						for i in range(3):
							face.loops[i][uvLayer].uv = faceUVs[i]

					try:
						createFace()
					except ValueError: # face overlaps another, try again with all-new vertices
						createFace(use_cache = False)
					break

			# Back in polyland now, with three verts processed.
			countPolys+= 1

		bm.to_mesh(md)
		del vertMap
		bm.free()
		md.update()
				
		if countPolys:
			ops.object.select_all(action="DESELECT")
			smd.m.select_set(True)
			bpy.context.view_layer.objects.active = smd.m
			
			ops.object.shade_smooth()
			
			for poly in smd.m.data.polygons:
				poly.select = True

			smd.m.show_wire = smd.jobType == PHYS

			md.normals_split_custom_set(norms)

			if smd.upAxis == 'Y':
				md.transform(rx90)
				md.update()

			if badWeights:
				self.warning(get_id("importer_err_badweights", True).format(badWeights,smd.jobName))
			print("- Imported {} polys".format(countPolys))

	# vertexanimation block
	def readShapes(self):
		smd = self.smd
		if smd.jobType is not FLEX:
			return

		if not smd.m:
			if self.qc:
				smd.m = self.qc.ref_mesh
			else: # user selection
				if bpy.context.active_object.type in shape_types:
					smd.m = bpy.context.active_object
				else:
					for obj in bpy.context.selected_objects:
						if obj.type in shape_types:
							smd.m = obj
				
		if not smd.m:
			self.error(get_id("importer_err_shapetarget")) # FIXME: this could actually be supported
			return

		if hasShapes(smd.m):
			smd.m.active_shape_key_index = 0
		smd.m.show_only_shape_key = True # easier to view each shape, less confusion when several are active at once

		def vec_round(v):
			return Vector([round(co,3) for co in v])
		co_map = {}
		mesh_cos = [vert.co for vert in smd.m.data.vertices]
		mesh_cos_rnd = None

		smd.vta_ref = None
		vta_cos = []
		vta_ids = []
		
		making_base_shape = True
		bad_vta_verts = []
		num_shapes = 0
		md = smd.m.data
		
		for line in smd.file:
			line = line.rstrip("\n")
			
			if smdBreak(line):
				break
			if smdContinue(line):
				continue
			
			values = line.split()

			if values[0] == "time":
				shape_name = smd.shapeNames.get(values[1])
				if smd.vta_ref == None:
					if not hasShapes(smd.m, False): smd.m.shape_key_add(name=shape_name if shape_name else "Basis")
					vd = bpy.data.meshes.new(name="VTA vertices")
					vta_ref = smd.vta_ref = bpy.data.objects.new(name=vd.name,object_data=vd)
					vta_ref.matrix_world = smd.m.matrix_world
					smd.g.objects.link(vta_ref)

					vta_err_vg = vta_ref.vertex_groups.new(name=get_id("importer_name_unmatchedvta"))
				elif making_base_shape:
					vd.vertices.add(int(len(vta_cos)/3))
					vd.vertices.foreach_set("co",vta_cos)
					num_vta_verts = len(vd.vertices)
					del vta_cos
					
					mod = vta_ref.modifiers.new(name="VTA Shrinkwrap",type='SHRINKWRAP')
					mod.target = smd.m
					mod.wrap_method = 'NEAREST_VERTEX'
					
					vd = bpy.data.meshes.new_from_object(vta_ref.evaluated_get(bpy.context.evaluated_depsgraph_get()))
					
					vta_ref.modifiers.remove(mod)
					del mod

					for i in range(len(vd.vertices)):
						id = vta_ids[i]
						co =  vd.vertices[i].co
						map_id = None
						try:
							map_id = mesh_cos.index(co)
						except ValueError:
							if not mesh_cos_rnd:
								mesh_cos_rnd = [vec_round(co) for co in mesh_cos]
							try:
								map_id = mesh_cos_rnd.index(vec_round(co))
							except ValueError:
								bad_vta_verts.append(i)
								continue
						co_map[id] = map_id
					
					bpy.data.meshes.remove(vd)
					del vd

					if bad_vta_verts:
						err_ratio = len(bad_vta_verts) / num_vta_verts
						vta_err_vg.add(bad_vta_verts,1.0,'REPLACE')
						message = get_id("importer_err_unmatched_mesh", True).format(len(bad_vta_verts), int(err_ratio * 100))
						if err_ratio == 1:
							self.error(message)
							return
						else:
							self.warning(message)
					else:
						removeObject(vta_ref)
					making_base_shape = False
				
				if not making_base_shape:
					smd.m.shape_key_add(name=shape_name if shape_name else values[1])
					num_shapes += 1

				continue # to the first vertex of the new shape

			cur_id = int(values[0])
			vta_co = getUpAxisMat(smd.upAxis) @ Vector([ float(values[1]), float(values[2]), float(values[3]) ])

			if making_base_shape:
				vta_ids.append(cur_id)
				vta_cos.extend(vta_co)
			else: # write to the shapekey
				try:
					md.shape_keys.key_blocks[-1].data[ co_map[cur_id] ].co = vta_co
				except KeyError:
					pass

		print("- Imported",num_shapes,"flex shapes")

	# Parses a QC file
	# harvest_only: only list the QC's animations and read its rig hints, e.g. to rescan the QC of an existing armature
	def readQC(self, filepath, newscene, doAnim, makeCamera, rotMode, outer_qc = False, harvest_only = False):
		filename = os.path.basename(filepath)
		filedir = os.path.dirname(filepath)
		normalisePath = _normaliseQcPath

		if outer_qc:
			print("\nQC IMPORTER: now working on",filename)

			qc = self.qc = QcInfo()
			qc.startTime = time.time()
			qc.jobName = filename
			qc.root_filedir = qc.outer_filedir = filedir
			qc.makeCamera = makeCamera
			qc.harvest_only = harvest_only
			qc.a = self.target_armature
			qc.visited_qcs.add(_qcFileKey(filepath))
			if harvest_only:
				pass
			elif newscene:
				bpy.context.screen.scene = bpy.data.scenes.new(filename) # BLENDER BUG: this currently doesn't update bpy.context.scene
			else:
				bpy.context.scene.name = filename
		else:
			qc = self.qc

		file = open(filepath, 'r')
		in_bodygroup = in_lod = False
		lod_threshold = None
		source_qc = os.path.splitext(filename)[0]
		sequence : _QcSequenceBlock | None = None # the $sequence or $animation being read
		for line_str in file:
			line, raw_line = self.parseQuoteBlockedLine(line_str, return_raw=True)
			if len(line) == 0:
				continue
			#print(line)

			# handle individual words (insert QC variable values, change slashes)
			for i in range(len(line)):
				word = line[i]
				raw_word = raw_line[i]
				for var, value in qc.vars.items():
					kw = "${}$".format(var)
					pos = word.lower().find(kw)
					if pos != -1:
						word = word.replace(word[pos:pos+len(kw)], value.lower())
					pos = raw_word.lower().find(kw)
					if pos != -1:
						raw_word = raw_word.replace(raw_word[pos:pos+len(kw)], value)
				line[i] = word.replace("/","\\") # studiomdl is Windows-only
				raw_line[i] = raw_word.replace("/","\\")

			# the rest of a $sequence or $animation
			if sequence:
				if sequence.awaiting_brace and line[0] != "{":
					self._finishQcSequence(sequence, doAnim) # it was a one-line definition, and this line is something else
					sequence = None
				else:
					sequence.read(line, raw_line, qc.cd())
					if sequence.finished:
						self._finishQcSequence(sequence, doAnim)
						sequence = None
					continue

			# Skip macros
			if line[0] == "$definemacro":
				self.warning(get_id("importer_qc_macroskip", True).format(filename))
				while line[-1] == "\\\\":
					line = self.parseQuoteBlockedLine( file.readline())
				continue

			# register new QC variable
			if line[0] == "$definevariable":
				qc.vars[line[1].lower()] = raw_line[2]
				continue

			# dir changes
			if line[0] == "$pushd":
				if line[1][-1] != "\\":
					line[1] += "\\"
				qc.dir_stack.append(line[1])
				continue
			if line[0] == "$popd":
				try:
					qc.dir_stack.pop()
				except IndexError:
					pass # invalid QC, but whatever
				continue

			# remember where the model's materials live, for Link VMT Textures
			if line[0] == "$cdmaterials" and len(line) > 1:
				if not qc.harvest_only:
					from .link_vmt import add_cdmaterial
					add_cdmaterial(bpy.context.scene, line[1])
				continue

			# up axis
			if line[0] == "$upaxis":
				if not qc.harvest_only:
					qc.upAxis = bpy.context.scene.vs.up_axis = line[1].upper()
					qc.upAxisMat = getUpAxisMat(line[1])
				continue

			# bones in pure animation QCs
			if line[0] == "$definebone":
				continue # TODO

			# rig hints: IK chains and hit groups describe the character's limbs
			if line[0] == "$ikchain" and len(line) > 2:
				self._addIkChainHint(line, raw_line)
				continue
			if line[0] == "$hbox" and len(line) > 2:
				try:
					group = int(line[1])
				except ValueError:
					continue
				if qc.include_depth == 0 or raw_line[2] not in qc.rig_hints["hitgroups"]:
					qc.rig_hints["hitgroups"][raw_line[2]] = group
				continue

			def import_file(word_index,default_ext,smd_type,append='APPEND',group=None,in_file_recursion = False):
				if qc.harvest_only:
					return False
				path = os.path.join( qc.cd(), appendExt(normalisePath(line[word_index]),default_ext) )

				if not in_file_recursion and not os.path.exists(path):
					return import_file(word_index,"dmx",smd_type,append,group,True)

				if not path in qc.imported_smds: # FIXME: an SMD loaded once relatively and once absolutely will still pass this test
					qc.imported_smds.append(path)
					self.append = append if qc.a else 'NEW_ARMATURE'

					# import the file
					self.parentCollection = self._qcGroupCollection(group) if group else None
					try:
						imported = (self.readDMX if path.endswith("dmx") else self.readSMD)(path,qc.upAxis,rotMode,False,smd_type)
					finally:
						self.parentCollection = None
					self.num_files_imported += imported
					return bool(imported)
				return False

			# meshes
			if line[0] in ["$body","$model"]:
				import_file(2,"smd",REF)
				continue
			if line[0] in ["$lod","$shadowlod"]:
				in_lod = True
				lod_threshold = None if line[0] == "$shadowlod" else (line[1] if len(line) > 1 else "")
				continue
			if in_lod:
				if line[0] == "replacemodel" and len(line) > 2:
					if line[2] != "blank" and import_file(2,"smd",REF,'VALIDATE',group="lod"):
						self._tagLodCollection(lod_threshold)
					continue
				if "}" in line:
					in_lod = False
					continue
			if line[0] == "$bodygroup":
				in_bodygroup = True
				continue
			if in_bodygroup:
				if line[0] == "studio":
					import_file(1,"smd",REF)
					continue
				if "}" in line:
					in_bodygroup = False
					continue

			# pose parameters, which drive blend sequences
			if line[0] == "$poseparameter" and len(line) > 3:
				try:
					qc.pose_params.setdefault(line[1].lower(), (raw_line[1], float(line[2]), float(line[3])))
				except ValueError:
					pass
				continue

			# a sequence which an $includemodel model defines, in its place in the sequence list
			if line[0] == "$declaresequence" and len(line) > 1:
				if doAnim:
					qc.pending_sequences.append(_QcSequenceBlock(line[0], line[1], raw_line[1], source_qc))
				continue

			# skeletal animations: listed now, imported on demand (see anim_list.py)
			if line[0] in ["$sequence","$animation"] and len(line) > 1:
				sequence = _QcSequenceBlock(line[0], line[1], raw_line[1], source_qc)
				start = 2
				if line[0] == "$animation" and len(line) > 2 and line[2] not in ["{","}"]:
					sequence.refs.append((line[2], raw_line[2], qc.cd()))
					start = 3
				sequence.read(line, raw_line, qc.cd(), start)
				if sequence.depth <= 0:
					if sequence.opened:
						self._finishQcSequence(sequence, doAnim) # "{ ... }" on one line
						sequence = None
					else:
						sequence.awaiting_brace = True # the block may start on the next line
				continue

			# animations of other models, which this model can play. Like the engine, read them after this model's own.
			if line[0] == "$includemodel" and len(line) > 1:
				if doAnim:
					qc.pending_includemodels.append((raw_line[1], filedir))
				continue

			# flex animation
			if line[0] == "flexfile":
				import_file(1,"vta",FLEX,'VALIDATE')
				continue

			# naming shapes
			if qc.ref_mesh and line[0] in ["flex","flexpair"]: # "flex" is safe because it cannot come before "flexfile"
				for i in range(1,len(line)):
					if line[i] == "frame":
						shape = qc.ref_mesh.data.shape_keys.key_blocks.get(line[i+1])
						if shape and shape.name.startswith("Key"): shape.name = line[1]
						break
				continue

			# physics mesh
			if line[0] in ["$collisionmodel","$collisionjoints"]:
				import_file(1,"smd",PHYS,'VALIDATE',group="physics")
				continue

			# origin; this is where viewmodel editors should put their camera, and is in general something to be aware of
			if line[0] == "$origin" and not qc.harvest_only:
				if qc.makeCamera:
					data = bpy.data.cameras.new(qc.jobName + "_origin")
					name = "camera"
				else:
					data = None
					name = "empty object"
				print("QC IMPORTER: created {} at $origin\n".format(name))

				origin = bpy.data.objects.new(qc.jobName + "_origin",data)
				bpy.context.scene.collection.objects.link(origin)

				origin.rotation_euler = Vector([pi/2,0,pi]) + Vector(getUpAxisMat(qc.upAxis).inverted().to_euler()) # works, but adding seems very wrong!
				ops.object.select_all(action="DESELECT")
				origin.select_set(True)
				ops.object.transform_apply(rotation=True)

				for i in range(3):
					origin.location[i] = float(line[i+1])
				origin.matrix_world = getUpAxisMat(qc.upAxis) @ origin.matrix_world

				if qc.makeCamera:
					bpy.context.scene.camera = origin
					origin.data.lens_unit = 'DEGREES'
					origin.data.lens = 31.401752 # value always in mm; this number == 54 degrees
					# Blender's FOV isn't locked to X or Y height, so a shift is needed to get the weapon aligned properly.
					# This is a nasty hack, and the values are only valid for the default 54 degrees angle
					origin.data.shift_y = -0.27
					origin.data.shift_x = 0.36
					origin.data.passepartout_alpha = 1
				else:
					origin.empty_display_type = 'PLAIN_AXES'

				qc.origin = origin

			# QC inclusion
			if line[0] == "$include":
				path = os.path.join(qc.root_filedir,normalisePath(line[1])) # special case: ignores dir stack

				if not path.endswith(".qc") and not path.endswith(".qci"):
					if os.path.exists(appendExt(path,"qci")):
						path = appendExt(path,"qci")
					elif os.path.exists(appendExt(path,"qc")):
						path = appendExt(path,"qc")
				try:
					self.readQC(path,False, doAnim, makeCamera, rotMode)
				except IOError:
					self.warning(get_id("importer_err_qci", True).format(path))

		file.close()
		if sequence:
			self._finishQcSequence(sequence, doAnim) # a one-line definition on the last line

		if qc.origin:
			qc.origin.parent = qc.a
			if qc.ref_mesh:
				size = min(qc.ref_mesh.dimensions) / 15
				if qc.makeCamera:
					qc.origin.data.display_size = size
				else:
					qc.origin.empty_display_size = size

		if outer_qc:
			self._resolveQcSequences()
			self._readQueuedIncludes(doAnim, makeCamera, rotMode)
			self._finishQcAnimations(filepath, doAnim, rotMode)
			printTimeMessage(qc.startTime,filename,"import","QC")
		return self.num_files_imported

	def _addIkChainHint(self, line, raw_line):
		qc = self.qc
		knee = None
		if "knee" in line:
			i = line.index("knee")
			try:
				knee = [float(v) for v in line[i+1:i+4]]
			except ValueError:
				pass
			if knee is not None and len(knee) != 3:
				knee = None
		hint = { "name": raw_line[1], "bone": raw_line[2], "knee": knee }
		chains = qc.rig_hints["ikchains"]
		existing = next((i for i, chain in enumerate(chains) if chain["name"].lower() == hint["name"].lower()), None)
		if existing is None:
			chains.append(hint)
		elif qc.include_depth == 0: # the model's own chains win over those of included models
			chains[existing] = hint

	def _qcGroupCollection(self, kind : str):
		"""The collection holding the model's LODs or physics meshes. It is excluded from the view layer at the end of the import."""
		qc = self.qc
		attr = "lod_collection" if kind == "lod" else "physics_collection"
		collection = getattr(qc, attr)
		if not collection:
			name = get_id("importer_qc_lods" if kind == "lod" else "importer_qc_physics", data=True).format(os.path.splitext(qc.jobName)[0])
			collection = bpy.data.collections.new(self.truncate_id_name(name, bpy.types.Collection))
			bpy.context.scene.collection.children.link(collection)
			setattr(qc, attr, collection)
		return collection

	def _tagLodCollection(self, threshold):
		collection = self.smd.g
		if not collection or collection == bpy.context.scene.collection:
			return
		if threshold is None:
			collection["lod_shadow"] = True
		else:
			try:
				collection["lod_threshold"] = float(threshold)
			except ValueError:
				pass

	def _finishQcSequence(self, sequence : '_QcSequenceBlock', doAnim):
		if not doAnim:
			return
		qc = self.qc
		if sequence.keyword == "$animation":
			anim = None
			if sequence.refs:
				_, raw_ref, cd = sequence.refs[0]
				anim = self._qcAnimDef(sequence.raw_name, raw_ref, cd, sequence)
			qc.anim_scope[sequence.name.lower()] = anim # even if the file is missing, so that sequences don't take the name for a path
		else:
			qc.pending_sequences.append(sequence)

	def _qcAnimDef(self, name : str, ref : str, cd : str, block : '_QcSequenceBlock') -> QcAnimDef | None:
		path = self._resolveAnimFile(ref, cd)
		if not path:
			return None
		subtract = block.subtract
		return QcAnimDef(name, path, cd, fps=block.fps, is_loop=block.has("loop"), is_delta=block.is_delta or subtract is not None, subtract=subtract)

	def _resolveAnimFile(self, ref : str, cd : str) -> str | None:
		qc = self.qc
		relative = _normaliseQcPath(ref)
		for ext in ("smd", "dmx"):
			path = os.path.join(cd, appendExt(relative, ext))
			if os.path.exists(path):
				return os.path.normpath(path)
		missing = os.path.join(cd, relative)
		if missing not in qc.missing_anim_files:
			qc.missing_anim_files.append(missing)
		return None

	def _resolveQcSequences(self):
		"""Adds the $sequences of the model being read to the sequence list, unless a model read before defined the same name."""
		qc = self.qc
		for block in qc.pending_sequences:
			key = block.name.lower()
			if block.keyword == "$declaresequence":
				if key not in qc.sequences:
					qc.sequences[key] = QcSequenceRecord(block.raw_name, block.source_qc, placeholder=True)
				continue
			existing = qc.sequences.get(key)
			if existing and not existing.placeholder:
				continue # the engine keeps the first sequence of a name
			record = self._qcSequenceRecord(block)
			if record:
				qc.sequences[key] = record # a placeholder keeps its place in the list
			else:
				qc.num_dropped += 1
		qc.pending_sequences = []

	def _qcSequenceRecord(self, block : '_QcSequenceBlock') -> QcSequenceRecord | None:
		"""Resolves the animations which a $sequence plays: $animations of the model, or files which take the sequence's options."""
		qc = self.qc
		record = QcSequenceRecord(block.raw_name, block.source_qc)
		post_subtract = False
		for word, raw_ref, cd in block.refs:
			key = word.lower()
			if key in qc.anim_scope:
				anim = qc.anim_scope[key]
			else:
				anim = self._qcAnimDef(os.path.splitext(os.path.basename(_normaliseQcPath(raw_ref)))[0], raw_ref, cd, block)
			if not anim:
				print("- Sequence \"{}\" not listed: \"{}\" was not found".format(block.raw_name, raw_ref))
				return None

			component = QcComponent(anim.name, anim.filepath, anim.fps or block.fps or 30.0)
			if anim.subtract:
				ref, component.subtract_frame, component.subtract_post = anim.subtract
				post_subtract |= component.subtract_post
				if ref.lower() in qc.anim_scope:
					base = qc.anim_scope[ref.lower()]
					component.subtract_filepath = base.filepath if base else ""
				else:
					component.subtract_filepath = self._resolveAnimFile(ref, anim.cd) or ""
			record.components.append(component)
			record.is_loop |= anim.is_loop
			record.is_delta |= anim.is_delta
		if not record.components:
			return None

		record.fps = record.components[0].fps
		record.is_loop |= block.has("loop")
		record.is_delta |= block.is_delta
		record.is_post = block.has("post") or block.has("delta") or (post_subtract and not block.has("predelta"))
		record.is_hidden = block.has("hidden")
		activity = block.args("activity")
		record.activity = activity[0] if activity else ""
		record.layers = [args[0] for option, _, args in block.options if option in ("addlayer", "blendlayer") and args]

		count = len(record.components)
		if count > 1:
			from .qc_compose import blend_grid
			params = block.blend_params
			width, height = blend_grid(count, block.blend_width, len(params))
			if width * height != count:
				print("- Sequence \"{}\": {} animations don't fill a blend grid; blending them in a row".format(block.raw_name, count))
				width, height = count, 1
			record.blend_width = width
			record.blend_params = params[:1] if height == 1 else params[:2]
		return record

	def _resolveIncludedModel(self, mdl_path : str, qc_dir : str) -> str | None:
		"""$includemodel names a compiled model. Look for a decompiled QC of it instead."""
		relative = _normaliseQcPath(mdl_path)
		stem = os.path.splitext(os.path.basename(relative))[0]
		subdir = os.path.dirname(relative)
		search_dirs = []
		for folder in (qc_dir, self.qc.root_filedir, self.qc.outer_filedir, bpy.path.abspath(self.includeSearchPath) if self.includeSearchPath else None):
			if folder and folder not in search_dirs:
				search_dirs.append(folder)
		for folder in search_dirs:
			for candidate in (os.path.join(folder, subdir, stem + ".qc"), os.path.join(folder, stem + ".qc"), os.path.join(folder, stem, stem + ".qc")):
				parent = os.path.dirname(candidate)
				if os.path.isdir(parent): # match the name case-insensitively, and use its real case
					wanted = os.path.basename(candidate).lower()
					match = next((name for name in os.listdir(parent) if name.lower() == wanted), None)
					if match:
						return os.path.join(parent, match)
		return None

	def _readIncludedModel(self, mdl_path : str, qc_dir : str, doAnim, makeCamera, rotMode):
		qc = self.qc
		path = self._resolveIncludedModel(mdl_path, qc_dir)
		if not path:
			if mdl_path not in qc.unresolved_includes:
				qc.unresolved_includes.append(mdl_path)
			return
		key = _qcFileKey(path)
		if key in qc.visited_qcs:
			return
		if qc.include_depth >= 8:
			self.warning(get_id("importer_qc_includemodel_depth", True).format(mdl_path))
			return
		qc.visited_qcs.add(key)
		print("- $includemodel \"{}\": reading {}".format(mdl_path, path))

		# an included model is a separate compile, with its own folder, variables and animation names
		saved = (qc.root_filedir, qc.dir_stack, qc.vars, qc.in_block_comment, qc.harvest_only, qc.anim_scope, qc.pending_sequences, qc.pending_includemodels)
		qc.root_filedir = os.path.dirname(path)
		qc.dir_stack = []
		qc.vars = {}
		qc.in_block_comment = False
		qc.harvest_only = True
		qc.anim_scope = {}
		qc.pending_sequences = []
		qc.pending_includemodels = []
		qc.include_depth += 1
		try:
			self.readQC(path, False, doAnim, makeCamera, rotMode)
			self._resolveQcSequences()
			self._readQueuedIncludes(doAnim, makeCamera, rotMode) # depth first, like the engine
		except IOError:
			self.warning(get_id("importer_err_qci", True).format(path))
		finally:
			qc.include_depth -= 1
			(qc.root_filedir, qc.dir_stack, qc.vars, qc.in_block_comment, qc.harvest_only, qc.anim_scope, qc.pending_sequences, qc.pending_includemodels) = saved

	def _readQueuedIncludes(self, doAnim, makeCamera, rotMode):
		"""Reads the $includemodel models of the model which has just been read, in order."""
		qc = self.qc
		queue, qc.pending_includemodels = qc.pending_includemodels, []
		for mdl_path, qc_dir in queue:
			self._readIncludedModel(mdl_path, qc_dir, doAnim, makeCamera, rotMode)

	def _finishQcAnimations(self, filepath, doAnim, rotMode):
		"""Lists the QC's sequences on its armature, and builds the rig."""
		qc = self.qc
		for include in qc.unresolved_includes:
			self.warning(get_id("importer_qc_includemodel_missing", True).format(include))
		if qc.missing_anim_files:
			self.warning(get_id("importer_qc_anims_missing", True).format(len(qc.missing_anim_files), qc.num_dropped))
			for path in qc.missing_anim_files:
				print("  - missing:", path)

		records = [record for record in qc.sequences.values() if not record.placeholder] if doAnim else []
		undefined = [record.name for record in qc.sequences.values() if record.placeholder]
		if doAnim and undefined:
			print("- {} $declaresequence names are not defined by any included model: {}".format(len(undefined), ", ".join(undefined)))
		if records and not qc.a:
			qc.a = self.findArmature()

		loaded = {}
		if records and not qc.a and not qc.harvest_only:
			# an animation-only QC: the first sequence which plays a plain animation provides the skeleton
			first = next((r for r in records if r.is_simple()), None) or next((r for r in records if not r.is_delta), None)
			path = first.components[0].filepath if first else None
			self.append = 'NEW_ARMATURE'
			if path and (self.readDMX if path.lower().endswith(".dmx") else self.readSMD)(path, qc.upAxis, rotMode, False, ANIM):
				self.num_files_imported += 1
				qc.a = qc.a or self.smd.a
				slot = self.smd.created_slot
				if first.is_simple():
					loaded[first.name.lower()] = (self.smd.created_action, slot.handle if slot else -1, self.smd.num_frames)
				elif slot:
					self.smd.created_action.slots.remove(slot) # a blend, which is composed when it is loaded
				elif self.smd.created_action:
					bpy.data.actions.remove(self.smd.created_action)

		arm = qc.a
		if not arm or arm.type != 'ARMATURE':
			return

		import json
		from . import anim_list
		arm.data.vs.rig_hints = json.dumps(qc.rig_hints)
		if records or qc.unresolved_includes:
			anim_list.store_records(arm, records, loaded, qc_path=os.path.realpath(filepath), rot_mode=rotMode, up_axis=qc.upAxis,
						   search_path=self.includeSearchPath, unresolved_includes=qc.unresolved_includes, pose_params=qc.pose_params)
			print("- Listed {} sequences on \"{}\"".format(len(records), arm.name))
			self.num_anims_listed = getattr(self, "num_anims_listed", 0) + len(records)
			if records and not self.lazyAnims and not qc.harvest_only:
				vs = arm.vs
				anim_list.load_items(bpy.context, arm, [i for i in anim_list.filtered_indices(vs, loaded_only=False)
					if anim_list.can_load(vs.qc_anims[i]) and not anim_list.is_loaded(vs.qc_anims[i])], logger=self)

		if self.generateRig and not qc.harvest_only:
			from . import rig
			rig.generate_rig(arm, qc.rig_hints, logger=self, only_if_found=True)

	def initSMD(self, filepath,smd_type,upAxis,rotMode):
		smd = self.smd = SmdInfo(os.path.splitext(os.path.basename(filepath))[0])
		smd.jobType = smd_type
		smd.startTime = time.time()
		smd.rotMode = rotMode
		if self.qc:
			smd.upAxis = self.qc.upAxis
			smd.a = self.qc.a
		if self.target_armature:
			smd.a = self.target_armature
		if upAxis:
			smd.upAxis = upAxis

		return smd

	def createCollection(self):
		if self.smd.jobType and self.smd.jobType != ANIM:
			if self.createCollections or self.parentCollection:
				self.smd.g = bpy.data.collections.new(self.smd.jobName)
				(self.parentCollection or bpy.context.scene.collection).children.link(self.smd.g)
			else:
				self.smd.g = bpy.context.scene.collection

	# Parses an SMD file
	def readSMD(self, filepath, upAxis, rotMode, newscene = False, smd_type = None):
		smd = self.initSMD(filepath,smd_type,upAxis,rotMode)
		self.appliedReferencePose = False

		try:
			smd.file = file = open(filepath, 'r')
		except IOError as err: # TODO: work out why errors are swallowed if I don't do this!
			self.error(get_id("importer_err_smd", True).format(smd.jobName,err))
			return 0

		if newscene:
			bpy.context.screen.scene = bpy.data.scenes.new(smd.jobName) # BLENDER BUG: this currently doesn't update bpy.context.scene
		elif self.modifyScene and bpy.context.scene.name == pgettext("Scene"):
			bpy.context.scene.name = smd.jobName

		print("\nSMD IMPORTER: now working on",smd.jobName)

		while True:
			header = self.parseQuoteBlockedLine(file.readline())
			if header: break

		if header != ["version" ,"1"]:
			self.warning (get_id("importer_err_smd_ver"))

		if smd.jobType == None:
			self.scanSMD() # What are we dealing with?
		self.createCollection()

		for line in file:
			if line == "nodes\n": self.readNodes()
			if line == "skeleton\n": self.readFrames()
			if line == "triangles\n": self.readPolys()
			if line == "vertexanimation\n": self.readShapes()

		file.close()
		printTimeMessage(smd.startTime,smd.jobName,"import")

		return 1

	def readDMX(self, filepath, upAxis, rotMode,newscene = False, smd_type = None):
		smd = self.initSMD(filepath,smd_type,upAxis,rotMode)
		smd.isDMX = 1

		bench = BenchMarker(1,"DMX")

		target_arm = (self.target_armature or self.findArmature()) if self.append != 'NEW_ARMATURE' else None
		if target_arm:
			smd.a = target_arm

		ob = bone = smd.atch = None
		if bpy.context.active_object and bpy.context.active_object.mode != 'OBJECT': ops.object.mode_set(mode='OBJECT')
		self.appliedReferencePose = False
		
		print( "\nDMX IMPORTER: now working on",os.path.basename(filepath) )	
		
		try:
			print("- Loading DMX...")
			try:
				dm = datamodel.load(filepath)
			except IOError as e:
				self.error(e)
				return 0
			bench.report("Load DMX")

			if self.modifyScene and bpy.context.scene.name.startswith("Scene"):
				bpy.context.scene.name = smd.jobName

			keywords = getDmxKeywords(dm.format_ver)

			correctiveSeparator = '_'
			if dm.format_ver >= 22 and any([elem for elem in dm.elements if elem.type == "DmeVertexDeltaData" and '__' in elem.name]):
				correctiveSeparator = '__'
				self._ensureSceneDmxVersion(dmx_version(9, 22, compiler=Compiler.MODELDOC))
			
			if not smd_type:
				smd.jobType = REF if dm.root.get("model") else ANIM
			self.createCollection()
			self.ensureAnimationBonesValidated()
			
			DmeModel = dm.root["skeleton"]
			transforms = DmeModel["baseStates"][0]["transforms"] if DmeModel.get("baseStates") and len(DmeModel["baseStates"]) > 0 else None

			DmeAxisSystem = DmeModel.get("axisSystem")
			if DmeAxisSystem:
				for axis in axes_lookup.items():
					if axis[1] == DmeAxisSystem["upAxis"] - 1:
						upAxis = smd.upAxis = axis[0]
						break
			
			def getBlenderQuat(datamodel_quat):
				return Quaternion([datamodel_quat[3], datamodel_quat[0], datamodel_quat[1], datamodel_quat[2]])
			def get_transform_matrix(elem):
				out = Matrix()
				if not elem: return out
				trfm = elem.get("transform")
				if transforms:
					for e in transforms:
						if e.name == elem.name:
							trfm = e
				if not trfm: return out
				out @= Matrix.Translation(Vector(trfm["position"]))
				out @= getBlenderQuat(trfm["orientation"]).to_matrix().to_4x4()
				return out
			def isBone(elem):
				return elem.type in ["DmeDag","DmeJoint"]
			def getBoneForElement(elem) -> bpy.types.EditBone:
				return smd.a.data.edit_bones[smd.boneIDs[elem.id]]
			def enumerateBonesAndAttachments(elem : datamodel.Element):
				parent = elem if isBone(elem) else None
				for child in cast(list[datamodel.Element], elem.get("children") or []):
					if child.type == "DmeDag" and child.get("shape") and child["shape"].type == "DmeAttachment":						
						yield (cast(datamodel.Element,child["shape"]), parent)
					elif isBone(child) and child.name != implicit_bone_name:
						# don't import Dags which simply wrap meshes. In some DMX animations, each bone has an empty mesh attached.
						boneShape = child.get("shape")
						if not boneShape or boneShape["currentState"] == None:
							yield (child, parent)
						yield from enumerateBonesAndAttachments(child)
					elif child.type == "DmeModel":
						yield from enumerateBonesAndAttachments(child)
			
			# Skeleton
			bone_matrices = {}
			if target_arm:
				missing_bones = []
				bpy.context.view_layer.objects.active = smd.a
				smd.a.hide_set(False)
				ops.object.mode_set(mode='EDIT')

				for (elem,parent) in enumerateBonesAndAttachments(DmeModel):
					if elem.type == "DmeAttachment" or elem.name is None:
						continue

					bone = smd.a.data.edit_bones.get(self.truncate_id_name(elem.name, bpy.types.Bone))
					if not bone:
						if self.append == 'APPEND' and smd.jobType in [REF,ANIM]:
							bone = smd.a.data.edit_bones.new(self.truncate_id_name(elem.name, bpy.types.Bone))
							bone.parent = getBoneForElement(parent) if parent else None
							bone.tail = (0,5,0)
							bone_matrices[bone.name] = get_transform_matrix(elem)
							smd.boneIDs[elem.id] = bone.name
							smd.boneTransformIDs[elem["transform"].id] = bone.name
						else:
							missing_bones.append(elem.name)
					else:
						scene_parent = bone.parent.name if bone.parent else "<None>"
						dmx_parent = parent.name if parent else "<None>"
						if scene_parent != dmx_parent:
							self.warning(get_id('importer_bone_parent_miss',True).format(elem.name,scene_parent,dmx_parent,smd.jobName))
							
						smd.boneIDs[elem.id] = bone.name
						smd.boneTransformIDs[elem["transform"].id] = bone.name

				if missing_bones and smd.jobType != ANIM: # animations report missing bones seperately
					self.warning(get_id("importer_err_missingbones", True).format(smd.jobName,len(missing_bones),smd.a.name))
					print("\n".join(missing_bones))
			elif any(enumerateBonesAndAttachments(DmeModel)):
				self.append = 'NEW_ARMATURE'
				ob = smd.a = self.createArmature(self.truncate_id_name(DmeModel.name or smd.jobName, bpy.types.Armature))
				if self.qc: self.qc.a = ob
				bpy.context.view_layer.objects.active = smd.a
				ops.object.mode_set(mode='EDIT')
				
				smd.a.matrix_world = getUpAxisMat(smd.upAxis)
				
				for (elem,parent) in enumerateBonesAndAttachments(DmeModel):
					if elem.name is None:
						continue

					parent = getBoneForElement(parent) if parent else None
					if elem.type == "DmeAttachment":
						atch = smd.atch = bpy.data.objects.new(name=self.truncate_id_name(elem.name, "Attachment"), object_data=None)
						if smd.g:
							smd.g.objects.link(atch)
						else:
							bpy.context.scene.collection.objects.link(atch)
						atch.show_in_front = True
						atch.empty_display_type = 'ARROWS'

						atch.parent = smd.a
						if parent:
							atch.parent_type = 'BONE'
							atch.parent_bone = parent.name
						
						atch.matrix_local = get_transform_matrix(elem)
					else:
						bone = smd.a.data.edit_bones.new(self.truncate_id_name(elem.name,bpy.types.Bone))
						bone.parent = parent
						bone.tail = (0,5,0)
						bone_matrices[bone.name] = get_transform_matrix(elem)
						smd.boneIDs[elem.id] = bone.name
						smd.boneTransformIDs[elem["transform"].id] = bone.name
			
			if smd.a:
				ops.object.mode_set(mode='POSE')
				if smd.jobType != ANIM:
					restData = {}
					for bone in smd.a.pose.bones:
						mat = bone_matrices.get(bone.name)
						if mat:
							keyframe = KeyFrame()
							keyframe.matrix = mat
							restData[bone] = [keyframe]
					if restData:
						self.applyFrames(restData,1)
			
			def parseModel(elem,matrix=Matrix(), last_bone = None):
				if elem.type in ["DmeModel","DmeDag", "DmeJoint"]:
					if elem.type == "DmeDag":
						matrix = matrix @ get_transform_matrix(elem)
					if elem.get("children") and elem["children"]:
						if elem.type == "DmeJoint":
							last_bone = elem
						subelems = elem["children"]
					elif elem.get("shape"):
						subelems = [elem["shape"]]
					else:
						return
					for subelem in subelems:
						parseModel(subelem,matrix,last_bone)
				elif elem.type == "DmeMesh":
					DmeMesh = elem
					if bpy.context.active_object:
						ops.object.mode_set(mode='OBJECT')
					mesh_name = self.truncate_id_name(DmeMesh.name,bpy.types.Mesh)
					ob = smd.m = bpy.data.objects.new(name=mesh_name, object_data=bpy.data.meshes.new(name=mesh_name))
					smd.g.objects.link(ob)
					ob.show_wire = smd.jobType == PHYS

					DmeVertexData = DmeMesh["currentState"]
					have_weightmap = keywords["weight"] in DmeVertexData["vertexFormat"]
					
					if smd.a:
						ob.parent = smd.a
						if have_weightmap:
							amod = ob.modifiers.new(name="Armature",type='ARMATURE')
							amod.object = smd.a
							amod.use_bone_envelopes = False
					else:
						ob.matrix_local = getUpAxisMat(smd.upAxis)
					
					print("Importing DMX mesh \"{}\"".format(DmeMesh.name))					
					
					bm = bmesh.new()
					bm.from_mesh(ob.data)
					
					positions = DmeVertexData[keywords['pos']]
					positionsIndices = DmeVertexData[keywords['pos'] + "Indices"]
					
					# Vertices
					for pos in positions:
						bm.verts.new( Vector(pos) )
					bm.verts.ensure_lookup_table()
					
					# Faces, Materials, Colours
					skipfaces = set()
					vertex_layer_infos = []

					class VertexLayerInfo():
						def __init__(self, layer, indices, values):
							self.layer = layer
							self.indices = indices
							self.values = values

						def get_loop_value(self, loop_index):
							return self.values[self.indices[loop_index]]

					# Normals
					normalsLayer = bm.loops.layers.float_vector.new("__bst_normal")
					normalsLayerName = normalsLayer.name
					vertex_layer_infos.append(VertexLayerInfo(normalsLayer, DmeVertexData[keywords['norm'] + "Indices"], DmeVertexData[keywords['norm']]))

					# Arbitrary vertex data
					def warnUneditableVertexData(name): self.warning("Vertex data '{}' was imported, but cannot be edited in Blender (as of 2.82)".format(name))
					def isClothEnableMap(name): return name.startswith("cloth_enable$")
					
					for vertexMap in [prop for prop in DmeVertexData["vertexFormat"] if prop not in keywords.values()]:
						indices = DmeVertexData.get(vertexMap + "Indices")
						if not indices:
							continue
						values = DmeVertexData.get(vertexMap)
						if not isinstance(values, list) or len(values) == 0:
							continue

						if isinstance(values[0], float):
							if isClothEnableMap(vertexMap):
								continue # will be imported later as a weightmap
							layers = bm.loops.layers.float
							warnUneditableVertexData(vertexMap)
						elif isinstance(values[0], int):
							layers = bm.loops.layers.int
							warnUneditableVertexData(vertexMap)
						elif isinstance(values[0], str):
							layers = bm.loops.layers.string
							warnUneditableVertexData(vertexMap)
						elif isinstance(values[0], datamodel.Vector2):
							layers = bm.loops.layers.uv
						elif isinstance(values[0], datamodel.Vector4) or isinstance(values[0], datamodel.Color):
							layers = bm.loops.layers.color
						else:
							self.warning("Could not import vertex data '{}'; Blender does not support {} data layers.".format(vertexMap, type(values[0]).__name__))
							continue

						vertex_layer_infos.append(VertexLayerInfo(layers.new(vertexMap), DmeVertexData[vertexMap + "Indices"], values))

						if vertexMap != "textureCoordinates":
							self._ensureSceneDmxVersion(dmx_version(9, 22))

					deform_group_names = ordered_set.OrderedSet()

					# Weightmap
					if have_weightmap:
						weighted_bone_indices = ordered_set.OrderedSet()
						jointWeights = DmeVertexData[keywords["weight"]]
						jointIndices = DmeVertexData[keywords["weight_indices"]]
						jointRange = range(DmeVertexData["jointCount"])
						deformLayer = bm.verts.layers.deform.new()

						joint_index = 0
						for vert in bm.verts:
							for i in jointRange:
								weight = jointWeights[joint_index]
								if weight > 0:
									vg_index = weighted_bone_indices.add(jointIndices[joint_index])
									vert[deformLayer][vg_index] = weight
								joint_index += 1
					
						joints = DmeModel["jointList"] if dm.format_ver >= 11 else DmeModel["jointTransforms"];
						for boneName in (joints[i].name for i in weighted_bone_indices):
							deform_group_names.add(boneName)
					
					for face_set in DmeMesh["faceSets"]:
						mat_path = face_set["material"]["mtlName"]
						bpy.context.scene.vs.material_path = os.path.dirname(mat_path).replace("\\","/")
						mat, mat_ind = self.getMeshMaterial(os.path.basename(mat_path))
						face_loops = []
						dmx_face = 0
						for vert in face_set["faces"]:
							if vert != -1:
								face_loops.append(vert)
								continue

							# -1 marks the end of a face definition, time to create it!
							try:
								face = bm.faces.new([bm.verts[positionsIndices[loop]] for loop in face_loops])
								face.smooth = True
								face.material_index = mat_ind

								# Apply normals and Source 2 vertex data
								for layer_info in vertex_layer_infos:
									is_uv_layer = layer_info.layer.name in bm.loops.layers.uv
									for i, loop in enumerate(face.loops):
										value = layer_info.get_loop_value(face_loops[i])
										if is_uv_layer:
											loop[layer_info.layer].uv = value
										else:	
											loop[layer_info.layer] = value

							except ValueError: # Can't have an overlapping face...this will be painful later
								skipfaces.add(dmx_face)
							dmx_face += 1
							face_loops.clear()
					

					for cloth_enable in (name for name in DmeVertexData["vertexFormat"] if isClothEnableMap(name)):
						deformLayer = bm.verts.layers.deform.verify()
						vg_index = deform_group_names.add(cloth_enable)
						data = DmeVertexData[cloth_enable]
						indices = DmeVertexData[cloth_enable + "Indices"]
						i = 0
						for face in bm.faces:
							for loop in face.loops:
								weight = data[indices[i]]
								loop.vert[deformLayer][vg_index] = weight
								i += 1
					
					for groupName in deform_group_names:
						ob.vertex_groups.new(name=groupName) # must create vertex groups before loading bmesh data

					if last_bone and not have_weightmap: # bone parent
						ob.parent_type = 'BONE'
						ob.parent_bone = last_bone.name
					
					# Move from BMesh to Blender
					bm.to_mesh(ob.data)
					del bm
					ob.data.update()
					ob.matrix_world @= matrix
					if ob.parent_bone:
						ob.matrix_world = ob.parent.matrix_world @ ob.parent.data.bones[ob.parent_bone].matrix_local @ ob.matrix_world
					elif ob.parent:
						ob.matrix_world = ob.parent.matrix_world @ ob.matrix_world
					if smd.jobType == PHYS:
						ob.display_type = 'SOLID'

					# Normals
					normalsLayer = ob.data.attributes[normalsLayerName]
					ob.data.normals_split_custom_set([value.vector for value in normalsLayer.data])
					del normalsLayer
					ob.data.attributes.remove(ob.data.attributes[normalsLayerName])

					# Stereo balance
					if keywords['balance'] in DmeVertexData["vertexFormat"]:
						vg = ob.vertex_groups.new(name=get_id("importer_balance_group", data=True))
						balanceIndices = DmeVertexData[keywords['balance'] + "Indices"]
						balance = DmeVertexData[keywords['balance']]
						ones = []
						for i in balanceIndices:
							val = balance[i]
							if val == 0:
								continue
							elif val == 1:
								ones.append(i)
							else:
								vg.add([i],val,'REPLACE')
						vg.add(ones,1,'REPLACE')

						ob.data.vs.flex_stereo_mode = 'VGROUP'
						ob.data.vs.flex_stereo_vg = vg.name

					# Shapes
					if DmeMesh.get("deltaStates"):
						for DmeVertexDeltaData in DmeMesh["deltaStates"]:
							if not ob.data.shape_keys:
								ob.shape_key_add(name="Basis")
								ob.show_only_shape_key = True
								ob.data.shape_keys.name = DmeMesh.name
							shape_key = ob.shape_key_add(name=DmeVertexDeltaData.name)
							
							if keywords['pos'] in DmeVertexDeltaData["vertexFormat"]:
								deltaPositions = DmeVertexDeltaData[keywords['pos']]
								for i,posIndex in enumerate(DmeVertexDeltaData[keywords['pos'] + "Indices"]):
									shape_key.data[posIndex].co += Vector(deltaPositions[i])

							if correctiveSeparator in DmeVertexDeltaData.name:
								flex.AddCorrectiveShapeDrivers.addDrivers(shape_key, DmeVertexDeltaData.name.split(correctiveSeparator))
			
			if smd.jobType in [REF,PHYS]:
				parseModel(DmeModel)
			
			if smd.jobType == ANIM:
				print("Importing DMX animation \"{}\"".format(smd.jobName))
				
				animation = dm.root["animationList"]["animations"][0]
				
				frameRate = animation.get("frameRate",30) # very, very old DMXs don't have this
				timeFrame = animation["timeFrame"]
				scale = timeFrame.get("scale",1.0)
				duration = timeFrame.get("duration") or timeFrame.get("durationTime")
				offset = timeFrame.get("offset") or timeFrame.get("offsetTime",0.0)
				start = timeFrame.get("start", 0)
				
				if type(duration) == int: duration = datamodel.Time.from_int(duration)
				if type(offset) == int: offset = datamodel.Time.from_int(offset)

				lastFrameIndex = 0
								
				keyframes = collections.defaultdict(list)
				unknown_bones = []
				for channel in animation["channels"]:
					toElement = channel["toElement"]
					if not toElement: continue # SFM

					bone_name = smd.boneTransformIDs.get(toElement.id)
					bone = smd.a.pose.bones.get(bone_name) if bone_name else None
					if not bone:
						if self.append != 'NEW_ARMATURE' and toElement.name not in unknown_bones:
							unknown_bones.append(toElement.name)
							print("- Animation refers to unrecognised bone \"{}\"".format(toElement.name))
						continue
					
					is_position_channel = channel["toAttribute"] == "position"
					is_rotation_channel = channel["toAttribute"] == "orientation"
					if not (is_position_channel or is_rotation_channel):
						continue
					
					frame_log = channel["log"]["layers"][0]
					times = frame_log["times"]
					values = frame_log["values"]
					
					for i in range( len(times) ):
						frame_time = times[i] + start
						if type(frame_time) == int: frame_time = datamodel.Time.from_int(frame_time)
						frame_value = values[i]
						
						keyframe = KeyFrame()
						keyframes[bone].append(keyframe)

						keyframe.frame = frame_time * frameRate
						lastFrameIndex = max(lastFrameIndex, keyframe.frame)
						
						if not (bone.parent or keyframe.pos or keyframe.rot):
							keyframe.matrix = getUpAxisMat(smd.upAxis).inverted()
						
						if is_position_channel and not keyframe.pos:
							keyframe.matrix @= Matrix.Translation(frame_value)
							keyframe.pos = True
						elif is_rotation_channel and not keyframe.rot:
							keyframe.matrix @= getBlenderQuat(frame_value).to_matrix().to_4x4()
							keyframe.rot = True
				
				if smd.a == None:
					self.warning(get_id("importer_err_noanimationbones", True).format(smd.jobName))
				else:
					if self.modifyScene:
						smd.a.hide_set(False)
						bpy.context.view_layer.objects.active = smd.a
					if unknown_bones:
						self.warning(get_id("importer_err_missingbones", True).format(smd.jobName,len(unknown_bones),smd.a.name))

					total_frames = ceil((duration * frameRate) if duration else lastFrameIndex) + 1 # need a frame for 0 too!

					# apply the keframes
					self.applyFrames(keyframes,total_frames)
					if smd.a.mode != 'OBJECT' and bpy.context.view_layer.objects.active == smd.a:
						ops.object.mode_set(mode='OBJECT') # the skeleton was matched in edit mode

					smd.num_frames += int(round(start * 2 * frameRate,0)) # the scene's frame range is set from this

		except datamodel.AttributeError as e:
			e.args = ["Invalid DMX file: {}".format(e.args[0] if e.args else "Unknown error")]
			raise
		
		bench.report("DMX imported in")
		return 1

	@classmethod
	def _ensureSceneDmxVersion(cls, version : dmx_version):
		if State.datamodelFormat < version.format:
			bpy.context.scene.vs.dmx_format = version.format_enum
		if State.datamodelEncoding < version.encoding:
			bpy.context.scene.vs.dmx_encoding = str(version.encoding)


class SmdImporter(bpy.types.Operator, SmdImportCore):
	bl_idname = "import_scene.smd"
	bl_label = get_id("importer_title")
	bl_description = get_id("importer_tip")
	bl_options = {'UNDO', 'PRESET'}

	# Properties used by the file browser
	filepath : StringProperty(name="File Path", description="File filepath used for importing the SMD/VTA/DMX/QC file", maxlen=1024, default="", options={'HIDDEN'})
	files : CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN'})
	directory : StringProperty(maxlen=1024, default="", subtype='FILE_PATH', options={'HIDDEN'})
	filter_folder : BoolProperty(name="Filter Folders", description="", default=True, options={'HIDDEN'})
	filter_glob : StringProperty(default="*.smd;*.vta;*.dmx;*.qc;*.qci", options={'HIDDEN'})

	# Custom properties
	doAnim : BoolProperty(name=get_id("importer_doanims"), default=True)
	lazyAnims : BoolProperty(name=get_id("importer_lazyanims"), description=get_id("importer_lazyanims_tip"), default=True)
	includeSearchPath : StringProperty(name=get_id("importer_includemodel_path"), description=get_id("importer_includemodel_path_tip"), subtype='DIR_PATH', default="")
	generateRig : BoolProperty(name=get_id("importer_generate_rig"), description=get_id("importer_generate_rig_tip"), default=True)
	createCollections : BoolProperty(name=get_id("importer_use_collections"), description=get_id("importer_use_collections_tip"), default=True)
	makeCamera : BoolProperty(name=get_id("importer_makecamera"),description=get_id("importer_makecamera_tip"),default=False)
	append : EnumProperty(name=get_id("importer_bones_mode"),description=get_id("importer_bones_mode_desc"),items=(
		('VALIDATE',get_id("importer_bones_validate"),get_id("importer_bones_validate_desc")),
		('APPEND',get_id("importer_bones_append"),get_id("importer_bones_append_desc")),
		('NEW_ARMATURE',get_id("importer_bones_newarm"),get_id("importer_bones_newarm_desc"))),
		default='APPEND')
	upAxis : EnumProperty(name="Up Axis",items=axes,default='Z',description=get_id("importer_up_tip"))
	rotMode : EnumProperty(name=get_id("importer_rotmode"),items=( ('XYZ', "Euler", ''), ('QUATERNION', "Quaternion", "") ),default='XYZ',description=get_id("importer_rotmode_tip"))
	boneMode : EnumProperty(name=get_id("importer_bonemode"),items=(('NONE','Default',''),('ARROWS','Arrows',''),('SPHERE','Sphere','')),default='SPHERE',description=get_id("importer_bonemode_tip"))
	
	def __init__(self, *args, **kwargs):
		bpy.types.Operator.__init__(self, *args, **kwargs)
		Logger.__init__(self)

	def execute(self, context):
		with State.suspend_updates(): # don't rebuild the export list after every file
			result = self._import(context)
		State.update_scene(context.scene)
		return result

	def _import(self, context):
		pre_obs = set(bpy.context.scene.objects)
		pre_eem = context.preferences.edit.use_enter_edit_mode
		pre_append = self.append
		context.preferences.edit.use_enter_edit_mode = False

		self.existingBones = [] # bones which existed before importing began
		self.num_files_imported = 0
		self.num_anims_listed = 0
		excluded_collections = []

		for filepath in [os.path.join(self.directory,file.name) for file in self.files] if self.files else [self.filepath]:
			filepath_lc = filepath.lower()
			if filepath_lc.endswith('.qc') or filepath_lc.endswith('.qci'):
				self.num_files_imported = self.readQC(filepath, False, self.doAnim, self.makeCamera, self.rotMode, outer_qc=True)
				excluded_collections.extend(c for c in (self.qc.lod_collection, self.qc.physics_collection) if c)
				if self.qc.a:
					bpy.context.view_layer.objects.active = self.qc.a
			elif filepath_lc.endswith('.smd'):
				self.num_files_imported = self.readSMD(filepath, self.upAxis, self.rotMode)
			elif filepath_lc.endswith ('.vta'):
				self.num_files_imported = self.readSMD(filepath, self.upAxis, self.rotMode, smd_type=FLEX)
			elif filepath_lc.endswith('.dmx'):
				self.num_files_imported = self.readDMX(filepath, self.upAxis, self.rotMode)
			else:
				if len(filepath_lc) == 0:
					self.report({'ERROR'},get_id("importer_err_nofile"))
				else:
					self.report({'ERROR'},get_id("importer_err_badfile", True).format(os.path.basename(filepath)))

			self.append = pre_append

		# LODs and physics meshes stay in the file, but are left out of the view layer
		for collection in excluded_collections:
			layer_collection = _findLayerCollection(context.view_layer.layer_collection, collection)
			if layer_collection:
				layer_collection.exclude = True

		if self.num_anims_listed:
			message = get_id("importer_complete_anims", True).format(self.num_files_imported,self.num_anims_listed,self.elapsed_time())
		else:
			message = get_id("importer_complete", True).format(self.num_files_imported,self.elapsed_time())
		self.errorReport(message)
		if self.num_files_imported:
			ops.object.select_all(action='DESELECT')
			view_obs = set(context.view_layer.objects)
			new_obs = set(bpy.context.scene.objects).difference(pre_obs).intersection(view_obs)
			xy = xyz = 0
			for ob in new_obs:
				ob.select_set(True)
				# FIXME: assumes meshes are centered around their origins
				xy = max(xy, int(max(ob.dimensions[0],ob.dimensions[1])) )
				xyz = max(xyz, max(xy,int(ob.dimensions[2])))
			active = self.qc.a if self.qc else self.smd.a
			if active in view_obs:
				bpy.context.view_layer.objects.active = active
			for area in context.screen.areas:
				if area.type == 'VIEW_3D':
					area.spaces.active.clip_end = max( area.spaces.active.clip_end, xyz * 2 )
		if bpy.context.area and bpy.context.area.type == 'VIEW_3D' and bpy.context.region:
			ops.view3d.view_selected()

		# an animation file imported on its own: play it
		smd = getattr(self, "smd", None)
		if not self.qc and smd and smd.jobType == ANIM and smd.num_frames:
			context.scene.frame_start = 0
			context.scene.frame_end = max(0, smd.num_frames - 1)
			context.scene.frame_set(0)

		context.preferences.edit.use_enter_edit_mode = pre_eem
		self.append = pre_append

		return {'FINISHED'}

	def invoke(self, context, event):
		self.upAxis = context.scene.vs.up_axis
		bpy.context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}

class AnimLoader(SmdImportCore):
	"""Imports animation files into an existing armature, for the QC animation list. Unlike the import operator it
	leaves the viewport, the selection, the object mode and the scene alone."""
	def __init__(self, armature : bpy.types.Object | None, rotMode = 'XYZ', upAxis = None, action : bpy.types.Action | None = None, assign_slot = True):
		Logger.__init__(self)
		self.target_armature = armature
		self.target_action = action
		self.assign_slot = assign_slot
		self.modifyScene = False
		self.append = 'VALIDATE'
		self.boneMode = 'NONE'
		self.createCollections = False
		self.rotMode = rotMode
		self.upAxis = upAxis or bpy.context.scene.vs.up_axis
		self.existingBones = []
		self.num_files_imported = 0
		self.lazyAnims = True

	def report(self, type, message):
		print(message)

	def load(self, filepath : str) -> SmdInfo | None:
		"""Returns the SmdInfo of the imported animation, which holds the new action, slot and frame count."""
		self.append = 'VALIDATE'
		read = self.readDMX if filepath.lower().endswith(".dmx") else self.readSMD
		try:
			imported = read(filepath, self.upAxis, self.rotMode, False, ANIM)
		except Exception as err:
			self.error(get_id("importer_err_smd", True).format(os.path.basename(filepath), err))
			return None
		if imported and self.smd.created_action:
			self.num_files_imported += 1
			return self.smd
		return None

	def capture(self, filepath : str) -> tuple[dict[str, list[KeyFrame]], int] | None:
		"""Reads an animation without keying it. Returns its keyframes by bone name, and its frame count."""
		self.append = 'VALIDATE'
		self.capture_only = True
		read = self.readDMX if filepath.lower().endswith(".dmx") else self.readSMD
		try:
			imported = read(filepath, self.upAxis, self.rotMode, False, ANIM)
		except Exception as err:
			self.error(get_id("importer_err_smd", True).format(os.path.basename(filepath), err))
			return None
		finally:
			self.capture_only = False
		return self.smd.captured if imported else None

	def key_frames(self, name : str, frames : dict[str, list[Matrix]], num_frames : int) -> SmdInfo:
		"""Keys bone-local matrices, in the space of the keyframes which capture() returns, into a new animation."""
		arm = self.target_armature
		assert(arm)
		smd = self.smd = SmdInfo(name)
		smd.jobType = ANIM
		smd.a = arm
		smd.upAxis = self.upAxis
		smd.rotMode = self.rotMode
		keyframes = {}
		for bone_name, matrices in frames.items():
			bone = arm.pose.bones.get(bone_name)
			if not bone:
				continue
			bone_keyframes = keyframes[bone] = []
			for frame, matrix in enumerate(matrices):
				keyframe = KeyFrame()
				keyframe.frame = frame
				keyframe.matrix = matrix
				keyframe.pos = keyframe.rot = True
				bone_keyframes.append(keyframe)
		self._keyAnimation(keyframes, num_frames)
		return smd
