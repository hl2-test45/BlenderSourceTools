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

# Lists the sequences of an imported QC on its armature. A character's QC and the models it $includemodels can define
# hundreds of sequences, so they are only imported when the user asks for one. Each loaded sequence becomes a slot of
# the armature's QC action (or an action of its own before Blender 4.4), which the list switches between.
#
# Most sequences play one animation file, which is imported as it is. Delta sequences, which the engine layers on
# another animation, and blend sequences, which mix animations by pose parameters, are composed by qc_compose.py
# (Blender 4.4+): deltas on the rest pose or on a chosen base sequence, blends at the armature's pose parameter values.
# A composed sequence is composed again when it is played after those have changed.

import bpy, os, time
from bpy.props import IntProperty
from mathutils import Matrix
from .utils import *
from . import qc_compose

_suppress_activation = False
_suppress_refresh = False
_pending_refresh : set[str] = set()

#
# Data
#

def get_armature(context) -> bpy.types.Object | None:
	"""The armature of the active object, if any."""
	ob = context.active_object
	if not ob:
		return None
	if ob.type == 'ARMATURE':
		return ob
	if ob.parent and ob.parent.type == 'ARMATURE':
		return ob.parent
	return ob.find_armature()

def _slot_handles(action : bpy.types.Action | None) -> set[int]:
	return {slot.handle for slot in action.slots} if action and State.useActionSlots else set()

def is_loaded(item, handles : set[int] | None = None) -> bool:
	if not item.action:
		return False
	if not State.useActionSlots:
		return True
	if handles is not None:
		return item.slot_handle in handles
	return findActionSlot(item.action, item.slot_handle) is not None

def needs_compose(item) -> bool:
	"""Whether the sequence is anything but one animation file played as it is."""
	return item.is_delta or len(item.components) > 1 or any(component.subtract_filepath for component in item.components)

def can_load(item) -> bool:
	return len(item.components) > 0 and (State.useActionSlots or not needs_compose(item))

def is_stale(vs, item) -> bool:
	"""A loaded composed sequence whose delta base or pose parameters have changed since."""
	return needs_compose(item) and is_loaded(item) and item.baked_key != qc_compose.bake_key(vs, item)

def is_legacy(item) -> bool:
	"""A row of the per-file animation list which older versions made."""
	return not len(item.components)

def blend_size(item) -> tuple[int, int]:
	count = len(item.components)
	width = max(1, min(item.blend_width, count))
	return width, max(1, count // width)

def blend_params(item) -> list[tuple[str, float, float]]:
	return [(param.name, param.start, param.end) for param in item.blend_params]

def _matches(item, needle : str) -> bool:
	return needle in item.name.lower() or needle in item.activity.lower() or any(needle in component.name.lower() for component in item.components)

def filtered_indices(vs, loaded_only = None) -> list[int]:
	"""Indices of the sequences shown by the list with its current filter."""
	needle = vs.qc_anims_filter.lower()
	loaded_only = vs.qc_anims_loaded_only if loaded_only is None else loaded_only
	handles = _slot_handles(vs.qc_action)
	indices = []
	for i, item in enumerate(vs.qc_anims):
		if item.is_delta and not vs.qc_anims_show_delta:
			continue
		if loaded_only and not is_loaded(item, handles):
			continue
		if needle and not _matches(item, needle):
			continue
		indices.append(i)
	return indices

def store_records(arm : bpy.types.Object, records : list[QcSequenceRecord], loaded : dict, qc_path : str, rot_mode : str, up_axis : str,
				  search_path : str = "", unresolved_includes : list[str] | None = None, pose_params : dict | None = None):
	"""Adds a QC's sequences to the armature's list. Sequences which are already listed keep their loaded animation."""
	global _suppress_activation, _suppress_refresh
	vs = arm.vs
	_suppress_activation = _suppress_refresh = True
	try:
		for i in reversed(range(len(vs.qc_anims))):
			if is_legacy(vs.qc_anims[i]):
				vs.qc_anims.remove(i) # its animation stays in the QC action
		if vs.qc_anims_active >= len(vs.qc_anims):
			vs.qc_anims_active = -1

		# indices, as adding to a collection can move its items
		existing = {item.name.lower(): i for i, item in enumerate(vs.qc_anims)}
		for record in records:
			key = record.name.lower()
			index = existing.get(key)
			if index is None:
				index = existing[key] = len(vs.qc_anims)
				vs.qc_anims.add().name = record.name
			item = vs.qc_anims[index]
			item.source_qc = record.source_qc
			item.activity = record.activity
			item.fps = record.fps
			item.is_delta = record.is_delta
			item.is_post = record.is_post
			item.is_hidden = record.is_hidden
			item.is_loop = record.is_loop
			item.layers = ", ".join(record.layers)
			item.blend_width = max(1, record.blend_width)
			item.components.clear()
			for component in record.components:
				c = item.components.add()
				c.name = component.name
				c.filepath = component.filepath
				c.fps = component.fps
				c.subtract_filepath = component.subtract_filepath
				c.subtract_frame = component.subtract_frame
				c.subtract_post = component.subtract_post
			item.blend_params.clear()
			for name, start, end in record.blend_params:
				param = item.blend_params.add()
				param.name = name
				param.start = start
				param.end = end

			state = loaded.get(key)
			if state:
				action, slot_handle, num_frames = state
				item.action = action
				item.slot_handle = slot_handle
				item.num_frames = num_frames
				if State.useActionSlots and not vs.qc_action:
					vs.qc_action = action
		_add_pose_params(vs, records, pose_params or {})
	finally:
		_suppress_activation = _suppress_refresh = False

	if not vs.qc_path:
		vs.qc_path = qc_path
		vs.qc_rot_mode = rot_mode
		vs.qc_up_axis = up_axis
	if os.path.normcase(bpy.path.abspath(vs.qc_path)) == os.path.normcase(qc_path):
		vs.qc_missing_includes = ";".join(unresolved_includes or [])
	if search_path:
		vs.qc_include_search_path = search_path

def _add_pose_params(vs, records : list[QcSequenceRecord], pose_params : dict[str, tuple[str, float, float]]):
	"""Adds the pose parameters which the armature doesn't have yet: the QC's, and those which blend sequences use
	without a $poseparameter, over the range the sequences cover. Existing ones keep their value."""
	ranges = dict(pose_params)
	for record in records:
		for name, start, end in record.blend_params:
			key = name.lower()
			if key in pose_params:
				continue
			_, low, high = ranges.get(key, (name, min(start, end), max(start, end)))
			ranges[key] = (name, min(low, start, end), max(high, start, end))
	existing = {param.name.lower() for param in vs.qc_pose_params}
	for key, (name, low, high) in ranges.items():
		if key not in existing:
			param = vs.qc_pose_params.add()
			param.name = name
			param.min_value = min(low, high)
			param.max_value = max(low, high)

def _qc_action(arm : bpy.types.Object) -> bpy.types.Action | None:
	"""The action which holds the slots of the armature's QC sequences (Blender 4.4+)."""
	if not State.useActionSlots:
		return None
	vs = arm.vs
	if not vs.qc_action:
		name = os.path.splitext(os.path.basename(vs.qc_path))[0] if vs.qc_path else arm.name
		vs.qc_action = bpy.data.actions.new(name)
	return vs.qc_action

def make_loader(arm : bpy.types.Object):
	from .import_smd import AnimLoader
	vs = arm.vs
	return AnimLoader(arm, rotMode=vs.qc_rot_mode or 'XYZ', upAxis=vs.qc_up_axis or None, action=_qc_action(arm), assign_slot=False)

def load_item(loader, arm : bpy.types.Object, index : int) -> bool:
	"""Imports a sequence, or composes it again if it is out of date."""
	vs = arm.vs
	item = vs.qc_anims[index]
	if is_loaded(item) and not is_stale(vs, item):
		return True
	if not can_load(item):
		return False
	if is_loaded(item):
		unload(arm, index)

	if needs_compose(item):
		evaluation = qc_compose.evaluate(loader, arm, item)
		if not evaluation:
			return False
		frames = {bone: [Matrix.LocRotScale(loc, rot, None) for loc, rot in transforms] for bone, transforms in evaluation.bones.items()}
		smd = loader.key_frames(item.name, frames, evaluation.num_frames)
		item.fps = evaluation.fps
		item.baked_key = qc_compose.bake_key(vs, item)
	else:
		smd = loader.load(bpy.path.abspath(item.components[0].filepath))
		if not smd:
			return False
	item.action = smd.created_action
	item.slot_handle = smd.created_slot.handle if smd.created_slot else -1
	item.num_frames = smd.num_frames
	return True

def relay_log(loader, logger):
	if logger:
		logger.log_warnings.extend(loader.log_warnings)
		logger.log_errors.extend(loader.log_errors)

def load_items(context, arm : bpy.types.Object, indices : list[int], logger = None, activate_last = True) -> list[int]:
	"""Imports the listed sequences at indices and returns the indices which were loaded."""
	loader = make_loader(arm)
	loaded = []
	with State.suspend_updates(quiet = len(indices) > 1):
		for index in indices:
			if load_item(loader, arm, index):
				loaded.append(index)
	relay_log(loader, logger)
	if activate_last and loaded:
		set_active(arm, loaded[-1])
	return loaded

def set_active(arm : bpy.types.Object, index : int):
	"""Selects an item in the list, which plays it if it is loaded."""
	arm.vs.qc_anims_active = index # calls on_active_changed

def activate(context, arm : bpy.types.Object, index : int, keep_frame = False) -> bool:
	"""Makes a loaded sequence the armature's current animation and fits the scene to it. A composed sequence whose base
	or pose parameters have changed is composed again first. With keep_frame, the pose and frame are left alone."""
	vs = arm.vs
	if is_stale(vs, vs.qc_anims[index]):
		with State.suspend_updates():
			load_item(make_loader(arm), arm, index)
	item = vs.qc_anims[index]
	if not is_loaded(item):
		return False
	ad = arm.animation_data or arm.animation_data_create()

	if not keep_frame:
		# Blender doesn't reset channels which the new animation doesn't key
		for bone in arm.pose.bones:
			bone.matrix_basis.identity()
		from . import rig
		rig.reset_ik_fk(arm)

	if ad.action != item.action:
		ad.action = item.action
	if State.useActionSlots:
		ad.action_slot = findActionSlot(item.action, item.slot_handle)

	scene = context.scene
	scene.frame_start = 0
	scene.frame_end = max(0, item.num_frames - 1)
	if item.fps > 0:
		scene.render.fps = max(1, round(item.fps))
		scene.render.fps_base = scene.render.fps / item.fps
	scene.frame_set(min(scene.frame_current, scene.frame_end) if keep_frame else 0)
	return True

def refresh_active(context, arm : bpy.types.Object) -> bool:
	"""Composes the armature's current sequence again if its delta base or pose parameters have changed."""
	vs = arm.vs
	index = vs.qc_anims_active
	if not 0 <= index < len(vs.qc_anims) or not is_stale(vs, vs.qc_anims[index]):
		return False
	return activate(context, arm, index, keep_frame=True)

def schedule_refresh(arm : bpy.types.Object):
	"""Refreshes the armature's current sequence shortly, once a slider has stopped sending changes for a moment."""
	_pending_refresh.add(arm.name)
	if not bpy.app.timers.is_registered(_refresh_pending):
		bpy.app.timers.register(_refresh_pending, first_interval=0.15)

def _refresh_pending():
	names = list(_pending_refresh)
	_pending_refresh.clear()
	wm = bpy.context.window_manager
	window = bpy.context.window or (wm.windows[0] if wm and wm.windows else None)
	for name in names:
		arm = bpy.data.objects.get(name)
		if not (arm and arm.type == 'ARMATURE'):
			continue
		if window: # reading a DMX changes the object mode, which needs a window
			with bpy.context.temp_override(window=window):
				refresh_active(bpy.context, arm)
		else:
			refresh_active(bpy.context, arm)
	return None

def unload(arm : bpy.types.Object, index : int):
	item = arm.vs.qc_anims[index]
	ad = arm.animation_data
	if State.useActionSlots:
		slot = findActionSlot(item.action, item.slot_handle)
		if slot:
			if ad and ad.action == item.action and ad.action_slot == slot:
				ad.action_slot = None
			item.action.slots.remove(slot)
	elif item.action:
		if ad and ad.action == item.action:
			ad.action = None
		bpy.data.actions.remove(item.action)
	item.action = None
	item.slot_handle = -1

def on_active_changed(props, context):
	if _suppress_activation:
		return
	arm = props.id_data
	if isinstance(arm, bpy.types.Object) and arm.type == 'ARMATURE' and 0 <= props.qc_anims_active < len(props.qc_anims):
		activate(context, arm, props.qc_anims_active)

def on_pose_param_changed(param, context):
	if _suppress_refresh:
		return
	arm = param.id_data
	if isinstance(arm, bpy.types.Object) and arm.type == 'ARMATURE':
		schedule_refresh(arm)

def on_delta_base_changed(props, context):
	on_pose_param_changed(props, context)

#
# UI
#

class SMD_UL_QcAnims(bpy.types.UIList):
	def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
		loaded = is_loaded(item)
		row = layout.row(align=True)
		row.active = not item.is_hidden
		row.label(text=item.name, icon='ACTION' if loaded else 'LAYER_USED')
		tags = []
		if item.is_delta:
			tags.append("Δ")
		if len(item.components) > 1:
			tags.append("{}×{}".format(*blend_size(item)))
		if loaded:
			tags.append("{}f".format(item.num_frames))
		if tags:
			info = row.row(align=True)
			info.alignment = 'RIGHT'
			info.enabled = False
			info.label(text="  ".join(tags))
		if loaded:
			row.operator(SMD_OT_QcAnimUnload.bl_idname, text="", icon='X', emboss=False).index = index
		elif can_load(item):
			row.operator(SMD_OT_QcAnimLoad.bl_idname, text="", icon='IMPORT', emboss=False).index = index
		else:
			row.label(text="", icon='ERROR')

	def draw_filter(self, context, layout):
		row = layout.row(align=True)
		row.prop(self, "use_filter_sort_alpha", text="", icon='SORTALPHA')
		row.prop(self, "use_filter_sort_reverse", text="", icon='SORT_DESC' if self.use_filter_sort_reverse else 'SORT_ASC')

	def filter_items(self, context, data, propname):
		items = getattr(data, propname)
		visible = set(filtered_indices(data))
		flags = [self.bitflag_filter_item if i in visible else 0 for i in range(len(items))]
		order = bpy.types.UI_UL_list.sort_items_by_name(items, "name") if self.use_filter_sort_alpha else []
		return flags, order

class SMD_PT_QcAnims(bpy.types.Panel):
	bl_label = get_id("qc_anims_title")
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'UI'
	bl_category = "Source"

	@classmethod
	def poll(cls, context):
		arm = get_armature(context)
		return bool(arm and (len(arm.vs.qc_anims) or arm.vs.qc_path))

	def draw(self, context):
		l = self.layout
		arm = get_armature(context)
		vs = arm.vs

		loaded = len(_slot_handles(vs.qc_action)) if State.useActionSlots else sum(1 for item in vs.qc_anims if item.action)
		playable = sum(1 for item in vs.qc_anims if not item.is_delta)
		l.label(text=get_id("qc_anims_count", True).format(playable, len(vs.qc_anims), loaded), icon='ARMATURE_DATA')

		row = l.row(align=True)
		row.prop(vs, "qc_anims_filter", text="", icon='VIEWZOOM')
		row.prop(vs, "qc_anims_loaded_only", text="", icon='ACTION')
		row.prop(vs, "qc_anims_show_delta", text="", icon='NLA')

		l.template_list("SMD_UL_QcAnims", "", vs, "qc_anims", vs, "qc_anims_active", rows=8, maxrows=20)

		row = l.row(align=True)
		row.operator(SMD_OT_QcAnimLoadFiltered.bl_idname, icon='IMPORT')
		if 0 <= vs.qc_anims_active < len(vs.qc_anims):
			self.draw_sequence(l, vs, vs.qc_anims[vs.qc_anims_active])

		box = l.box()
		col = box.column(align=True)
		if any(is_legacy(item) for item in vs.qc_anims):
			col.label(text=get_id("qc_anims_legacy"), icon='ERROR')
		if vs.qc_missing_includes:
			for name in vs.qc_missing_includes.split(";"):
				col.label(text=get_id("qc_anims_missing_include", True).format(name), icon='ERROR')
		col.prop(vs, "qc_include_search_path", text="")
		col.operator(SMD_OT_QcAnimsRescan.bl_idname, icon='FILE_REFRESH')

	def draw_sequence(self, layout, vs, item):
		box = layout.box()
		col = box.column(align=True)
		flags = [flag for flag, on in (("delta", item.is_delta), ("loop", item.is_loop), ("hidden", item.is_hidden)) if on]
		col.label(text="{}  ·  {:g} fps{}".format(item.source_qc, item.fps, "  ·  " + ", ".join(flags) if flags else ""), icon='FILE')
		if item.activity:
			col.label(text=item.activity, icon='POSE_HLT')
		if item.layers:
			col.label(text=get_id("qc_anim_layers_info", True).format(item.layers), icon='NLA')

		count = len(item.components)
		values = qc_compose.pose_param_values(vs)
		if count > 1:
			col = box.column(align=True)
			width, height = blend_size(item)
			col.label(text=get_id("qc_anim_blend_info", True).format(width, height, " × ".join(param.name for param in item.blend_params) or "-"), icon='MOD_ARRAY')
			for index, weight in sorted(qc_compose.blend_weights(count, item.blend_width, blend_params(item), values)):
				if weight >= 0.005:
					col.label(text="{}   {:.0%}".format(item.components[index].name, weight), icon='DOT')

		names = [param.name for param in item.blend_params]
		if item.is_delta:
			col = box.column(align=True)
			col.prop_search(vs, "qc_delta_base", vs, "qc_anims", text="", icon='ARMATURE_DATA')
			base = qc_compose.delta_base(vs, item)
			if base:
				names += [param.name for param in base.blend_params if param.name.lower() not in {name.lower() for name in names}]
			elif vs.qc_delta_base:
				col.label(text=get_id("qc_anim_base_invalid", True).format(vs.qc_delta_base), icon='ERROR')
			else:
				col.label(text=get_id("qc_anim_rest_pose"), icon='INFO')

		if names:
			col = box.column(align=True)
			col.label(text=get_id("qc_pose_params"), icon='DRIVER')
			params = {param.name.lower(): param for param in vs.qc_pose_params}
			for name in names:
				param = params.get(name.lower())
				if param:
					col.prop(param, "value", text="{}  [{:g}, {:g}]".format(param.name, param.min_value, param.max_value))

		if needs_compose(item) and not State.useActionSlots:
			box.label(text=get_id("qc_anim_needs_slots"), icon='ERROR')

#
# Operators
#

class _QcAnimOperator:
	@classmethod
	def poll(cls, context):
		arm = get_armature(context)
		return bool(arm and len(arm.vs.qc_anims) and (context.mode in ('OBJECT', 'POSE')))

class SMD_OT_QcAnimLoad(_QcAnimOperator, bpy.types.Operator):
	bl_idname = "smd.qc_anim_load"
	bl_label = get_id("qc_anim_load")
	bl_description = get_id("qc_anim_load_tip")
	bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

	index : IntProperty(min=0)

	def execute(self, context):
		arm = get_armature(context)
		if self.index >= len(arm.vs.qc_anims):
			return {'CANCELLED'}
		item = arm.vs.qc_anims[self.index]
		if not can_load(item):
			self.report({'ERROR'}, get_id("qc_anim_needs_slots", True))
			return {'CANCELLED'}
		logger = Logger()
		loaded = load_items(context, arm, [self.index], logger=logger)
		for message in logger.log_errors + logger.log_warnings:
			self.report({'WARNING'}, message)
		if not loaded:
			self.report({'ERROR'}, get_id("qc_anim_load_failed", True).format(arm.vs.qc_anims[self.index].name))
			return {'CANCELLED'}
		State.update_scene(context.scene)
		return {'FINISHED'}

class SMD_OT_QcAnimLoadFiltered(_QcAnimOperator, bpy.types.Operator):
	bl_idname = "smd.qc_anim_load_filtered"
	bl_label = get_id("qc_anim_load_filtered")
	bl_description = get_id("qc_anim_load_filtered_tip")
	bl_options = {'REGISTER', 'UNDO'}

	def _begin(self, context):
		arm = get_armature(context)
		vs = arm.vs
		self.arm_name = arm.name
		self.todo = [i for i in filtered_indices(vs, loaded_only=False) if can_load(vs.qc_anims[i]) and not is_loaded(vs.qc_anims[i])]
		self.total = len(self.todo)
		self.loaded = []
		self.loader = make_loader(arm)
		self.started = time.time()
		return arm

	def _load_some(self, arm, seconds):
		deadline = time.perf_counter() + seconds
		with State.suspend_updates(quiet=True):
			while self.todo and time.perf_counter() < deadline:
				index = self.todo.pop(0)
				if load_item(self.loader, arm, index):
					self.loaded.append(index)

	def _finish(self, context, arm):
		for message in self.loader.log_errors:
			self.report({'WARNING'}, message)
		if self.loaded:
			set_active(arm, self.loaded[-1])
		State.update_scene(context.scene)
		self.report({'INFO'}, get_id("qc_anim_loaded_count", True).format(len(self.loaded), round(time.time() - self.started, 1)))
		return {'FINISHED'}

	def execute(self, context): # scripts, and Redo
		arm = self._begin(context)
		self._load_some(arm, float("inf"))
		return self._finish(context, arm)

	def invoke(self, context, event):
		arm = self._begin(context)
		if not self.todo:
			self.report({'INFO'}, get_id("qc_anim_nothing_to_load", True))
			return {'CANCELLED'}
		wm = context.window_manager
		self.timer = wm.event_timer_add(0.01, window=context.window)
		wm.progress_begin(0, self.total)
		wm.modal_handler_add(self)
		return {'RUNNING_MODAL'}

	def modal(self, context, event):
		wm = context.window_manager
		arm = bpy.data.objects.get(self.arm_name)
		if event.type == 'ESC' or not arm:
			self.todo = []
		elif event.type != 'TIMER':
			return {'PASS_THROUGH'}
		else:
			self._load_some(arm, 0.1)
			wm.progress_update(self.total - len(self.todo))
			if context.area:
				context.area.tag_redraw()
			context.workspace.status_text_set(get_id("qc_anim_loading", True).format(self.total - len(self.todo), self.total))

		if self.todo:
			return {'RUNNING_MODAL'}
		wm.event_timer_remove(self.timer)
		wm.progress_end()
		context.workspace.status_text_set(None)
		return self._finish(context, arm) if arm else {'CANCELLED'}

class SMD_OT_QcAnimUnload(_QcAnimOperator, bpy.types.Operator):
	bl_idname = "smd.qc_anim_unload"
	bl_label = get_id("qc_anim_unload")
	bl_description = get_id("qc_anim_unload_tip")
	bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

	index : IntProperty(min=0)

	def execute(self, context):
		arm = get_armature(context)
		if self.index >= len(arm.vs.qc_anims):
			return {'CANCELLED'}
		unload(arm, self.index)
		State.update_scene(context.scene)
		return {'FINISHED'}

class SMD_OT_QcAnimsRescan(bpy.types.Operator):
	bl_idname = "smd.qc_anims_rescan"
	bl_label = get_id("qc_anims_rescan")
	bl_description = get_id("qc_anims_rescan_tip")
	bl_options = {'REGISTER', 'UNDO'}

	@classmethod
	def poll(cls, context):
		arm = get_armature(context)
		return bool(arm and arm.vs.qc_path)

	def execute(self, context):
		arm = get_armature(context)
		vs = arm.vs
		path = bpy.path.abspath(vs.qc_path)
		if not os.path.exists(path):
			self.report({'ERROR'}, get_id("importer_err_qci", True).format(path))
			return {'CANCELLED'}

		from .import_smd import AnimLoader
		loader = AnimLoader(arm, rotMode=vs.qc_rot_mode or 'XYZ', upAxis=vs.qc_up_axis or None)
		loader.includeSearchPath = vs.qc_include_search_path
		before = len(vs.qc_anims)
		with State.suspend_updates():
			loader.readQC(path, False, True, False, vs.qc_rot_mode or 'XYZ', outer_qc=True, harvest_only=True)
		qc_compose.clear_cache()
		for message in loader.log_warnings + loader.log_errors:
			self.report({'WARNING'}, message)
		self.report({'INFO'}, get_id("qc_anims_rescanned", True).format(len(vs.qc_anims) - before, len(vs.qc_anims)))
		return {'FINISHED'}

classes = (
	SMD_UL_QcAnims,
	SMD_PT_QcAnims,
	SMD_OT_QcAnimLoad,
	SMD_OT_QcAnimLoadFiltered,
	SMD_OT_QcAnimUnload,
	SMD_OT_QcAnimsRescan,
)
