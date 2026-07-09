"""Specular early reflections — multi-order image-source + AABB face bounces.

Physics (Allen & Berkley 1979; Kuttruff; Vorländer):
- Specular bounce: r = i − 2(i·n)n (axis-aligned → negate axis component)
- Energy per bounce: R_E = (1−α)(1−s); pressure R_p = √R_E
- Path delay: excess length / c; gain relative to direct via R_p / path_length
- Occlusion is emergent: invalid if any leg fails segment_line_of_sight

Hybrid (Savioja & Svensson / Wayverb pattern):
- Room BoundaryPlanes: recursive ISM up to MAX_ROOM_ORDER
- Interior AABBs: finite-face specular order-1 + pruned order-2
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from acoustics.directivity import arrival_dir_from_path, launch_band_energy_gains, launch_dir_from_path
from acoustics.materials import (
	SPEED_OF_SOUND_MPS,
	AcousticMaterial,
	apply_air_absorption_energy,
	apply_diffuse_surface_reflection,
	apply_surface_reflection,
	energy_to_lpf_hz,
	energy_to_pressure_gain,
	resolve_material,
)
from acoustics.types import ReflectionTap
from math3d import Vec3, length, lerp, sub
from occlusion import Aabb, BoundaryPlane, segment_line_of_sight

# Specular orders for the six room planes (concrete bunker early field).
_MAX_ROOM_ORDER = 3
# AABB finite-face specular: order-1 + pruned order-2.
_MAX_AABB_ORDER = 2
# ODEON-style transition order: ≤ TO keep ISM+scatter taps; > TO → reverb_send.
TRANSITION_ORDER = 2
# Keep the loudest early taps for Spherion multi-tap playback.
# Match Spherion ``kMaxReflectionTaps`` (dense bunker early field).
_MAX_TAPS = 8
# Flutter echo: max periodic repeats of the strongest parallel-wall pair.
_FLUTTER_MAX_REPEATS = 4
_FLUTTER_DECAY = 0.55
# Dedupe taps that land in the same delay bin (seconds).
_DELAY_BIN_S = 0.0015
# Coplanar AABB face vs room shell — skip to avoid double-counting.
_COPLANAR_EPS = 0.02
# Minimum path length to avoid singularities.
_MIN_PATH_M = 0.25
# Relative pressure gain floor (vs 1/r_direct reference).
_MIN_REL_GAIN = 1.0e-4
# Order-2 AABB CPU caps: keep best first hits, then best second-leg candidates.
_AABB_O1_KEEP = 8
_AABB_O2_FACE_CAP = 24
_AABB_O2_ENERGY_FLOOR = 2.0e-4


@dataclass
class _Face:
	"""One outward face of an AABB (finite rectangle)."""

	aabb_index: int
	axis: int
	coord: float
	normal: float
	u_axis: int
	v_axis: int
	u_min: float
	u_max: float
	v_min: float
	v_max: float


def _mirror_across_plane(point: Vec3, plane: BoundaryPlane) -> Vec3:
	"""Reflect a point across an axis-aligned boundary plane."""
	mirrored = list(point)
	mirrored[plane.axis] = 2.0 * plane.coord - mirrored[plane.axis]
	return (mirrored[0], mirrored[1], mirrored[2])


def _segment_plane_intersection(
	origin: Vec3,
	target: Vec3,
	plane: BoundaryPlane,
) -> Vec3 | None:
	"""Return where segment origin→target crosses ``plane``, or None."""
	axis = plane.axis
	o = origin[axis]
	t = target[axis]
	if abs(t - o) < 1e-9:
		return None
	u = (plane.coord - o) / (t - o)
	if u <= 1e-5 or u >= 1.0 - 1e-5:
		return None
	return lerp(origin, target, u)


def _aabb_faces(bounds: Aabb, aabb_index: int) -> list[_Face]:
	"""Enumerate six outward faces of an AABB."""
	box_min, box_max = bounds
	return [
		_Face(aabb_index, 0, box_min[0], -1.0, 1, 2, box_min[1], box_max[1], box_min[2], box_max[2]),
		_Face(aabb_index, 0, box_max[0], +1.0, 1, 2, box_min[1], box_max[1], box_min[2], box_max[2]),
		_Face(aabb_index, 1, box_min[1], -1.0, 0, 2, box_min[0], box_max[0], box_min[2], box_max[2]),
		_Face(aabb_index, 1, box_max[1], +1.0, 0, 2, box_min[0], box_max[0], box_min[2], box_max[2]),
		_Face(aabb_index, 2, box_min[2], -1.0, 0, 1, box_min[0], box_max[0], box_min[1], box_max[1]),
		_Face(aabb_index, 2, box_max[2], +1.0, 0, 1, box_min[0], box_max[0], box_min[1], box_max[1]),
	]


def _face_coplanar_with_boundary(face: _Face, boundaries: list[BoundaryPlane]) -> bool:
	"""True when this AABB face coincides with the room shell."""
	for plane in boundaries:
		if plane.axis == face.axis and abs(plane.coord - face.coord) <= _COPLANAR_EPS:
			return True
	return False


def _exterior_side(point: Vec3, face: _Face) -> bool:
	"""True when ``point`` lies on the outward side of ``face``."""
	return (point[face.axis] - face.coord) * face.normal > 1e-5


def _on_face_rect(hit: Vec3, face: _Face) -> bool:
	"""True when ``hit`` lies inside the finite face rectangle."""
	u = hit[face.u_axis]
	v = hit[face.v_axis]
	return (
		face.u_min - 1e-4 <= u <= face.u_max + 1e-4
		and face.v_min - 1e-4 <= v <= face.v_max + 1e-4
	)


def _mirror_across_face(point: Vec3, face: _Face) -> Vec3:
	"""Reflect a point across an AABB face plane."""
	mirrored = list(point)
	mirrored[face.axis] = 2.0 * face.coord - mirrored[face.axis]
	return (mirrored[0], mirrored[1], mirrored[2])


def _hit_from_image_face(eye: Vec3, source: Vec3, face: _Face) -> Vec3 | None:
	"""Image-source hit on a finite AABB face, or None if invalid."""
	if not _exterior_side(eye, face) or not _exterior_side(source, face):
		return None
	mirror_s = _mirror_across_face(source, face)
	d = sub(mirror_s, eye)
	da = d[face.axis]
	if abs(da) < 1e-9:
		return None
	t = (face.coord - eye[face.axis]) / da
	if t <= 1e-5 or t >= 1.0 - 1e-5:
		return None
	hit = lerp(eye, mirror_s, t)
	if not _on_face_rect(hit, face):
		return None
	return hit


def _validate_path(
	points: list[Vec3],
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
) -> bool:
	"""Every leg of the polyline must have clear geometric visibility."""
	for a, b in zip(points, points[1:]):
		if not segment_line_of_sight(a, b, occluders, boundaries):
			return False
	return True


def _path_length(points: list[Vec3]) -> float:
	"""Sum of segment lengths along a bounce polyline."""
	total = 0.0
	for a, b in zip(points, points[1:]):
		total += length(sub(b, a))
	return total


def _face_outward_normal(face: _Face) -> Vec3:
	"""Outward unit normal for an AABB face."""
	n = [0.0, 0.0, 0.0]
	n[face.axis] = face.normal
	return (n[0], n[1], n[2])


def _plane_outward_normal(plane: BoundaryPlane, hit: Vec3, interior_point: Vec3) -> Vec3:
	"""Outward normal for a room boundary plane (points into room from hit)."""
	n = [0.0, 0.0, 0.0]
	# Pick sign so normal points toward interior reference (listener or source).
	trial = list(hit)
	trial[plane.axis] = hit[plane.axis] + 0.1
	if (trial[plane.axis] - interior_point[plane.axis]) * (plane.coord - interior_point[plane.axis]) > 0:
		n[plane.axis] = 1.0 if plane.coord > interior_point[plane.axis] else -1.0
	else:
		n[plane.axis] = -1.0 if plane.coord > interior_point[plane.axis] else 1.0
	return (n[0], n[1], n[2])


def _lambert_diffuse_weight(normal: Vec3, to_listener: Vec3) -> float:
	"""Lambert cosine factor for diffuse reception (0 = grazing, 1 = normal)."""
	mag_n = math.sqrt(normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2)
	mag_l = math.sqrt(to_listener[0] ** 2 + to_listener[1] ** 2 + to_listener[2] ** 2)
	if mag_n < 1e-9 or mag_l < 1e-9:
		return 0.0
	cos_theta = (
		normal[0] * to_listener[0] + normal[1] * to_listener[1] + normal[2] * to_listener[2]
	) / (mag_n * mag_l)
	return max(0.0, cos_theta)


def _tap_from_path(
	points: list[Vec3],
	materials: list[AcousticMaterial],
	direct_len: float,
	bounce_order: int,
	*,
	is_diffuse: bool = False,
	surface_normal: Vec3 | None = None,
	source_directivity: str = "omni",
	source_axis: Vec3 = (0.0, 0.0, -1.0),
	speaker_radius_m: float = 0.0,
) -> ReflectionTap | None:
	"""Convert a validated bounce polyline into a pressure tap relative to direct.

	Launch directivity (pattern × baffled-piston H(θ,ka)) weights energy leaving
	the source along the first path leg — reflections are not omnidirectional.
	"""
	path_len = _path_length(points)
	if path_len < _MIN_PATH_M or direct_len < 1e-4:
		return None

	# Unit energy at source, shaped by speaker facing + size, then surface bounces.
	launch = launch_dir_from_path(points)
	energy = launch_band_energy_gains(
		source_directivity,
		source_axis,
		launch,
		speaker_radius_m,
	)
	for material in materials:
		if is_diffuse:
			energy = apply_diffuse_surface_reflection(energy, material)
		else:
			energy = apply_surface_reflection(energy, material)
	energy = apply_air_absorption_energy(energy, path_len)

	# Lambert reception weight for diffuse taps (ODEON early scattering model).
	if is_diffuse and surface_normal is not None and len(points) >= 2:
		eye = points[0]
		last_hit = points[-2]
		to_listener = sub(eye, last_hit)
		lambert = _lambert_diffuse_weight(surface_normal, to_listener)
		if lambert < 0.03:
			return None
		energy = [e * lambert for e in energy]

	rel_pressure = (direct_len / path_len) * energy_to_pressure_gain(energy)
	if rel_pressure < _MIN_REL_GAIN:
		return None

	delay_s = max(0.0, (path_len - direct_len) / SPEED_OF_SOUND_MPS)
	band_tuple = tuple(max(0.0, e) for e in energy)
	return ReflectionTap(
		gain=min(1.2, rel_pressure),
		delay_s=min(0.20, delay_s),
		lpf_hz=energy_to_lpf_hz(energy),
		bounce_order=bounce_order,
		arrival_dir=arrival_dir_from_path(points),
		band_energy=band_tuple,  # type: ignore[arg-type]
		is_diffuse=is_diffuse,
	)


def _diffuse_tap_from_specular_path(
	points: list[Vec3],
	materials: list[AcousticMaterial],
	direct_len: float,
	bounce_order: int,
	surface_normal: Vec3,
	*,
	source_directivity: str = "omni",
	source_axis: Vec3 = (0.0, 0.0, -1.0),
	speaker_radius_m: float = 0.0,
) -> ReflectionTap | None:
	"""Companion diffuse tap: specular on prior bounces, Lambert scatter on last (ODEON ESM)."""
	if not materials:
		return None
	path_len = _path_length(points)
	if path_len < _MIN_PATH_M or direct_len < 1e-4:
		return None

	# Same launch weighting as specular — scatter does not invent omni energy.
	launch = launch_dir_from_path(points)
	energy = launch_band_energy_gains(
		source_directivity,
		source_axis,
		launch,
		speaker_radius_m,
	)
	for material in materials[:-1]:
		energy = apply_surface_reflection(energy, material)
	energy = apply_diffuse_surface_reflection(energy, materials[-1])
	energy = apply_air_absorption_energy(energy, path_len)

	eye = points[0]
	last_hit = points[-2]
	to_listener = sub(eye, last_hit)
	lambert = _lambert_diffuse_weight(surface_normal, to_listener)
	if lambert < 0.03:
		return None
	energy = [e * lambert for e in energy]

	rel_pressure = (direct_len / path_len) * energy_to_pressure_gain(energy)
	if rel_pressure < _MIN_REL_GAIN * 0.5:
		return None

	delay_s = max(0.0, (path_len - direct_len) / SPEED_OF_SOUND_MPS)
	band_tuple = tuple(max(0.0, e) for e in energy)
	return ReflectionTap(
		gain=min(1.0, rel_pressure),
		delay_s=min(0.20, delay_s),
		lpf_hz=energy_to_lpf_hz(energy),
		bounce_order=bounce_order,
		arrival_dir=arrival_dir_from_path(points),
		band_energy=band_tuple,  # type: ignore[arg-type]
		is_diffuse=True,
	)


def _material_at_plane_hit(
	hit: Vec3,
	occluders: list[Aabb],
	occluder_materials: list[str] | None,
	default: AcousticMaterial,
) -> AcousticMaterial:
	"""Prefer material of a coplanar AABB near the hit; else room default."""
	for index, bounds in enumerate(occluders):
		box_min, box_max = bounds
		if (
			box_min[0] - 0.08 <= hit[0] <= box_max[0] + 0.08
			and box_min[1] - 0.08 <= hit[1] <= box_max[1] + 0.08
			and box_min[2] - 0.08 <= hit[2] <= box_max[2] + 0.08
		):
			name = occluder_materials[index] if occluder_materials and index < len(occluder_materials) else None
			return resolve_material(bounds, name)
	return default


def _reconstruct_room_hits(
	eye: Vec3,
	source: Vec3,
	plane_sequence: list[BoundaryPlane],
) -> list[Vec3] | None:
	"""Recover bounce points for an image-source plane sequence (Borish backtrace).

	``plane_sequence[0]`` is the first bounce surface; the last entry is the last bounce.
	Returns ``[eye, hit1, …, hitN, source]`` or None if a plane miss occurs.
	"""
	if not plane_sequence:
		return [eye, source]

	# images[0] = real source; images[k] = source mirrored across plane_sequence[0..k-1]
	images: list[Vec3] = [source]
	pos = source
	for plane in plane_sequence:
		pos = _mirror_across_plane(pos, plane)
		images.append(pos)

	# Backtrace from listener toward the highest-order image; hits appear last-bounce-first.
	hits_rev: list[Vec3] = []
	cursor = eye
	for order in range(len(plane_sequence), 0, -1):
		plane = plane_sequence[order - 1]
		hit = _segment_plane_intersection(cursor, images[order], plane)
		if hit is None:
			return None
		hits_rev.append(hit)
		cursor = hit

	hits = list(reversed(hits_rev))
	return [eye, *hits, source]


def _trace_room_ism(
	eye: Vec3,
	source: Vec3,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
	*,
	occluder_materials: list[str] | None,
	direct_len: float,
	default_material: AcousticMaterial,
	max_order: int,
	source_directivity: str = "omni",
	source_axis: Vec3 = (0.0, 0.0, -1.0),
	speaker_radius_m: float = 0.0,
) -> list[ReflectionTap]:
	"""Recursive image-source on room BoundaryPlanes (Allen–Berkley / Borish)."""
	taps: list[ReflectionTap] = []
	# Each node: (image_position, plane_sequence)
	frontier: list[tuple[Vec3, list[BoundaryPlane]]] = [(source, [])]
	# Shared launch kwargs for specular + diffuse companions.
	launch_kw = dict(
		source_directivity=source_directivity,
		source_axis=source_axis,
		speaker_radius_m=speaker_radius_m,
	)

	for _order in range(1, max_order + 1):
		next_frontier: list[tuple[Vec3, list[BoundaryPlane]]] = []
		for image_pos, seq in frontier:
			last = seq[-1] if seq else None
			for plane in boundaries:
				# No immediate back-reflection on the same plane.
				if last is not None and last.axis == plane.axis and abs(last.coord - plane.coord) < 1e-9:
					continue
				new_image = _mirror_across_plane(image_pos, plane)
				new_seq = seq + [plane]
				next_frontier.append((new_image, new_seq))

				points = _reconstruct_room_hits(eye, source, new_seq)
				if points is None or len(points) < 3:
					continue
				if not _validate_path(points, occluders, boundaries):
					continue

				mats: list[AcousticMaterial] = []
				for hit in points[1:-1]:
					mats.append(
						_material_at_plane_hit(hit, occluders, occluder_materials, default_material)
					)
				tap = _tap_from_path(
					points, mats, direct_len, bounce_order=len(new_seq), **launch_kw
				)
				if tap is not None:
					taps.append(tap)
					if mats and points[-2] is not None:
						normal = _plane_outward_normal(new_seq[-1], points[-2], eye)
						diff = _diffuse_tap_from_specular_path(
							points, mats, direct_len, len(new_seq), normal, **launch_kw
						)
						if diff is not None and diff.gain >= _MIN_REL_GAIN * 0.5:
							taps.append(diff)
		frontier = next_frontier

	return taps


@dataclass
class _AabbOrder1Hit:
	"""Validated first-order AABB face hit used to seed order-2 searches."""

	face: _Face
	hit: Vec3
	material: AcousticMaterial
	tap: ReflectionTap


def _trace_aabb_order1(
	eye: Vec3,
	source: Vec3,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
	*,
	occluder_materials: list[str] | None,
	direct_len: float,
	source_directivity: str = "omni",
	source_axis: Vec3 = (0.0, 0.0, -1.0),
	speaker_radius_m: float = 0.0,
) -> tuple[list[ReflectionTap], list[_AabbOrder1Hit]]:
	"""First-order specular off finite AABB faces (not coplanar with room shell)."""
	taps: list[ReflectionTap] = []
	hits: list[_AabbOrder1Hit] = []
	launch_kw = dict(
		source_directivity=source_directivity,
		source_axis=source_axis,
		speaker_radius_m=speaker_radius_m,
	)
	for index, bounds in enumerate(occluders):
		name = occluder_materials[index] if occluder_materials and index < len(occluder_materials) else None
		material = resolve_material(bounds, name)
		for face in _aabb_faces(bounds, index):
			if _face_coplanar_with_boundary(face, boundaries):
				continue
			hit = _hit_from_image_face(eye, source, face)
			if hit is None:
				continue
			points = [eye, hit, source]
			if not _validate_path(points, occluders, boundaries):
				continue
			tap = _tap_from_path(points, [material], direct_len, bounce_order=1, **launch_kw)
			if tap is not None:
				taps.append(tap)
				normal = _face_outward_normal(face)
				diff = _diffuse_tap_from_specular_path(
					points, [material], direct_len, 1, normal, **launch_kw
				)
				if diff is not None and diff.gain >= _MIN_REL_GAIN * 0.5:
					taps.append(diff)
				hits.append(_AabbOrder1Hit(face=face, hit=hit, material=material, tap=tap))
	return taps, hits


def _same_face(a: _Face, b: _Face) -> bool:
	"""True when two face descriptors refer to the same AABB plane patch."""
	return (
		a.aabb_index == b.aabb_index
		and a.axis == b.axis
		and abs(a.coord - b.coord) < 1e-9
		and a.normal == b.normal
	)


def _hit_second_bounce_face(
	first_hit: Vec3,
	source: Vec3,
	face: _Face,
) -> Vec3 | None:
	"""Image-source hit on ``face`` for the leg first_hit → source."""
	if not _exterior_side(first_hit, face) or not _exterior_side(source, face):
		return None
	mirror_s = _mirror_across_face(source, face)
	d = sub(mirror_s, first_hit)
	da = d[face.axis]
	if abs(da) < 1e-9:
		return None
	t = (face.coord - first_hit[face.axis]) / da
	if t <= 1e-5 or t >= 1.0 - 1e-5:
		return None
	hit = lerp(first_hit, mirror_s, t)
	if not _on_face_rect(hit, face):
		return None
	return hit


def _trace_aabb_order2(
	eye: Vec3,
	source: Vec3,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
	order1_hits: list[_AabbOrder1Hit],
	*,
	occluder_materials: list[str] | None,
	direct_len: float,
	default_material: AcousticMaterial,
	source_directivity: str = "omni",
	source_axis: Vec3 = (0.0, 0.0, -1.0),
	speaker_radius_m: float = 0.0,
) -> list[ReflectionTap]:
	"""Second-order specular: AABB→AABB or AABB→room-plane after a valid order-1 hit.

	CPU budget: keep loudest order-1 seeds, prune second faces, early energy terminate.
	Every leg is validated with ``segment_line_of_sight``.
	"""
	if not order1_hits or not occluders:
		return []

	seeds = sorted(order1_hits, key=lambda h: -h.tap.gain)[:_AABB_O1_KEEP]
	# Precompute candidate second faces (AABB + room planes as infinite faces).
	second_faces: list[tuple[_Face | None, BoundaryPlane | None, AcousticMaterial]] = []
	for index, bounds in enumerate(occluders):
		name = occluder_materials[index] if occluder_materials and index < len(occluder_materials) else None
		material = resolve_material(bounds, name)
		for face in _aabb_faces(bounds, index):
			if _face_coplanar_with_boundary(face, boundaries):
				continue
			second_faces.append((face, None, material))
	for plane in boundaries:
		# Represent room plane as a synthetic infinite face for image bounce.
		second_faces.append((None, plane, default_material))

	launch_kw = dict(
		source_directivity=source_directivity,
		source_axis=source_axis,
		speaker_radius_m=speaker_radius_m,
	)
	taps: list[ReflectionTap] = []
	face_tests = 0
	for seed in seeds:
		if seed.tap.gain < _AABB_O2_ENERGY_FLOOR:
			continue
		for face2, plane2, mat2 in second_faces:
			if face_tests >= _AABB_O2_FACE_CAP * len(seeds):
				return taps
			face_tests += 1

			if face2 is not None:
				if _same_face(seed.face, face2):
					continue
				hit2 = _hit_second_bounce_face(seed.hit, source, face2)
				if hit2 is None:
					continue
				points = [eye, seed.hit, hit2, source]
				mats = [seed.material, mat2]
			else:
				assert plane2 is not None
				# Mirror source across room plane; intersect seed.hit → image.
				mirror_s = _mirror_across_plane(source, plane2)
				hit2 = _segment_plane_intersection(seed.hit, mirror_s, plane2)
				if hit2 is None:
					continue
				points = [eye, seed.hit, hit2, source]
				mats = [seed.material, mat2]

			if not _validate_path(points, occluders, boundaries):
				continue
			tap = _tap_from_path(points, mats, direct_len, bounce_order=2, **launch_kw)
			if tap is not None and tap.gain >= _AABB_O2_ENERGY_FLOOR:
				taps.append(tap)
				if mats:
					if face2 is not None:
						normal = _face_outward_normal(face2)
					else:
						assert plane2 is not None
						normal = _plane_outward_normal(plane2, points[-2], eye)
					diff = _diffuse_tap_from_specular_path(
						points, mats, direct_len, 2, normal, **launch_kw
					)
					if diff is not None and diff.gain >= _AABB_O2_ENERGY_FLOOR * 0.5:
						taps.append(diff)
	return taps


def _merge_taps(taps: list[ReflectionTap], max_taps: int = _MAX_TAPS) -> tuple[ReflectionTap, ...]:
	"""Keep loudest taps; reserve at least one slot for diffuse scatter energy."""
	if not taps:
		return ()

	def _merge_list(ordered: list[ReflectionTap], limit: int) -> list[ReflectionTap]:
		merged: list[ReflectionTap] = []
		for tap in ordered:
			duplicate = False
			for index, existing in enumerate(merged):
				if abs(existing.delay_s - tap.delay_s) <= _DELAY_BIN_S:
					g = math.sqrt(existing.gain * existing.gain + tap.gain * tap.gain)
					merged[index] = ReflectionTap(
						gain=min(1.2, g),
						delay_s=min(existing.delay_s, tap.delay_s),
						lpf_hz=max(existing.lpf_hz, tap.lpf_hz),
						bounce_order=min(existing.bounce_order, tap.bounce_order),
						arrival_dir=existing.arrival_dir,
						band_energy=tuple(
							math.sqrt(a * a + b * b)
							for a, b in zip(existing.band_energy, tap.band_energy)
						),  # type: ignore[arg-type]
						is_diffuse=existing.is_diffuse or tap.is_diffuse,
					)
					duplicate = True
					break
			if not duplicate:
				merged.append(tap)
			if len(merged) >= limit:
				break
		return merged

	# ODEON TO: keep only taps at or below transition order for early field.
	filtered = [t for t in taps if t.bounce_order <= TRANSITION_ORDER]
	if not filtered:
		filtered = list(taps)

	specular = sorted([t for t in filtered if not t.is_diffuse], key=lambda t: -t.gain)
	diffuse = sorted([t for t in filtered if t.is_diffuse], key=lambda t: -t.gain)
	# Reserve up to two slots for diffuse when scatter paths exist (ISO 17497).
	spec_limit = max(1, max_taps - (2 if diffuse else 0))
	merged = _merge_list(specular, spec_limit)
	if diffuse:
		merged.extend(_merge_list(diffuse, min(2, max_taps - len(merged))))
	merged.sort(key=lambda t: t.delay_s)
	return tuple(merged[:max_taps])


def find_parallel_boundary_pairs(
	boundaries: list[BoundaryPlane],
) -> list[tuple[BoundaryPlane, BoundaryPlane, float]]:
	"""Find opposing parallel BoundaryPlanes; return (plane_a, plane_b, separation_m)."""
	by_axis: dict[int, list[BoundaryPlane]] = {0: [], 1: [], 2: []}
	for plane in boundaries:
		if 0 <= plane.axis <= 2:
			by_axis[plane.axis].append(plane)
	pairs: list[tuple[BoundaryPlane, BoundaryPlane, float]] = []
	for axis, planes in by_axis.items():
		if len(planes) < 2:
			continue
		coords = sorted(planes, key=lambda p: p.coord)
		# Pair outermost opposing walls on this axis.
		lo, hi = coords[0], coords[-1]
		sep = abs(hi.coord - lo.coord)
		if sep >= 0.5:
			pairs.append((lo, hi, sep))
	return pairs


def flutter_echo_period_s(separation_m: float) -> float:
	"""Flutter period for parallel walls: round-trip 2L/c."""
	return (2.0 * max(separation_m, 0.5)) / SPEED_OF_SOUND_MPS


def emit_flutter_taps(
	boundaries: list[BoundaryPlane],
	*,
	seed_gain: float,
	seed_delay_s: float,
	seed_lpf_hz: float,
	arrival_dir: Vec3 = (0.0, 0.0, 1.0),
	max_repeats: int = _FLUTTER_MAX_REPEATS,
) -> list[ReflectionTap]:
	"""Emit a decaying periodic tap train for the strongest parallel wall pair.

	Period = 2L/c. Caps at ``max_repeats`` (3–4). Used when parallel hard walls
	create audible flutter (Kuttruff).
	"""
	pairs = find_parallel_boundary_pairs(boundaries)
	if not pairs or seed_gain < 0.02:
		return []
	# Strongest flutter: largest separation among pairs (longest period, clearer).
	_lo, _hi, sep = max(pairs, key=lambda p: p[2])
	period = flutter_echo_period_s(sep)
	taps: list[ReflectionTap] = []
	gain = seed_gain * _FLUTTER_DECAY
	delay = seed_delay_s + period
	for _ in range(max_repeats):
		if gain < _MIN_REL_GAIN:
			break
		taps.append(
			ReflectionTap(
				gain=min(1.0, gain),
				delay_s=min(0.35, delay),
				lpf_hz=max(400.0, seed_lpf_hz * 0.92),
				bounce_order=TRANSITION_ORDER,
				arrival_dir=arrival_dir,
				band_energy=(0.8, 0.7, 0.5, 0.35, 0.2, 0.1),
				is_diffuse=False,
			)
		)
		gain *= _FLUTTER_DECAY
		delay += period
		seed_lpf_hz *= 0.92
	return taps


def trace_specular_reflections(
	eye: Vec3,
	source: Vec3,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
	*,
	occluder_materials: list[str] | None = None,
	max_room_order: int = _MAX_ROOM_ORDER,
	include_flutter: bool = True,
	source_directivity: str = "omni",
	source_axis: Vec3 = (0.0, 0.0, -1.0),
	speaker_radius_m: float = 0.0,
) -> tuple[ReflectionTap, ...]:
	"""Return validated early specular taps (room ISM + AABB order-1).

	Occlusion is a side effect: blocked legs never produce a tap.
	Orders above ``TRANSITION_ORDER`` are dropped here (energy folded in tracer).
	Launch directivity (facing × piston size) weights energy leaving the source.
	"""
	direct_len = length(sub(source, eye))
	if direct_len < _MIN_PATH_M:
		return ()

	default_material = resolve_material(((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)), "concrete")
	launch_kw = dict(
		source_directivity=source_directivity,
		source_axis=source_axis,
		speaker_radius_m=speaker_radius_m,
	)
	taps: list[ReflectionTap] = []
	taps.extend(
		_trace_room_ism(
			eye,
			source,
			occluders,
			boundaries,
			occluder_materials=occluder_materials,
			direct_len=direct_len,
			default_material=default_material,
			max_order=max_room_order,
			**launch_kw,
		)
	)
	if _MAX_AABB_ORDER >= 1:
		aabb_taps, aabb_hits = _trace_aabb_order1(
			eye,
			source,
			occluders,
			boundaries,
			occluder_materials=occluder_materials,
			direct_len=direct_len,
			**launch_kw,
		)
		taps.extend(aabb_taps)
		if _MAX_AABB_ORDER >= 2:
			taps.extend(
				_trace_aabb_order2(
					eye,
					source,
					occluders,
					boundaries,
					aabb_hits,
					occluder_materials=occluder_materials,
					direct_len=direct_len,
					default_material=default_material,
					**launch_kw,
				)
			)

	# Flutter: periodic train from strongest parallel opposing walls.
	if include_flutter and taps:
		best = max(taps, key=lambda t: t.gain)
		taps.extend(
			emit_flutter_taps(
				boundaries,
				seed_gain=best.gain * 0.45,
				seed_delay_s=best.delay_s,
				seed_lpf_hz=best.lpf_hz,
				arrival_dir=best.arrival_dir,
			)
		)

	return _merge_taps(taps)


def trace_first_reflection(
	eye: Vec3,
	source: Vec3,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
	*,
	occluder_materials: list[str] | None = None,
) -> tuple[float, float, float]:
	"""Compatibility: return the loudest tap as (gain, delay_s, lpf_hz)."""
	taps = trace_specular_reflections(
		eye,
		source,
		occluders,
		boundaries,
		occluder_materials=occluder_materials,
		max_room_order=1,
	)
	if not taps:
		return 0.0, 0.0, 18_000.0
	best = max(taps, key=lambda t: t.gain)
	return best.gain, best.delay_s, best.lpf_hz
