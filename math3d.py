"""3D vector helpers and segment clipping utilities."""

import math

Vec3 = tuple[float, float, float]
Interval = tuple[float, float]


def sub(a: Vec3, b: Vec3) -> Vec3:
	"""Subtract two 3D points."""
	return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def add(a: Vec3, b: Vec3) -> Vec3:
	"""Add two 3D points."""
	return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def scale(v: Vec3, s: float) -> Vec3:
	"""Scale a 3D vector."""
	return v[0] * s, v[1] * s, v[2] * s


def lerp(a: Vec3, b: Vec3, t: float) -> Vec3:
	"""Linearly interpolate between two 3D points."""
	return (
		a[0] + (b[0] - a[0]) * t,
		a[1] + (b[1] - a[1]) * t,
		a[2] + (b[2] - a[2]) * t,
	)


def length_sq(v: Vec3) -> float:
	"""Squared length of a 3D vector."""
	return v[0] * v[0] + v[1] * v[1] + v[2] * v[2]


def length(v: Vec3) -> float:
	"""Length of a 3D vector."""
	return math.sqrt(length_sq(v))


def vertex_key(point: Vec3) -> tuple[int, int, int]:
	"""Quantize a world position so shared endpoints reuse one cache entry.

	Integer scaling avoids ``round()`` — that showed up hot in render profiles.
	"""
	return (
		int(point[0] * 10000.0 + (0.5 if point[0] >= 0.0 else -0.5)),
		int(point[1] * 10000.0 + (0.5 if point[1] >= 0.0 else -0.5)),
		int(point[2] * 10000.0 + (0.5 if point[2] >= 0.0 else -0.5)),
	)


def intersect_intervals(a: Interval, b: Interval) -> Interval | None:
	"""Return the overlap of two parametric intervals on [0, 1]."""
	t0 = max(a[0], b[0])
	t1 = min(a[1], b[1])
	if t0 > t1 + 1e-9:
		return None
	return t0, t1


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
	"""Merge touching or overlapping parametric intervals."""
	if not intervals:
		return []

	sorted_intervals = sorted(intervals, key=lambda item: item[0])
	merged: list[Interval] = [sorted_intervals[0]]
	for t0, t1 in sorted_intervals[1:]:
		last_t0, last_t1 = merged[-1]
		if t0 <= last_t1 + 1e-6:
			merged[-1] = (last_t0, max(last_t1, t1))
		else:
			merged.append((t0, t1))
	return merged


def collect_axis_plane_breakpoints(start: Vec3, end: Vec3, coord: float, axis: int) -> list[float]:
	"""Return parametric t values where a segment crosses a plane."""
	delta = end[axis] - start[axis]
	if abs(delta) < 1e-10:
		return []

	t = (coord - start[axis]) / delta
	if 0.0 < t < 1.0:
		return [t]
	return []


def clip_segment_to_sphere_interval(
	start: Vec3,
	end: Vec3,
	center: Vec3,
	radius: float,
) -> Interval | None:
	"""Return the parametric sub-interval inside a sphere, or None if fully outside."""
	if radius <= 0.0:
		return None

	rel_start = sub(start, center)
	rel_end = sub(end, center)
	delta = sub(rel_end, rel_start)

	a_coeff = length_sq(delta)
	radius_sq = radius * radius

	if a_coeff < 1e-10:
		if length_sq(rel_start) <= radius_sq:
			return 0.0, 1.0
		return None

	b_coeff = 2.0 * (
		rel_start[0] * delta[0]
		+ rel_start[1] * delta[1]
		+ rel_start[2] * delta[2]
	)
	c_coeff = length_sq(rel_start) - radius_sq

	discriminant = b_coeff * b_coeff - 4.0 * a_coeff * c_coeff
	if discriminant < 0.0:
		if c_coeff <= 0.0:
			return 0.0, 1.0
		return None

	sqrt_disc = math.sqrt(discriminant)
	inv_2a = 1.0 / (2.0 * a_coeff)
	t0 = (-b_coeff - sqrt_disc) * inv_2a
	t1 = (-b_coeff + sqrt_disc) * inv_2a

	t_enter = max(0.0, min(t0, t1))
	t_exit = min(1.0, max(t0, t1))
	if t_enter > t_exit:
		return None

	return t_enter, t_exit


def clip_segment_to_sphere(
	start: Vec3,
	end: Vec3,
	center: Vec3,
	radius: float,
) -> tuple[Vec3, Vec3] | None:
	"""Return the sub-segment inside a sphere, or None if fully outside."""
	interval = clip_segment_to_sphere_interval(start, end, center, radius)
	if interval is None:
		return None

	t_enter, t_exit = interval
	return lerp(start, end, t_enter), lerp(start, end, t_exit)


def clip_segment_to_min_z(
	start: Vec3,
	end: Vec3,
	min_z: float,
) -> tuple[Vec3, Vec3] | None:
	"""Clip a camera-space segment to points in front of the near plane."""
	z0 = start[2]
	z1 = end[2]

	if z0 < min_z and z1 < min_z:
		return None
	if z0 >= min_z and z1 >= min_z:
		return start, end

	delta_z = z1 - z0
	if abs(delta_z) < 1e-10:
		return None

	t = (min_z - z0) / delta_z
	t = max(0.0, min(1.0, t))
	clip_point = lerp(start, end, t)

	if z0 < min_z:
		return clip_point, end
	return start, clip_point


Edge = tuple[Vec3, Vec3]


def subdivide_edge(start: Vec3, end: Vec3, divisions: int) -> list[Edge]:
	"""Split one edge into ``divisions`` equal colinear segments."""
	if divisions <= 1:
		return [(start, end)]

	segments: list[Edge] = []
	for index in range(divisions):
		t0 = index / divisions
		t1 = (index + 1) / divisions
		segments.append((lerp(start, end, t0), lerp(start, end, t1)))
	return segments


def subdivide_edges(edges: list[Edge], divisions: int) -> list[Edge]:
	"""Subdivide every edge in a wireframe edge list."""
	if divisions <= 1:
		return list(edges)

	dense: list[Edge] = []
	for start, end in edges:
		dense.extend(subdivide_edge(start, end, divisions))
	return dense
