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

# Evaluates the QC sequences which can't be played by importing a single file: delta sequences, which the engine layers
# on another animation, and blend sequences, whose animations are mixed by pose parameters. The result is keyed like any
# other animation (see anim_list.load_item).
#
# Transforms are bone-local, in the space of the imported files' keyframes: what studiomdl reads, with root bones
# relative to the up axis. The engine composes them in that space too, so the results match it.

import bpy, os, collections
from mathutils import Quaternion, Vector
from .utils import KeyFrame, boneParentSpace, getUpAxisMat, isRigBone

Transform = tuple[Vector, Quaternion]
IDENTITY : Transform = (Vector(), Quaternion())

#
# Math
#

def blend_grid(count : int, width : int, num_params : int) -> tuple[int, int]:
	"""The columns and rows which studiomdl lays a blend sequence's animations out in. The first pose parameter
	varies along the rows."""
	if width > 0:
		return width, max(1, count // width)
	if num_params < 2 or count < 4:
		return count, 1
	side = round(count ** 0.5)
	if side * side == count:
		return side, side
	return count, 1

def blend_axis(value : float, start : float, end : float, count : int) -> tuple[int, float]:
	"""The animation which a pose parameter value selects along a blend axis of count animations, and the weight of
	the next one (Studio_LocalPoseParameter)."""
	if count < 2:
		return 0, 0.0
	s = (value - start) / (end - start) if end != start else 0.0
	s = min(max(s, 0.0), 1.0)
	if count == 2:
		return 0, s
	scaled = s * (count - 1)
	index = min(int(scaled), count - 2)
	return index, scaled - index

def blend_position(count : int, width : int, params : list[tuple[str, float, float]], values : dict[str, float]) -> tuple[int, float, int, float, int]:
	"""(column, weight of the next column, row, weight of the next row, columns) of a blend sequence."""
	width = max(1, min(width, count))
	height = max(1, count // width)
	def axis(i, n):
		if i >= len(params):
			return 0, 0.0
		name, start, end = params[i]
		return blend_axis(values.get(name.lower(), 0.0), start, end, n)
	x, fx = axis(0, width)
	y, fy = axis(1, height)
	return x, fx, y, fy, width

def blend_weights(count : int, width : int, params : list[tuple[str, float, float]], values : dict[str, float]) -> list[tuple[int, float]]:
	"""The animations which a blend sequence mixes, and their weights."""
	x, fx, y, fy, width = blend_position(count, width, params, values)
	weights = []
	for dy, wy in ((0, 1 - fy), (1, fy)):
		for dx, wx in ((0, 1 - fx), (1, fx)):
			if wx * wy > 0:
				weights.append(((y + dy) * width + x + dx, wx * wy))
	return weights

def subtract(a : Transform, b : Transform, post : bool) -> Transform:
	"""The delta which takes pose b to pose a, as studiomdl's "subtract" (post) and "presubtract" options make it."""
	rot = b[1].inverted() @ a[1] if post else a[1] @ b[1].inverted()
	return a[0] - b[0], rot.normalized()

def apply_delta(base : Transform, delta : Transform, post : bool) -> Transform:
	"""Layers a delta on a pose like the engine does: a post delta rotates in the bone's own space, after the base
	rotation; other deltas rotate in the parent's space. Positions are added."""
	rot = base[1] @ delta[1] if post else delta[1] @ base[1]
	return base[0] + delta[0], rot.normalized()

def interpolate(a : Transform, b : Transform, f : float) -> Transform:
	if f <= 0:
		return a
	if f >= 1:
		return b
	return a[0].lerp(b[0], f), a[1].slerp(b[1], f)

def sample(transforms : list[Transform], frame : float) -> Transform:
	last = len(transforms) - 1
	if frame <= 0 or last <= 0:
		return transforms[0]
	if frame >= last:
		return transforms[last]
	i = int(frame)
	return interpolate(transforms[i], transforms[i + 1], frame - i)

#
# Animation files
#

class Track:
	"""An animation file's transforms, for every frame of every bone which it keys."""
	def __init__(self, keyframes : dict[str, list[KeyFrame]], num_frames : int):
		self.num_frames = max(1, num_frames)
		self.bones : dict[str, list[Transform]] = {}
		for name, bone_keyframes in keyframes.items():
			locs : list[Vector | None] = [None] * self.num_frames
			rots : list[Quaternion | None] = [None] * self.num_frames
			for keyframe in bone_keyframes:
				frame = min(max(int(round(keyframe.frame)), 0), self.num_frames - 1)
				if keyframe.pos:
					locs[frame] = keyframe.matrix.to_translation()
				if keyframe.rot:
					rots[frame] = keyframe.matrix.to_quaternion()
			self.bones[name] = list(zip(_hold(locs, Vector()), _hold(rots, Quaternion())))

def _hold(values : list, default) -> list:
	"""Fills frames without a key with the previous key, or the first key before it."""
	previous = next((v for v in values if v is not None), default)
	for i, value in enumerate(values):
		if value is None:
			values[i] = previous
		else:
			previous = value
	return values

_tracks : collections.OrderedDict[tuple, Track] = collections.OrderedDict()
_TRACK_CACHE_SIZE = 128

def load_track(loader, arm : bpy.types.Object, filepath : str) -> Track | None:
	path = os.path.normpath(bpy.path.abspath(filepath))
	try:
		key = (os.path.normcase(path), os.path.getmtime(path), arm.data.name, loader.upAxis)
	except OSError:
		return None
	track = _tracks.get(key)
	if track is None:
		captured = loader.capture(path)
		if not captured:
			return None
		track = _tracks[key] = Track(*captured)
		while len(_tracks) > _TRACK_CACHE_SIZE:
			_tracks.popitem(last=False)
	else:
		_tracks.move_to_end(key)
	return track

def clear_cache():
	_tracks.clear()

def rest_transforms(arm : bpy.types.Object, up_axis : str) -> dict[str, Transform]:
	"""The rest pose, in the space of the keyframes: the transform which _keyAnimation turns into an identity basis."""
	legacy = arm.data.vs.legacy_rotation
	up = getUpAxisMat(up_axis)
	transforms = {}
	for bone in arm.data.bones:
		if isRigBone(bone):
			continue
		parent_space, right = boneParentSpace(bone, legacy, up)
		matrix = parent_space.inverted() @ bone.matrix_local
		if right is not None:
			matrix = matrix @ right.inverted()
		transforms[bone.name] = (matrix.to_translation(), matrix.to_quaternion())
	return transforms

#
# Sequences
#

class Evaluation:
	def __init__(self, num_frames : int, fps : float, is_loop : bool, bones : dict[str, list[Transform]]):
		self.num_frames = num_frames
		self.fps = fps
		self.is_loop = is_loop
		self.bones = bones

def pose_param_values(vs) -> dict[str, float]:
	return {param.name.lower(): param.value for param in vs.qc_pose_params}

def delta_base(vs, item):
	"""The sequence which a delta sequence is layered on, or None for the rest pose."""
	if not item.is_delta:
		return None
	name = vs.qc_delta_base.lower()
	if not name:
		return None
	return next((other for other in vs.qc_anims if other.name.lower() == name and not other.is_delta and len(other.components)), None)

def bake_key(vs, item) -> str:
	"""What an evaluation of the sequence depends on besides its files. A loaded sequence is out of date when it changes."""
	names = [param.name for param in item.blend_params]
	parts = []
	if item.is_delta:
		base = delta_base(vs, item)
		parts.append("base=" + (base.name.lower() if base else ""))
		if base:
			names += [param.name for param in base.blend_params]
	values = pose_param_values(vs)
	parts += ["{}={:g}".format(name.lower(), values.get(name.lower(), 0.0)) for name in names]
	return "|".join(parts)

def evaluate(loader, arm : bpy.types.Object, item, values : dict[str, float] | None = None) -> Evaluation | None:
	"""Every frame of a sequence at the current pose parameter values, layered on its base if it is a delta."""
	vs = arm.vs
	values = pose_param_values(vs) if values is None else values
	components = item.components
	count = len(components)
	if not count:
		return None
	params = [(param.name, param.start, param.end) for param in item.blend_params]
	x, fx, y, fy, width = blend_position(count, item.blend_width, params, values)
	weights = blend_weights(count, item.blend_width, params, values)

	tracks : dict[int, Track] = {}
	subtracts : dict[int, Track] = {}
	for index, _ in weights:
		component = components[index]
		track = load_track(loader, arm, component.filepath)
		if not track:
			return None
		tracks[index] = track
		if component.subtract_filepath:
			subtract_track = load_track(loader, arm, component.subtract_filepath)
			if subtract_track:
				subtracts[index] = subtract_track

	main = max(weights, key=lambda w: w[1])[0] # the most weighted animation sets the length and speed
	num_frames = tracks[main].num_frames
	fps = components[main].fps
	rest = rest_transforms(arm, loader.upAxis)

	def component_frame(track : Track, i : int):
		return i * (track.num_frames - 1) / (num_frames - 1) if num_frames > 1 else 0

	def component_value(index : int, bone : str, i : int) -> Transform:
		track = tracks[index]
		transforms = track.bones.get(bone)
		if transforms is None:
			value = IDENTITY if item.is_delta else rest.get(bone, IDENTITY)
		else:
			value = sample(transforms, component_frame(track, i))
		subtract_track = subtracts.get(index)
		if subtract_track:
			base = subtract_track.bones.get(bone)
			component = components[index]
			value = subtract(value, sample(base, component.subtract_frame) if base else IDENTITY, component.subtract_post)
		return value

	def mix(a, b, f : float) -> Transform:
		"""Interpolates the transforms which a() and b() return, only evaluating those with a weight."""
		if f <= 0:
			return a()
		if f >= 1:
			return b()
		return interpolate(a(), b(), f)

	def blended_value(bone : str, i : int) -> Transform:
		def row(r):
			return mix(lambda: component_value(r * width + x, bone, i), lambda: component_value(r * width + x + 1, bone, i), fx)
		return mix(lambda: row(y), lambda: row(y + 1), fy)

	bone_names = set()
	for track in tracks.values():
		bone_names.update(track.bones)

	if not item.is_delta:
		bones = {bone: [blended_value(bone, i) for i in range(num_frames)] for bone in bone_names}
		return Evaluation(num_frames, fps, item.is_loop, bones)

	base_item = delta_base(vs, item)
	base = evaluate(loader, arm, base_item, values) if base_item else None
	def base_value(bone : str, i : int) -> Transform:
		transforms = base.bones.get(bone) if base else None
		if not transforms:
			return rest.get(bone, IDENTITY)
		frame = i / fps * base.fps if fps > 0 else i
		if base.is_loop and base.num_frames > 1:
			frame %= base.num_frames - 1
		return sample(transforms, frame)

	bone_names.update(rest)
	bones = {bone: [apply_delta(base_value(bone, i), blended_value(bone, i), item.is_post) for i in range(num_frames)] for bone in bone_names}
	return Evaluation(num_frames, fps, item.is_loop, bones)
