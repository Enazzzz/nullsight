"""Line-of-sight clipping against solids and room boundary planes."""

from dataclasses import dataclass, field

from math3d import (
	Interval,
	Vec3,
	collect_axis_plane_breakpoints,
	lerp,
	merge_intervals,
	sub,
	vertex_key,
)

Aabb = tuple[Vec3, Vec3]
SURFACE_EPSILON = 0.001
VISIBILITY_EPSILON = 0.0005


@dataclass(frozen=True)
class BoundaryPlane:
	"""Axis-aligned room boundary used to block sight through walls."""

	axis: int
	coord: float


@dataclass
class VisibilityCache:
	"""Per-frame visibility cache so shared vertices stay stable."""

	eye: Vec3
	occluders: list[Aabb]
	boundaries: list[BoundaryPlane]
	_values: dict[tuple[float, float, float], bool] = field(default_factory=dict)

	def is_visible(self, point: Vec3) -> bool:
		"""Return cached line-of-sight visibility for a world point."""
		key = vertex_key(point)
		if key not in self._values:
			self._values[key] = _point_visible(self.eye, point, self.occluders, self.boundaries)
		return self._values[key]


def _point_on_boundary(point: Vec3, plane: BoundaryPlane) -> bool:
	"""Return True when a point lies on a wall/floor/ceiling surface."""
	return abs(point[plane.axis] - plane.coord) <= SURFACE_EPSILON


def _boundary_blocks(eye: Vec3, point: Vec3, plane: BoundaryPlane) -> bool:
	"""Return True when a boundary lies between the eye and the point."""
	if _point_on_boundary(point, plane):
		return False

	eye_coord = eye[plane.axis]
	point_coord = point[plane.axis]
	return (eye_coord - plane.coord) * (point_coord - plane.coord) < 0.0


def _point_on_aabb_surface(point: Vec3, bounds: Aabb) -> bool:
	"""Return True when a point lies on an axis-aligned box hull."""
	box_min, box_max = bounds
	for axis in range(3):
		on_face = (
			abs(point[axis] - box_min[axis]) <= SURFACE_EPSILON
			or abs(point[axis] - box_max[axis]) <= SURFACE_EPSILON
		)
		if not on_face:
			continue

		in_span = True
		for other in range(3):
			if other == axis:
				continue
			if point[other] < box_min[other] - SURFACE_EPSILON or point[other] > box_max[other] + SURFACE_EPSILON:
				in_span = False
				break
		if in_span:
			return True
	return False


def _point_on_aabb_face_toward_eye(eye: Vec3, point: Vec3, bounds: Aabb) -> bool:
	"""Return True when a hull point lies on the near side of a solid facing the eye."""
	if not _point_on_aabb_surface(point, bounds):
		return False

	box_min, box_max = bounds
	for axis in range(3):
		if abs(point[axis] - box_min[axis]) <= SURFACE_EPSILON and eye[axis] < point[axis] - SURFACE_EPSILON:
			return True
		if abs(point[axis] - box_max[axis]) <= SURFACE_EPSILON and eye[axis] > point[axis] + SURFACE_EPSILON:
			return True
	return False


def _point_strictly_inside_aabb(point: Vec3, bounds: Aabb) -> bool:
	"""Return True when a point lies in a box interior, not on its hull."""
	box_min, box_max = bounds
	return (
		box_min[0] + SURFACE_EPSILON < point[0] < box_max[0] - SURFACE_EPSILON
		and box_min[1] + SURFACE_EPSILON < point[1] < box_max[1] - SURFACE_EPSILON
		and box_min[2] + SURFACE_EPSILON < point[2] < box_max[2] - SURFACE_EPSILON
	)


def _segment_aabb_overlap_param(origin: Vec3, target: Vec3, bounds: Aabb) -> Interval | None:
	"""Return overlap along segment origin->target as t in [0, 1]."""
	box_min, box_max = bounds
	direction = sub(target, origin)
	t_enter = 0.0
	t_exit = 1.0

	for axis in range(3):
		if abs(direction[axis]) < 1e-10:
			if origin[axis] < box_min[axis] or origin[axis] > box_max[axis]:
				return None
			continue

		inv_dir = 1.0 / direction[axis]
		t0 = (box_min[axis] - origin[axis]) * inv_dir
		t1 = (box_max[axis] - origin[axis]) * inv_dir
		if t0 > t1:
			t0, t1 = t1, t0

		t_enter = max(t_enter, t0)
		t_exit = min(t_exit, t1)
		if t_enter > t_exit:
			return None

	t_enter = max(0.0, t_enter)
	t_exit = min(1.0, t_exit)
	if t_enter > t_exit:
		return None
	return t_enter, t_exit


def _ray_blocked_by_aabb(eye: Vec3, point: Vec3, bounds: Aabb) -> bool:
	"""Return True when solid volume lies on the eye->point segment before the point."""
	if _point_on_aabb_face_toward_eye(eye, point, bounds):
		return False

	overlap = _segment_aabb_overlap_param(eye, point, bounds)
	if overlap is None:
		return False

	t_enter, t_exit = overlap
	if t_exit - t_enter < VISIBILITY_EPSILON:
		return False

	return t_enter < 1.0 - VISIBILITY_EPSILON


def _point_visible_for_one_aabb(eye: Vec3, point: Vec3, bounds: Aabb) -> bool:
	"""Return True when a point is visible from the eye with respect to one solid box."""
	if _point_strictly_inside_aabb(point, bounds):
		return False

	if _point_on_aabb_surface(point, bounds):
		return _point_on_aabb_face_toward_eye(eye, point, bounds)

	return not _ray_blocked_by_aabb(eye, point, bounds)


def _point_visible(
	eye: Vec3,
	point: Vec3,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
) -> bool:
	"""Return True when nothing blocks the line of sight to a point."""
	for bounds in occluders:
		if not _point_visible_for_one_aabb(eye, point, bounds):
			return False

	for plane in boundaries:
		if _boundary_blocks(eye, point, plane):
			return False

	return True


def segment_line_of_sight(
	eye: Vec3,
	target: Vec3,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
) -> bool:
	"""Return True when the segment from ``eye`` to ``target`` is unobstructed.

	Public wrapper over the same AABB / boundary tests used by visual occlusion.
	"""
	return _point_visible(eye, target, occluders, boundaries)


def segment_blocking_occluders(eye: Vec3, target: Vec3, occluders: list[Aabb]) -> list[Aabb]:
	"""Return solids that block the eye→target segment, ordered by ray entry."""
	blockers: list[tuple[float, Aabb]] = []
	for bounds in occluders:
		if _point_on_aabb_face_toward_eye(eye, target, bounds):
			continue
		overlap = _segment_aabb_overlap_param(eye, target, bounds)
		if overlap is None:
			continue
		t_enter, t_exit = overlap
		if t_exit - t_enter < VISIBILITY_EPSILON:
			continue
		if t_enter >= 1.0 - VISIBILITY_EPSILON:
			continue
		blockers.append((t_enter, bounds))

	blockers.sort(key=lambda item: item[0])
	return [bounds for _, bounds in blockers]


def _collect_occlusion_breakpoints(
	start: Vec3,
	end: Vec3,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
) -> list[float]:
	"""Collect parametric t values where visibility may change along a segment."""
	breakpoints = {0.0, 1.0}

	for plane in boundaries:
		breakpoints.update(collect_axis_plane_breakpoints(start, end, plane.coord, plane.axis))

	for box_min, box_max in occluders:
		for axis in range(3):
			breakpoints.update(collect_axis_plane_breakpoints(start, end, box_min[axis], axis))
			breakpoints.update(collect_axis_plane_breakpoints(start, end, box_max[axis], axis))

	return sorted(breakpoints)


def _resolve_visible_interval(
	cache: VisibilityCache,
	start: Vec3,
	end: Vec3,
	t0: float,
	t1: float,
	depth: int = 0,
) -> list[Interval]:
	"""Recursively resolve one visible sub-interval with exact cutoffs."""
	span = t1 - t0
	if span < 1e-6:
		point = lerp(start, end, t0)
		return [(t0, t1)] if cache.is_visible(point) else []

	visible_start = cache.is_visible(lerp(start, end, t0))
	visible_end = cache.is_visible(lerp(start, end, t1))
	mid_t = (t0 + t1) * 0.5
	visible_mid = cache.is_visible(lerp(start, end, mid_t))

	if visible_start and visible_end and visible_mid:
		return [(t0, t1)]
	if not visible_start and not visible_end and not visible_mid:
		return []

	if depth >= 18:
		if visible_mid:
			return [(t0, t1)]
		return []

	left = _resolve_visible_interval(cache, start, end, t0, mid_t, depth + 1)
	right = _resolve_visible_interval(cache, start, end, mid_t, t1, depth + 1)
	return merge_intervals(left + right)


def clip_segment_visibility_intervals(
	eye: Vec3,
	start: Vec3,
	end: Vec3,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
	visibility_cache: VisibilityCache | None = None,
) -> list[Interval]:
	"""Return parametric intervals along a segment that are line-of-sight visible."""
	cache = visibility_cache or VisibilityCache(eye, occluders, boundaries)
	breakpoints = _collect_occlusion_breakpoints(start, end, occluders, boundaries)
	visible_intervals: list[Interval] = []

	for index in range(len(breakpoints) - 1):
		t0 = breakpoints[index]
		t1 = breakpoints[index + 1]
		if t1 - t0 < 1e-10:
			continue
		visible_intervals.extend(_resolve_visible_interval(cache, start, end, t0, t1))

	return merge_intervals(visible_intervals)


def clip_hull_edge_intervals(
	eye: Vec3,
	start: Vec3,
	end: Vec3,
	bounds: Aabb,
) -> list[Interval]:
	"""Return the front-facing portion of an occluder hull edge."""
	if not _point_on_aabb_surface(start, bounds) or not _point_on_aabb_surface(end, bounds):
		return [(0.0, 1.0)]

	breakpoints = {0.0, 1.0}
	box_min, box_max = bounds
	for axis in range(3):
		breakpoints.update(collect_axis_plane_breakpoints(start, end, box_min[axis], axis))
		breakpoints.update(collect_axis_plane_breakpoints(start, end, box_max[axis], axis))

	sorted_breakpoints = sorted(breakpoints)
	visible: list[Interval] = []
	for index in range(len(sorted_breakpoints) - 1):
		t0 = sorted_breakpoints[index]
		t1 = sorted_breakpoints[index + 1]
		if t1 - t0 < 1e-10:
			continue

		sample = lerp(start, end, (t0 + t1) * 0.5)
		if _point_on_aabb_face_toward_eye(eye, sample, bounds):
			visible.append((t0, t1))

	return merge_intervals(visible)


def clip_segment_visibility(
	eye: Vec3,
	start: Vec3,
	end: Vec3,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
	visibility_cache: VisibilityCache | None = None,
) -> list[tuple[Vec3, Vec3]]:
	"""Split a world-space segment into visible sub-segments with exact endpoints."""
	intervals = clip_segment_visibility_intervals(eye, start, end, occluders, boundaries, visibility_cache)
	return [(lerp(start, end, t0), lerp(start, end, t1)) for t0, t1 in intervals]
