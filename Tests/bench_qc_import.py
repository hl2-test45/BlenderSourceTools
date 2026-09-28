"""Time a QC import and report what it created.

    blender -b --factory-startup --python Tests/bench_qc_import.py -- --qc <file.qc> [options]

Options:
    --qc PATH          QC to import.
    --subset N         Instead of importing --qc as-is, build a temporary QC holding its
                       $bodygroup/$lod blocks plus the first N $animation lines of --anim-qc.
    --anim-qc PATH     Animation QC to take the --subset animations from.
    --lazy 0|1         Value for the importer's lazyAnims property (ignored if the property doesn't exist).
    --load-all         After importing, load every sequence the list shows, i.e. all but deltas (lazy mode only).
    --profile          Print the cProfile top 25 by cumulative time.
"""
import os, sys, time, tempfile, argparse, cProfile, pstats

import bpy

parser = argparse.ArgumentParser()
parser.add_argument("--addon-root", help="Folder containing io_scene_valvesource (default: this repository)")
parser.add_argument("--qc", required=True)
parser.add_argument("--pre-qc", help="QC imported (untimed) before --qc, e.g. the model an animation QC belongs to")
parser.add_argument("--subset", type=int, default=0)
parser.add_argument("--anim-qc")
parser.add_argument("--lazy", type=int, default=None)
parser.add_argument("--load-all", action="store_true")
parser.add_argument("--profile", action="store_true")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])

sys.path.insert(0, os.path.realpath(args.addon_root or os.path.join(os.path.dirname(__file__), "..")))

def read_blocks(path, keywords):
	"""Yields the text of each top-level block whose first line starts with one of keywords."""
	with open(path) as f:
		lines = f.readlines()
	i = 0
	while i < len(lines):
		stripped = lines[i].strip()
		if any(stripped.lower().startswith(k) for k in keywords):
			block = [lines[i]]
			depth = lines[i].count("{") - lines[i].count("}")
			i += 1
			if depth == 0 and i < len(lines) and lines[i].strip().startswith("{"):
				depth = 0 # "{" on the next line
				while i < len(lines):
					block.append(lines[i])
					depth += lines[i].count("{") - lines[i].count("}")
					i += 1
					if depth <= 0: break
			else:
				while depth > 0 and i < len(lines):
					block.append(lines[i])
					depth += lines[i].count("{") - lines[i].count("}")
					i += 1
			yield "".join(block)
		else:
			i += 1

def build_subset(model_qc, anim_qc, count):
	model_dir = os.path.dirname(os.path.realpath(model_qc))
	anim_dir = os.path.dirname(os.path.realpath(anim_qc))
	out = ['$pushd "{}\\"\n'.format(model_dir)]
	out.extend(read_blocks(model_qc, ["$bodygroup", "$lod"]))
	out.append('$popd\n$pushd "{}\\"\n'.format(anim_dir))
	# spread the picks over the whole file; the first entries are all 1-frame Crowbar "@...corrective" helpers
	anims = [a for a in read_blocks(anim_qc, ["$animation"]) if not a.split('"')[1].startswith("@")]
	step = max(1, len(anims) // count)
	out.extend(anims[::step][:count])
	out.append("$popd\n")
	path = os.path.join(tempfile.gettempdir(), "bst_bench_subset_{}.qc".format(count))
	with open(path, "w") as f:
		f.write("".join(out))
	return path

bpy.ops.preferences.addon_enable(module="io_scene_valvesource")
import io_scene_valvesource
print("Add-on loaded from", os.path.dirname(io_scene_valvesource.__file__))

qc = build_subset(args.qc, args.anim_qc, args.subset) if args.subset else args.qc

import_props = {p.identifier for p in bpy.ops.import_scene.smd.get_rna_type().properties}
kwargs = {"filepath": qc}
if args.lazy is not None and "lazyAnims" in import_props:
	kwargs["lazyAnims"] = bool(args.lazy)

if args.pre_qc:
	pre_kwargs = {"filepath": args.pre_qc}
	if "lazyAnims" in import_props: pre_kwargs["lazyAnims"] = True
	bpy.ops.import_scene.smd(**pre_kwargs)

profiler = cProfile.Profile() if args.profile else None
start = time.perf_counter()
if profiler: profiler.enable()
result = bpy.ops.import_scene.smd(**kwargs)
if profiler: profiler.disable()
import_time = time.perf_counter() - start

load_time = None
if args.load_all:
	arm = next((ob for ob in bpy.context.scene.objects if ob.type == 'ARMATURE' and len(ob.vs.qc_anims)), None)
	if arm:
		bpy.context.view_layer.objects.active = arm
		start = time.perf_counter()
		if profiler: profiler.enable()
		from io_scene_valvesource import anim_list
		anim_list.load_items(bpy.context, arm, [i for i in anim_list.filtered_indices(arm.vs, loaded_only=False) if anim_list.can_load(arm.vs.qc_anims[i])])
		if profiler: profiler.disable()
		load_time = time.perf_counter() - start

def count_keys():
	keys = curves = slots = 0
	for action in bpy.data.actions:
		if hasattr(action, "layers") and action.layers:
			slots += len(action.slots)
			for layer in action.layers:
				for strip in layer.strips:
					for bag in strip.channelbags:
						for fc in bag.fcurves:
							curves += 1
							keys += len(fc.keyframe_points)
		else:
			for fc in getattr(action, "fcurves", []):
				curves += 1
				keys += len(fc.keyframe_points)
	return keys, curves, slots

keys, curves, slots = count_keys()
sequences = [item for ob in bpy.data.objects if hasattr(ob.vs, "qc_anims") for item in ob.vs.qc_anims]
deltas = sum(1 for item in sequences if item.is_delta)
print("\n==== BENCH ====")
print("qc          :", qc)
print("result      :", result, kwargs)
print("import time : {:.2f} s".format(import_time))
if load_time is not None: print("load-all    : {:.2f} s".format(load_time))
print("actions     :", len(bpy.data.actions), " slots:", slots, " fcurves:", curves, " keyframes:", keys)
print("sequences   : {} ({} playable, {} delta, {} hidden, {} blends)".format(len(sequences), len(sequences) - deltas, deltas,
	sum(1 for item in sequences if item.is_hidden), sum(1 for item in sequences if len(item.components) > 1)))
print("objects     :", len(bpy.data.objects), " meshes:", len(bpy.data.meshes))
excluded = []
def walk(lc):
	for child in lc.children:
		if child.exclude: excluded.append(child.name)
		walk(child)
walk(bpy.context.view_layer.layer_collection)
print("excluded    :", excluded)
if profiler:
	pstats.Stats(profiler).sort_stats("cumulative").print_stats(25)
