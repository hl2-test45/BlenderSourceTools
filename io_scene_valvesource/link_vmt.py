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

# Gives imported materials their Source textures. The importer only creates
# materials *named* after the Source material; this resolves each name through
#
#     materials/<$cdmaterials>/<name>.vmt  ->  $basetexture
#                                          ->  <png root>/materials/<basetexture>.png
#
# and wires an Image Texture into the Principled BSDF. The PNGs are expected to
# be a VTFEdit "Convert Folder" run over <game root>/materials, written with the
# same layout under <png root>.

import bpy, os, re
from bpy.props import BoolProperty
from .utils import *

IMAGE_EXTS = (".png", ".tga")

def split_cdmaterials(value):
	"""Split the semicolon-separated $cdmaterials scene property into a clean list."""
	return [c.replace("\\","/").strip("/ ") for c in value.split(";") if c.strip("/ \\")]

def add_cdmaterial(scene, path):
	"""Record a $cdmaterials path from a QC on the scene, without duplicates."""
	path = path.replace("\\","/").strip("/ ")
	if not path:
		return
	current = split_cdmaterials(scene.vs.vmt_cdmaterials)
	if path.lower() not in [c.lower() for c in current]:
		current.append(path)
		scene.vs.vmt_cdmaterials = ";".join(current)

#
# Path helpers
#

def _norm(rel):
	return rel.replace("\\", "/").strip("/")

def _resolve_ci(root, rel):
	"""Return the on-disk path of root/rel, matching each component case-insensitively."""
	rel = _norm(rel)
	direct = os.path.join(root, *rel.split("/"))
	if os.path.exists(direct):
		return direct
	cur = root
	for part in rel.split("/"):
		if not os.path.isdir(cur):
			return None
		match = next((e for e in os.listdir(cur) if e.lower() == part.lower()), None)
		if match is None:
			return None
		cur = os.path.join(cur, match)
	return cur

def _find(root, rel):
	"""Locate <rel> (a path relative to materials/) under a root the user may have pointed at
	the folder containing materials/, at materials/ itself, or at materials/models."""
	rel = _norm(rel)
	if rel.lower().startswith("materials/"):
		rel = rel[len("materials/"):]
	candidates = ["materials/" + rel, rel]
	if rel.lower().startswith("models/"):
		candidates.append(rel[len("models/"):])
	for c in candidates:
		p = _resolve_ci(root, c)
		if p:
			return p
	return None

def _strip_ext(rel, exts):
	low = rel.lower()
	for ext in exts:
		if low.endswith(ext):
			return rel[:-len(ext)]
	return rel

#
# Minimal Valve KeyValues reader (enough for VMTs)
#

def _tokenize(text):
	i, n = 0, len(text)
	while i < n:
		c = text[i]
		if c.isspace():
			i += 1
		elif text.startswith("//", i):
			j = text.find("\n", i)
			i = n if j < 0 else j
		elif c == '"':
			j = text.find('"', i + 1)
			if j < 0:
				j = n
			yield text[i + 1:j]
			i = j + 1
		elif c in "{}":
			yield c
			i += 1
		elif c == "[":  # [$X360] / [!$WIN32] conditionals: ignore
			j = text.find("]", i)
			i = n if j < 0 else j + 1
		else:
			j = i
			while j < n and not text[j].isspace() and text[j] not in '{}"[':
				j += 1
			yield text[i:j]
			i = j

def _parse_block(tokens):
	d = {}
	for tok in tokens:
		if tok == "}":
			break
		key = tok.lower()
		try:
			val = next(tokens)
		except StopIteration:
			break
		if val == "{":
			d[key] = _parse_block(tokens)
		elif val == "}":
			break
		else:
			d[key] = val
	return d

def _flatten_conditionals(params):
	# ">=DX90" { ... } style blocks: hoist their contents to the parent
	for key in [k for k, v in params.items() if isinstance(v, dict) and k[:1] in ("<", ">", "=")]:
		params.update(params.pop(key))
	return params

def _truthy(v):
	return str(v).strip() not in ("", "0", "0.0")

#
# Operator
#

class SMD_OT_LinkVmtTextures(bpy.types.Operator, Logger):
	bl_idname = "smd.link_vmt_textures"
	bl_label = get_id("link_vmt_title")
	bl_description = get_id("link_vmt_tip")
	bl_options = {'REGISTER', 'UNDO'}

	selected_only : BoolProperty(name=get_id("link_vmt_selected"), description=get_id("link_vmt_selected_tip"), default=False)
	overwrite : BoolProperty(name=get_id("link_vmt_overwrite"), description=get_id("link_vmt_overwrite_tip"), default=False)
	link_bumpmap : BoolProperty(name=get_id("link_vmt_bumpmap"), description=get_id("link_vmt_bumpmap_tip"), default=True)

	def __init__(self, *args, **kwargs):
		bpy.types.Operator.__init__(self, *args, **kwargs)
		Logger.__init__(self)

	@classmethod
	def poll(cls, context):
		return len(bpy.data.materials) > 0

	def invoke(self, context, event):
		if not context.scene.vs.vmt_game_root and context.scene.vs.game_path:
			context.scene.vs.vmt_game_root = context.scene.vs.game_path
		return context.window_manager.invoke_props_dialog(self, width=500)

	def draw(self, context):
		l = self.layout
		l.use_property_split = True
		vs = context.scene.vs

		row = l.row()
		row.alert = not os.path.isdir(self.game_root(context))
		row.prop(vs, "vmt_game_root")
		row = l.row()
		row.alert = not os.path.isdir(self.png_root(context))
		row.prop(vs, "vmt_png_root")
		l.prop(vs, "vmt_cdmaterials")

		l.separator()
		l.prop(self, "selected_only")
		l.prop(self, "overwrite")
		l.prop(self, "link_bumpmap")

	def game_root(self, context):
		return os.path.normpath(bpy.path.abspath(context.scene.vs.vmt_game_root or context.scene.vs.game_path or "."))

	def png_root(self, context):
		vs = context.scene.vs
		return os.path.normpath(bpy.path.abspath(vs.vmt_png_root)) if vs.vmt_png_root else self.game_root(context)

	def execute(self, context):
		self.game_root_path = self.game_root(context)
		self.png_root_path = self.png_root(context)
		self.cdmaterials = split_cdmaterials(context.scene.vs.vmt_cdmaterials)
		self._vmt_index = None

		for path in (self.game_root_path, self.png_root_path):
			if not os.path.isdir(path):
				self.report({'ERROR'}, get_id("link_vmt_err_folder", True).format(path))
				return {'CANCELLED'}

		linked = skipped = 0
		for mat in self.target_materials(context):
			if getattr(mat, "is_grease_pencil", False):
				continue
			if mat.node_tree and not self.overwrite and any(n.type == "TEX_IMAGE" for n in mat.node_tree.nodes):
				skipped += 1
				continue
			vmt = self.find_vmt(mat.name)
			if vmt is None:
				self.warning(get_id("link_vmt_warn_novmt", True).format(mat.name))
				continue
			shader, params = self.load_vmt(vmt)
			if not params.get("$basetexture"):
				self.warning(get_id("link_vmt_warn_nobase", True).format(mat.name, os.path.relpath(vmt, self.game_root_path), shader))
				continue
			img = self.build_material(mat, params)
			if img is None:
				self.warning(get_id("link_vmt_warn_nopng", True).format(mat.name, "materials/" + _norm(params["$basetexture"]) + ".vtf"))
				continue
			print("- {} <- {}".format(mat.name, img))
			linked += 1

		self.errorReport(get_id("link_vmt_report", True).format(linked, skipped))
		return {'FINISHED'}

	def target_materials(self, context):
		if self.selected_only:
			mats = []
			for ob in context.selected_objects:
				for slot in getattr(ob, "material_slots", []):
					if slot.material and slot.material not in mats:
						mats.append(slot.material)
			return mats
		return list(bpy.data.materials)

	# ---- VMT lookup ----

	def vmt_by_name(self, name):
		"""Fallback for empty $cdmaterials: index every VMT under materials/models once."""
		if self._vmt_index is None:
			self._vmt_index = {}
			base = _find(self.game_root_path, "models")
			if base:
				for dirpath, _, files in os.walk(base):
					for f in files:
						if f.lower().endswith(".vmt"):
							self._vmt_index.setdefault(f[:-4].lower(), os.path.join(dirpath, f))
		return self._vmt_index.get(name.lower())

	def find_vmt(self, mat_name):
		name = re.sub(r"\.\d{3}$", "", mat_name)  # Blender's .001 suffix
		name = _strip_ext(name, (".vmt",))
		if "/" in name or "\\" in name:  # the importer kept the path (Crowbar "remove path" was off)
			return _find(self.game_root_path, _norm(name) + ".vmt")
		for cd in self.cdmaterials:
			p = _find(self.game_root_path, _norm(cd) + "/" + name + ".vmt")
			if p:
				return p
		if not self.cdmaterials:
			return self.vmt_by_name(name)
		return None

	def find_image(self, texture):
		"""Map a VMT texture value (relative to materials/, no extension) to a PNG/TGA on disk."""
		rel = _strip_ext(_norm(texture), (".vtf",))
		for root in (self.png_root_path, self.game_root_path):
			for ext in IMAGE_EXTS:
				p = _find(root, rel + ext)
				if p:
					return p
		return None

	def load_vmt(self, path, depth=0):
		"""Return (shader, params) with 'patch' VMTs resolved onto the material they include."""
		with open(path, "r", encoding="utf-8", errors="replace") as f:
			kv = _parse_block(_tokenize(f.read()))
		if not kv:
			return None, {}
		shader, params = next(iter(kv.items()))
		if not isinstance(params, dict):
			return shader, {}
		params = _flatten_conditionals(params)
		if shader == "patch" and depth < 4:
			inc = params.get("include")
			base_shader, merged = (None, {})
			if inc:
				inc_path = _find(self.game_root_path, inc)
				if inc_path:
					base_shader, merged = self.load_vmt(inc_path, depth + 1)
			merged = dict(merged)
			for k in ("insert", "replace"):
				if isinstance(params.get(k), dict):
					merged.update(params[k])
			return base_shader or "patch", merged
		return shader, params

	# ---- Node building ----

	@staticmethod
	def _principled(mat):
		nt = mat.node_tree
		bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
		if bsdf is None:
			out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
			if out is None:
				out = nt.nodes.new("ShaderNodeOutputMaterial")
			bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
			nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
		return bsdf

	@staticmethod
	def _image_node(mat, path, label, location, non_color=False):
		img = bpy.data.images.load(path, check_existing=True)
		if non_color:
			img.colorspace_settings.name = "Non-Color"
		node = mat.node_tree.nodes.new("ShaderNodeTexImage")
		node.image = img
		node.label = label
		node.location = location
		return node

	@staticmethod
	def _set_alpha_mode(mat, blended):
		# Blender 4.2+ (EEVEE Next) vs. legacy EEVEE
		if hasattr(mat, "surface_render_method"):
			mat.surface_render_method = "BLENDED" if blended else "DITHERED"
		elif hasattr(mat, "blend_method"):
			mat.blend_method = "BLEND" if blended else "CLIP"

	def build_material(self, mat, params):
		if mat.node_tree is None:
			mat.use_nodes = True
		nt = mat.node_tree
		bsdf = self._principled(mat)

		base = self.find_image(params["$basetexture"])
		if base is None:
			return None

		if self.overwrite:
			for n in [n for n in nt.nodes if n.type in ("TEX_IMAGE", "NORMAL_MAP", "MATH")]:
				nt.nodes.remove(n)

		tex = self._image_node(mat, base, "$basetexture", (bsdf.location.x - 400, bsdf.location.y))
		nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

		translucent = _truthy(params.get("$translucent", 0)) or _truthy(params.get("$additive", 0))
		alphatest = _truthy(params.get("$alphatest", 0))
		if translucent:
			nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
			self._set_alpha_mode(mat, blended=True)
		elif alphatest:
			clip = nt.nodes.new("ShaderNodeMath")
			clip.operation = "GREATER_THAN"
			clip.inputs[1].default_value = float(params.get("$alphatestreference", 0.5))
			clip.location = (bsdf.location.x - 200, bsdf.location.y - 250)
			nt.links.new(tex.outputs["Alpha"], clip.inputs[0])
			nt.links.new(clip.outputs["Value"], bsdf.inputs["Alpha"])
			self._set_alpha_mode(mat, blended=False)

		if self.link_bumpmap and params.get("$bumpmap"):
			bump = self.find_image(params["$bumpmap"])
			if bump:
				bnode = self._image_node(mat, bump, "$bumpmap", (bsdf.location.x - 600, bsdf.location.y - 350), non_color=True)
				nmap = nt.nodes.new("ShaderNodeNormalMap")
				nmap.location = (bsdf.location.x - 250, bsdf.location.y - 350)
				nt.links.new(bnode.outputs["Color"], nmap.inputs["Color"])
				nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
		return base
