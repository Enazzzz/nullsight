"""Geometric acoustic path tracing — path-native occlusion via multi-bounce rays.

Occlusion is not a fake gain multiplier. A blocked direct ray simply contributes
zero direct energy; audibility comes from transmission, diffraction, and/or
specular reflections that still reach the listener (Allen–Berkley ISM + AABB faces).

Diffraction is a separate delayed path (UTD/BTM); it must not duck the dry source.
"""

from __future__ import annotations

import math

from acoustics.directivity import directivity_pressure_gain
from acoustics.diffraction import estimate_diffraction_paths
from acoustics.materials import aabb_thickness_m, resolve_material, transmission_through_solid
from acoustics.modes import modal_lf_boost, modes_from_boundaries
from acoustics.portals_graph import portal_graph_gain_for_path
from acoustics.radiosity import radiosity_reverb_send_boost, transition_order_fold
from acoustics.reflection import TRANSITION_ORDER, trace_specular_reflections
from acoustics.reverb_zones import reverb_send_for_path, reverb_zone_from_geometry
from acoustics.structure import structure_borne_for_source
from acoustics.types import AcousticLink, AcousticSource, ReflectionTap
from math3d import Vec3, length, sub
from occlusion import Aabb, BoundaryPlane, segment_blocking_occluders, segment_line_of_sight

# Clear direct path.
_CLEAR_GAIN = 1.0
_CLEAR_LPF_HZ = 18_000.0
_BLOCKED_GAIN = 0.0
_BLOCKED_LPF_HZ = 400.0
# Grazing / partial when a thin solid clips the ray (still a direct-path variant).
_PARTIAL_GAIN_MIN = 0.15
_PARTIAL_GAIN_MAX = 0.40
_PARTIAL_LPF_MIN = 450.0
_PARTIAL_LPF_MAX = 1_600.0
_NEAR_SURFACE_BOOST_M = 1.8


def _lerp(a: float, b: float, t: float) -> float:
	"""Linear interpolation between two scalars."""
	return a + (b - a) * t


def _point_aabb_surface_distance(point: Vec3, bounds: Aabb) -> float:
	"""Approximate distance from a point to the nearest face of an AABB."""
	box_min, box_max = bounds
	dx = 0.0
	if point[0] < box_min[0]:
		dx = box_min[0] - point[0]
	elif point[0] > box_max[0]:
		dx = point[0] - box_max[0]
	dy = 0.0
	if point[1] < box_min[1]:
		dy = box_min[1] - point[1]
	elif point[1] > box_max[1]:
		dy = point[1] - box_max[1]
	dz = 0.0
	if point[2] < box_min[2]:
		dz = box_min[2] - point[2]
	elif point[2] > box_max[2]:
		dz = point[2] - box_max[2]
	return math.sqrt(dx * dx + dy * dy + dz * dz)


def _material_for_bounds(
	bounds: Aabb,
	occluders: list[Aabb],
	materials: list[str] | None,
) -> str | None:
	"""Find the authored material name for ``bounds`` by identity match."""
	if not materials:
		return None
	for index, candidate in enumerate(occluders):
		if candidate is bounds or candidate == bounds:
			if index < len(materials):
				return materials[index]
	return None


def _partial_obstruction(
	eye: Vec3,
	source: Vec3,
	blockers: list[Aabb],
	occluders: list[Aabb],
	materials: list[str] | None,
) -> tuple[float, float] | None:
	"""Return (gain, lpf_hz) for thin-slab grazing occlusion, or None when not partial."""
	if not blockers:
		return None

	path_len = length(sub(source, eye))
	if path_len < 1e-4:
		return None

	primary = blockers[0]
	thickness = aabb_thickness_m(primary)
	# Only thin partitions (drywall / glass / door leaf) graze; bunker walls are solid.
	if thickness > 0.12:
		return None

	overlap = segment_blocking_occluders(eye, source, [primary])
	if not overlap:
		return None

	penetration_ratio = min(1.0, thickness / max(path_len, 0.5))
	gain = _lerp(_PARTIAL_GAIN_MAX, _PARTIAL_GAIN_MIN, penetration_ratio)
	lpf = _lerp(_PARTIAL_LPF_MAX, _PARTIAL_LPF_MIN, penetration_ratio)

	surface_dist = _point_aabb_surface_distance(source, primary)
	if surface_dist < _NEAR_SURFACE_BOOST_M:
		prox = 1.0 - surface_dist / _NEAR_SURFACE_BOOST_M
		gain = min(0.45, gain + prox * 0.12)
		lpf = _lerp(lpf, 1_100.0, prox * 0.35)

	mat_name = _material_for_bounds(primary, occluders, materials)
	material = resolve_material(primary, mat_name)
	double_leaf = mat_name == "metal_door" or material.name == "metal_door"
	layer_gain, layer_lpf = transmission_through_solid(
		material,
		thickness,
		double_leaf=double_leaf,
	)
	if layer_gain <= 0.0:
		return None
	gain = min(0.5, gain * (0.5 + 0.5 * min(1.0, layer_gain * 8.0)))
	lpf = min(lpf, max(layer_lpf, _PARTIAL_LPF_MIN))
	return gain, lpf


def _wall_transmission(
	blockers: list[Aabb],
	occluders: list[Aabb],
	materials: list[str] | None,
	*,
	portal_gain: float,
	portal_lpf_hz: float,
) -> tuple[float, float]:
	"""Accumulate mass-law / MAM transmission through solids along the ray.

	Thick concrete returns ~0 (physically correct); cracked-door portal can still bleed.
	Door panels tagged ``metal_door`` use double-leaf MAM STL.
	"""
	gain = 1.0
	lpf = _CLEAR_LPF_HZ
	for bounds in blockers:
		mat_name = _material_for_bounds(bounds, occluders, materials)
		material = resolve_material(bounds, mat_name)
		double_leaf = mat_name == "metal_door" or material.name == "metal_door"
		layer_gain, layer_lpf = transmission_through_solid(
			material,
			aabb_thickness_m(bounds),
			double_leaf=double_leaf,
		)
		gain *= layer_gain
		lpf = min(lpf, layer_lpf)
		if gain < 1e-6:
			gain = 0.0
			break

	# Portal / cracked door: opening leakage, not mass-law through the panel.
	if portal_gain > 0.0:
		gain = max(gain, portal_gain * 0.85)
		lpf = max(lpf, portal_lpf_hz * 0.65)

	return gain, lpf


def _loudest_tap(taps: tuple[ReflectionTap, ...]) -> tuple[float, float, float]:
	"""Return (gain, delay_s, lpf_hz) for the strongest specular tap."""
	if not taps:
		return 0.0, 0.0, 18_000.0
	best = max(taps, key=lambda t: t.gain)
	return best.gain, best.delay_s, best.lpf_hz


def _min_reflection_delay(taps: tuple[ReflectionTap, ...]) -> float:
	"""Earliest early-reflection delay for late-reverb predelay."""
	if not taps:
		return 0.0
	return min(t.delay_s for t in taps if t.delay_s > 0.0) if any(t.delay_s > 0.0 for t in taps) else 0.0


def _path_mode_label(
	*,
	direct_gain: float,
	trans_gain: float,
	diff_gain: float,
	refl_gain: float,
) -> str:
	"""Pick a diagnostic label from which path carries the most energy."""
	if direct_gain >= 0.9:
		return "clear"
	if direct_gain > 0.05:
		return "partial"
	candidates = (
		("transmission", trans_gain),
		("diffraction", diff_gain),
		("reflected", refl_gain),
	)
	best_name, best_g = max(candidates, key=lambda item: item[1])
	if best_g < 0.02:
		return "blocked"
	return best_name


def _zone_for_trace(
	room_name: str,
	boundaries: list[BoundaryPlane],
	*,
	door_open_frac: float = 0.0,
	occluders: list[Aabb] | None = None,
	occluder_materials: list[str] | None = None,
	predelay_s: float = 0.0,
	shell_material: str = "concrete",
):
	"""Resolve geometry-driven reverb zone (Sabine/Eyring + door coupling)."""
	return reverb_zone_from_geometry(
		room_name,
		boundaries,
		material_name=shell_material,
		door_open_frac=door_open_frac,
		occluders=occluders,
		occluder_materials=occluder_materials,
		predelay_s=predelay_s,
	)


def _finalize_link(
	*,
	source_id: int,
	direct_gain: float,
	direct_lpf_hz: float,
	trans_gain: float = 0.0,
	trans_lpf: float = 400.0,
	taps: tuple[ReflectionTap, ...],
	diff_gain: float = 0.0,
	diff_delay: float = 0.0,
	diff_lpf: float = 1_200.0,
	diff_extra: tuple[ReflectionTap, ...] = (),
	path_mode: str,
	zone,
	room_name: str,
	portal_graph_gain: float = 0.0,
	lf_boost: float = 1.0,
	structure_gain: float = 0.0,
	structure_lpf: float = 450.0,
	structure_delay: float = 0.0,
) -> AcousticLink:
	"""Build AcousticLink with radiosity fold, modal LF boost, and predelay.

	Modal wiring: ``lf_boost`` from ``modal_lf_boost`` multiplies reverb_send
	when modes cluster below Schroeder (mild LF energy into the late bus).
	Structure-borne is a parallel dull path (table radio / future footsteps).
	"""
	# ODEON TO: fold orders above TRANSITION_ORDER into reverb_send.
	kept, send = transition_order_fold(
		taps,
		transition_order=TRANSITION_ORDER,
		direct_gain=direct_gain,
		base_send=reverb_send_for_path(
			room_name,
			direct_gain=max(direct_gain, trans_gain, diff_gain, structure_gain, 0.0),
			reflection_gain=_loudest_tap(taps)[0],
			path_mode=path_mode,
			zone=zone,
		),
	)
	# Residual diffuse after early taps → late bus (energy conservation).
	send = radiosity_reverb_send_boost(kept, direct_gain=direct_gain, base_send=send)
	send = min(0.65, send * lf_boost)

	refl_gain, refl_delay, refl_lpf = _loudest_tap(kept)
	predelay = max(zone.predelay_s, _min_reflection_delay(kept))

	return AcousticLink(
		source_id=source_id,
		direct_gain=direct_gain,
		direct_lpf_hz=direct_lpf_hz,
		transmission_gain=trans_gain,
		transmission_lpf_hz=trans_lpf,
		reflection_gain=refl_gain,
		reflection_delay_s=refl_delay,
		reflection_lpf_hz=refl_lpf,
		reflection_taps=kept,
		diffraction_gain=diff_gain,
		diffraction_delay_s=diff_delay,
		diffraction_lpf_hz=diff_lpf,
		path_mode=path_mode,
		reverb_send=send,
		rt60_s=zone.rt60_s if zone.enabled else 0.0,
		reverb_predelay_s=min(0.25, predelay),
		portal_graph_gain=portal_graph_gain,
		structure_borne_gain=structure_gain,
		structure_borne_lpf_hz=structure_lpf,
		structure_borne_delay_s=structure_delay,
		diffraction_extra=diff_extra,
	)


def trace_acoustic_path(
	eye: Vec3,
	source: AcousticSource,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
	*,
	occluder_materials: list[str] | None = None,
	portal_gain: float = 0.0,
	portal_lpf_hz: float = 18_000.0,
	room_name: str = "",
	door_open_frac: float = 0.0,
	shell_material: str = "concrete",
	floor_material: str | None = None,
	structure_coupled: bool = True,
) -> AcousticLink:
	"""Trace all geometric paths; occlusion = absence of a clear direct ray."""
	# Specular cluster first — needed for predelay into zone.
	target = source.position
	taps_raw = (
		trace_specular_reflections(
			eye,
			target,
			occluders,
			boundaries,
			occluder_materials=occluder_materials,
			source_directivity=source.directivity,
			source_axis=source.directivity_axis,
			speaker_radius_m=source.speaker_radius_m,
		)
		if source.enabled
		else ()
	)
	predelay_hint = _min_reflection_delay(taps_raw)
	zone = _zone_for_trace(
		room_name,
		boundaries,
		door_open_frac=door_open_frac,
		occluders=occluders,
		occluder_materials=occluder_materials,
		predelay_s=predelay_hint,
		shell_material=shell_material,
	)
	rt60 = zone.rt60_s if zone.enabled else 0.0

	# Modal LF boost: cluster below Schroeder → mild reverb_send bump (documented).
	modes, _f_s = modes_from_boundaries(boundaries, rt60_s=rt60) if rt60 > 0.0 else ([], 0.0)
	lf_boost = modal_lf_boost(modes)

	# Structure-borne: table radio / low sources couple into the floor.
	floor_mat = floor_material if floor_material is not None else shell_material
	struct_g, struct_lpf, struct_delay = structure_borne_for_source(
		target,
		eye,
		floor_material=floor_mat,
		coupled=structure_coupled and source.enabled,
	)

	if not source.enabled:
		return AcousticLink(
			source_id=source.source_id,
			direct_gain=_BLOCKED_GAIN,
			direct_lpf_hz=_BLOCKED_LPF_HZ,
			path_mode="blocked",
			rt60_s=rt60,
			reverb_predelay_s=zone.predelay_s,
		)

	dir_gain = directivity_pressure_gain(
		source.directivity,
		source.directivity_axis,
		target,
		eye,
		speaker_radius_m=source.speaker_radius_m,
	)
	refl_gain, _refl_delay, _refl_lpf = _loudest_tap(taps_raw)

	clear = segment_line_of_sight(eye, target, occluders, boundaries)
	graph_gain = portal_graph_gain_for_path(
		eye,
		target,
		door_open_frac=door_open_frac,
		los_blocked=not clear,
	)

	if clear:
		path_mode = "clear"
		direct_gain = _CLEAR_GAIN * dir_gain
		return _finalize_link(
			source_id=source.source_id,
			direct_gain=direct_gain,
			direct_lpf_hz=_CLEAR_LPF_HZ,
			taps=taps_raw,
			path_mode=path_mode,
			zone=zone,
			room_name=room_name,
			lf_boost=lf_boost,
			structure_gain=struct_g,
			structure_lpf=struct_lpf,
			structure_delay=struct_delay,
		)

	blockers = segment_blocking_occluders(eye, target, occluders)
	partial = _partial_obstruction(eye, target, blockers, occluders, occluder_materials)
	if partial is not None:
		partial_gain, partial_lpf = partial
		partial_gain = min(0.5, partial_gain * dir_gain)
		path_mode = "partial"
		return _finalize_link(
			source_id=source.source_id,
			direct_gain=partial_gain,
			direct_lpf_hz=partial_lpf,
			taps=taps_raw,
			path_mode=path_mode,
			zone=zone,
			room_name=room_name,
			portal_graph_gain=graph_gain,
			lf_boost=lf_boost,
			structure_gain=struct_g,
			structure_lpf=struct_lpf,
			structure_delay=struct_delay,
		)

	# Direct ray blocked — no fake occlusion duck. Alternate paths only.
	trans_gain, trans_lpf = _wall_transmission(
		blockers,
		occluders,
		occluder_materials,
		portal_gain=portal_gain,
		portal_lpf_hz=portal_lpf_hz,
	)
	# Portal graph boost when door blocks cross-cell LOS.
	if graph_gain > 0.0:
		trans_gain = max(trans_gain, graph_gain * 0.9)
		trans_lpf = max(trans_lpf, 400.0 + graph_gain * 4_000.0)

	diff_paths = estimate_diffraction_paths(
		eye,
		target,
		blockers,
		occluders=occluders,
		boundaries=boundaries,
		portal_gain=max(portal_gain, graph_gain),
		direct_blocked=True,
	)
	diff_gain = diff_paths[0].gain if diff_paths else 0.0
	diff_delay = diff_paths[0].delay_s if diff_paths else 0.0
	diff_lpf = diff_paths[0].lpf_hz if diff_paths else 1_200.0
	# Secondary edges → extra early taps (soft multi-shadow).
	diff_extra: tuple[ReflectionTap, ...] = ()
	if len(diff_paths) > 1:
		extras: list[ReflectionTap] = []
		for path in diff_paths[1:]:
			extras.append(
				ReflectionTap(
					gain=path.gain,
					delay_s=path.delay_s,
					lpf_hz=path.lpf_hz,
					bounce_order=0,
					arrival_dir=(0.0, 0.0, 1.0),
					is_diffuse=True,
				)
			)
		diff_extra = tuple(extras)

	path_mode = _path_mode_label(
		direct_gain=_BLOCKED_GAIN,
		trans_gain=trans_gain,
		diff_gain=diff_gain,
		refl_gain=refl_gain,
	)
	return _finalize_link(
		source_id=source.source_id,
		direct_gain=_BLOCKED_GAIN,
		direct_lpf_hz=_BLOCKED_LPF_HZ,
		trans_gain=trans_gain,
		trans_lpf=trans_lpf,
		taps=taps_raw,
		diff_gain=diff_gain,
		diff_delay=diff_delay,
		diff_lpf=diff_lpf,
		diff_extra=diff_extra,
		path_mode=path_mode,
		zone=zone,
		room_name=room_name,
		portal_graph_gain=graph_gain,
		lf_boost=lf_boost,
		structure_gain=struct_g,
		structure_lpf=struct_lpf,
		structure_delay=struct_delay,
	)


def trace_direct_path(
	eye: Vec3,
	source: AcousticSource,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
) -> AcousticLink:
	"""Compatibility wrapper — delegates to :func:`trace_acoustic_path`."""
	return trace_acoustic_path(eye, source, occluders, boundaries)
