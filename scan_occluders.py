"""Occluders derived only from geometry discovered during a scan.

Live occlusion uses **scanned face patches** built from the vertices the scan
actually touched — not full object AABBs. Each patch is an axis-aligned quad
(min/max on the face plane) connecting the sampled scan hits on that surface.
"""

from __future__ import annotations

import math
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
from occlusion import BoundaryPlane, SURFACE_EPSILON, VISIBILITY_EPSILON

# Hull identity for grouping faces on the same solid.
HullKey = tuple[tuple[float, float, float], tuple[float, float, float]]
FaceKey = tuple[HullKey | None, int, float]

# Slabs thinner than this register both sides so center-plane wire still occludes.
_THIN_SLAB_EXTENT = 0.25


@dataclass(frozen=True)
class ScannedFace:
	"""Axis-aligned surface patch discovered during a scan (two-sided occluder)."""

	axis: int
	coord: float
	span_a_axis: int
	span_b_axis: int
	min_a: float
	max_a: float
	min_b: float
	max_b: float
	hull_key: HullKey | None = None


# World-space grid cell size for spatial face queries in large levels.
FACE_GRID_CELL = 4.0


def _face_world_bounds(face: ScannedFace) -> tuple[Vec3, Vec3]:
	"""Return a loose axis-aligned bounds volume for one scanned face."""
	pad = SURFACE_EPSILON
	lo = [0.0, 0.0, 0.0]
	hi = [0.0, 0.0, 0.0]
	lo[face.axis] = face.coord - pad
	hi[face.axis] = face.coord + pad
	lo[face.span_a_axis] = face.min_a
	hi[face.span_a_axis] = face.max_a
	lo[face.span_b_axis] = face.min_b
	hi[face.span_b_axis] = face.max_b
	return (lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])


def _cell_span(min_v: float, max_v: float, cell: float) -> tuple[int, int]:
	"""Return inclusive grid cell index range for a world axis span."""
	c0 = int(math.floor(min_v / cell))
	c1 = int(math.floor(max_v / cell))
	return c0, c1


class ScannedFaceIndex:
	"""Uniform grid index for narrowing live-occlusion face tests in large spaces."""

	def __init__(self, faces: list[ScannedFace], cell_size: float = FACE_GRID_CELL) -> None:
		self._faces = faces
		self._cell = cell_size
		self._cells: dict[tuple[int, int, int], list[int]] = {}
		for index, face in enumerate(faces):
			box_min, box_max = _face_world_bounds(face)
			x0, x1 = _cell_span(box_min[0], box_max[0], cell_size)
			y0, y1 = _cell_span(box_min[1], box_max[1], cell_size)
			z0, z1 = _cell_span(box_min[2], box_max[2], cell_size)
			for cx in range(x0, x1 + 1):
				for cy in range(y0, y1 + 1):
					for cz in range(z0, z1 + 1):
						self._cells.setdefault((cx, cy, cz), []).append(index)

	def query_indices(self, start: Vec3, end: Vec3, eye: Vec3 | None = None) -> list[int]:
		"""Return candidate face indices overlapping the eye-to-segment bounds."""
		box_min = (
			min(start[0], end[0]),
			min(start[1], end[1]),
			min(start[2], end[2]),
		)
		box_max = (
			max(start[0], end[0]),
			max(start[1], end[1]),
			max(start[2], end[2]),
		)
		if eye is not None:
			box_min = (
				min(box_min[0], eye[0]),
				min(box_min[1], eye[1]),
				min(box_min[2], eye[2]),
			)
			box_max = (
				max(box_max[0], eye[0]),
				max(box_max[1], eye[1]),
				max(box_max[2], eye[2]),
			)
		x0, x1 = _cell_span(box_min[0], box_max[0], self._cell)
		y0, y1 = _cell_span(box_min[1], box_max[1], self._cell)
		z0, z1 = _cell_span(box_min[2], box_max[2], self._cell)

		seen: set[int] = set()
		ordered: list[int] = []
		for cx in range(x0, x1 + 1):
			for cy in range(y0, y1 + 1):
				for cz in range(z0, z1 + 1):
					for index in self._cells.get((cx, cy, cz), ()):
						if index in seen:
							continue
						seen.add(index)
						ordered.append(index)
		return ordered


@dataclass
class _FaceBuilder:
	"""Mutable accumulator for merging scan hits onto one face quad."""

	axis: int
	coord: float
	span_a_axis: int
	span_b_axis: int
	min_a: float = float("inf")
	max_a: float = float("-inf")
	min_b: float = float("inf")
	max_b: float = float("-inf")
	_source_hull: HullKey | None = field(default=None, repr=False)

	def key(self, hull_key: HullKey | None) -> FaceKey:
		"""Stable identity for deduplicating faces during a scan bake."""
		return (hull_key, self.axis, round(self.coord, 4))

	def set_source_hull(self, hull_key: HullKey | None) -> None:
		"""Remember which solid hull this face patch belongs to."""
		self._source_hull = hull_key

	def include(self, point: Vec3) -> None:
		"""Expand this face quad to cover a scanned surface point."""
		self.min_a = min(self.min_a, point[self.span_a_axis])
		self.max_a = max(self.max_a, point[self.span_a_axis])
		self.min_b = min(self.min_b, point[self.span_b_axis])
		self.max_b = max(self.max_b, point[self.span_b_axis])

	def is_valid(self) -> bool:
		"""Return True when at least one scan sample landed on this face."""
		return self.min_a <= self.max_a and self.min_b <= self.max_b

	def build(self) -> ScannedFace:
		"""Finalize a frozen scanned face patch from the hit bounding quad."""
		pad = SURFACE_EPSILON
		return ScannedFace(
			self.axis,
			self.coord,
			self.span_a_axis,
			self.span_b_axis,
			self.min_a - pad,
			self.max_a + pad,
			self.min_b - pad,
			self.max_b + pad,
			hull_key=self._source_hull,
		)


@dataclass
class ScannedOccluderCache:
	"""Per-frame cache for visibility against scan-discovered surfaces only."""

	eye: Vec3
	faces: list[ScannedFace]
	face_index: ScannedFaceIndex | None = None
	_values: dict[tuple[float, float, float], bool] = field(default_factory=dict)

	def is_visible(self, point: Vec3) -> bool:
		"""Return True when no scanned surface blocks the eye from this point."""
		key = vertex_key(point)
		if key not in self._values:
			relevant = _faces_relevant_to_point(self.eye, point, self.faces)
			self._values[key] = _point_visible_from_scanned_faces(self.eye, point, relevant)
		return self._values[key]


def _other_axes(axis: int) -> tuple[int, int]:
	"""Return the two axes orthogonal to ``axis``."""
	if axis == 0:
		return 1, 2
	if axis == 1:
		return 0, 2
	return 0, 1


def _hull_key(bounds: tuple[Vec3, Vec3]) -> HullKey:
	"""Hashable key for one scanned solid hull."""
	box_min, box_max = bounds
	return (
		(round(box_min[0], 4), round(box_min[1], 4), round(box_min[2], 4)),
		(round(box_max[0], 4), round(box_max[1], 4), round(box_max[2], 4)),
	)


def _point_inside_aabb(point: Vec3, bounds: tuple[Vec3, Vec3]) -> bool:
	"""Return True when a point lies inside an axis-aligned box (inclusive)."""
	box_min, box_max = bounds
	return (
		box_min[0] - SURFACE_EPSILON <= point[0] <= box_max[0] + SURFACE_EPSILON
		and box_min[1] - SURFACE_EPSILON <= point[1] <= box_max[1] + SURFACE_EPSILON
		and box_min[2] - SURFACE_EPSILON <= point[2] <= box_max[2] + SURFACE_EPSILON
	)


def _segment_hull_faces(start: Vec3, end: Vec3, bounds: tuple[Vec3, Vec3]) -> list[tuple[int, float]]:
	"""Return the hull face plane(s) a wire segment lies on.

	Center-plane wall wire (not exactly on the AABB face) still maps to the
	nearest face, or both faces when the solid is a thin slab.
	"""
	box_min, box_max = bounds
	faces: list[tuple[int, float]] = []
	seen: set[tuple[int, float]] = set()

	def add(axis: int, coord: float) -> None:
		key = (axis, round(coord, 4))
		if key in seen:
			return
		seen.add(key)
		faces.append((axis, coord))

	const_axes: list[int] = []
	if abs(end[0] - start[0]) <= SURFACE_EPSILON:
		const_axes.append(0)
	if abs(end[1] - start[1]) <= SURFACE_EPSILON:
		const_axes.append(1)
	if abs(end[2] - start[2]) <= SURFACE_EPSILON:
		const_axes.append(2)

	for axis in const_axes:
		extent = box_max[axis] - box_min[axis]
		edge_val = start[axis]
		if extent <= _THIN_SLAB_EXTENT:
			add(axis, box_min[axis])
			add(axis, box_max[axis])
		elif abs(edge_val - box_min[axis]) <= abs(edge_val - box_max[axis]):
			add(axis, box_min[axis])
		else:
			add(axis, box_max[axis])

	if faces:
		return faces

	mid = lerp(start, end, 0.5)
	if not _point_inside_aabb(mid, bounds):
		return faces

	for axis in range(3):
		extent = box_max[axis] - box_min[axis]
		if extent <= _THIN_SLAB_EXTENT:
			add(axis, box_min[axis])
			add(axis, box_max[axis])
		elif abs(mid[axis] - box_min[axis]) <= abs(mid[axis] - box_max[axis]):
			add(axis, box_min[axis])
		else:
			add(axis, box_max[axis])
	return faces


def _boundary_faces_for_point(
	point: Vec3,
	boundaries: list[BoundaryPlane],
) -> list[tuple[int, float]]:
	"""Return boundary planes a scanned environment point lies on."""
	faces: list[tuple[int, float]] = []
	for plane in boundaries:
		if abs(point[plane.axis] - plane.coord) <= SURFACE_EPSILON:
			faces.append((plane.axis, plane.coord))
	return faces


def _interval_sample_ts(t0: float, t1: float) -> list[float]:
	"""Sample parametric positions along a scanned edge interval."""
	if t1 - t0 < 1e-10:
		return [t0]
	return [t0, t1, (t0 + t1) * 0.5]


def _ensure_face_builder(
	builders: dict[FaceKey, _FaceBuilder],
	hull_keys: dict[FaceKey, HullKey],
	*,
	axis: int,
	coord: float,
	hull_id: HullKey | None,
) -> _FaceBuilder:
	"""Fetch or create a face accumulator for one plane."""
	span_a_axis, span_b_axis = _other_axes(axis)
	builder = _FaceBuilder(axis, coord, span_a_axis, span_b_axis)
	key = builder.key(hull_id)
	if key not in builders:
		builders[key] = builder
		if hull_id is not None:
			hull_keys[key] = hull_id
	builder = builders[key]
	builder.set_source_hull(hull_id)
	return builder


def _register_segment_point(
	builders: dict[FaceKey, _FaceBuilder],
	hull_keys: dict[FaceKey, HullKey],
	*,
	segment_start: Vec3,
	segment_end: Vec3,
	hull_bounds: tuple[Vec3, Vec3] | None,
	boundaries: list[BoundaryPlane],
	point: Vec3,
	occluders: list[tuple[Vec3, Vec3]] | None,
) -> None:
	"""Attach a scanned point to every face plane the segment touches."""
	if hull_bounds is not None:
		hull_id = _hull_key(hull_bounds)
		for axis, coord in _segment_hull_faces(segment_start, segment_end, hull_bounds):
			_ensure_face_builder(
				builders,
				hull_keys,
				axis=axis,
				coord=coord,
				hull_id=hull_id,
			).include(point)
		return

	for axis, coord in _boundary_faces_for_point(point, boundaries):
		_ensure_face_builder(
			builders,
			hull_keys,
			axis=axis,
			coord=coord,
			hull_id=None,
		).include(point)

	if not occluders:
		return

	for bounds in occluders:
		if not _point_inside_aabb(point, bounds):
			continue
		hull_id = _hull_key(bounds)
		for axis, coord in _segment_hull_faces(segment_start, segment_end, bounds):
			_ensure_face_builder(
				builders,
				hull_keys,
				axis=axis,
				coord=coord,
				hull_id=hull_id,
			).include(point)


def build_scanned_occluders(
	segments: list,
	_scan_eye: Vec3,
	boundaries: list[BoundaryPlane],
	occluders: list[tuple[Vec3, Vec3]] | None = None,
) -> list[ScannedFace]:
	"""Build face quads from scan hits — each patch spans only discovered vertices."""
	builders: dict[FaceKey, _FaceBuilder] = {}
	hull_keys: dict[FaceKey, HullKey] = {}
	solid_bounds = list(occluders or [])

	for segment in segments:
		hull_bounds = segment.hull_bounds
		sample_points: list[Vec3] = [segment.start, segment.end]
		for t0, t1 in segment.intervals:
			for t in _interval_sample_ts(t0, t1):
				sample_points.append(lerp(segment.start, segment.end, t))

		if hull_bounds is not None:
			hull_id = _hull_key(hull_bounds)
			for axis, coord in _segment_hull_faces(segment.start, segment.end, hull_bounds):
				face_builder = _ensure_face_builder(
					builders,
					hull_keys,
					axis=axis,
					coord=coord,
					hull_id=hull_id,
				)
				face_builder.include(segment.start)
				face_builder.include(segment.end)

		for point in sample_points:
			_register_segment_point(
				builders,
				hull_keys,
				segment_start=segment.start,
				segment_end=segment.end,
				hull_bounds=hull_bounds,
				boundaries=boundaries,
				point=point,
				occluders=solid_bounds if hull_bounds is None else None,
			)

	faces: list[ScannedFace] = []
	for builder in builders.values():
		if builder.is_valid():
			faces.append(builder.build())
	return faces


def _span_overlaps_face(
	start: Vec3,
	end: Vec3,
	span_axis: int,
	face_min: float,
	face_max: float,
	margin: float = SURFACE_EPSILON,
) -> bool:
	"""Return True when a segment overlaps a face span on one axis."""
	segment_min = min(start[span_axis], end[span_axis]) - margin
	segment_max = max(start[span_axis], end[span_axis]) + margin
	return segment_max >= face_min and segment_min <= face_max


def _faces_relevant_to_segment(
	eye: Vec3,
	start: Vec3,
	end: Vec3,
	faces: list[ScannedFace],
	candidate_indices: list[int] | None = None,
) -> list[ScannedFace]:
	"""Return scanned faces that may affect visibility along a segment."""
	relevant: list[ScannedFace] = []
	indices = candidate_indices if candidate_indices is not None else range(len(faces))
	eps2 = SURFACE_EPSILON * SURFACE_EPSILON
	for index in indices:
		face = faces[index]
		axis = face.axis
		coord = face.coord
		on_plane = False
		for point in (start, end):
			if abs(point[axis] - coord) <= SURFACE_EPSILON:
				on_plane = True
				break
		if not on_plane:
			d0 = start[axis] - coord
			d1 = end[axis] - coord
			eye_off = eye[axis] - coord
			if d0 * d1 > eps2 and d0 * eye_off > eps2:
				continue

		if not _span_overlaps_face(start, end, face.span_a_axis, face.min_a, face.max_a):
			continue
		if not _span_overlaps_face(start, end, face.span_b_axis, face.min_b, face.max_b):
			continue

		relevant.append(face)
	return relevant


def _faces_relevant_to_point(
	eye: Vec3,
	point: Vec3,
	faces: list[ScannedFace],
) -> list[ScannedFace]:
	"""Return scanned faces that may block line of sight from the eye to a point."""
	relevant: list[ScannedFace] = []
	for face in faces:
		axis = face.axis
		if _point_on_scanned_face(point, face):
			relevant.append(face)
			continue

		eye_offset = eye[axis] - face.coord
		point_offset = point[axis] - face.coord
		if eye_offset * point_offset > VISIBILITY_EPSILON:
			continue

		if not face.min_a - SURFACE_EPSILON <= point[face.span_a_axis] <= face.max_a + SURFACE_EPSILON:
			continue
		if not face.min_b - SURFACE_EPSILON <= point[face.span_b_axis] <= face.max_b + SURFACE_EPSILON:
			continue

		relevant.append(face)
	return relevant


def _point_on_any_scanned_face(point: Vec3, faces: list[ScannedFace]) -> bool:
	"""Return True when a point lies on any scanned face patch."""
	for face in faces:
		if _point_on_scanned_face(point, face):
			return True
	return False


def _point_on_scanned_face(point: Vec3, face: ScannedFace) -> bool:
	"""Return True when a point lies on a scanned face rectangle."""
	if abs(point[face.axis] - face.coord) > SURFACE_EPSILON:
		return False
	return (
		face.min_a - SURFACE_EPSILON <= point[face.span_a_axis] <= face.max_a + SURFACE_EPSILON
		and face.min_b - SURFACE_EPSILON <= point[face.span_b_axis] <= face.max_b + SURFACE_EPSILON
	)


def _segment_hits_scanned_face(eye: Vec3, point: Vec3, face: ScannedFace) -> bool:
	"""Return True when a ray passes through a two-sided scanned face patch."""
	direction = sub(point, eye)
	axis = face.axis
	denom = direction[axis]
	if abs(denom) < 1e-10:
		return False

	t_hit = (face.coord - eye[axis]) / denom
	if t_hit <= VISIBILITY_EPSILON or t_hit >= 1.0 - VISIBILITY_EPSILON:
		return False

	hit = lerp(eye, point, t_hit)
	return (
		face.min_a - SURFACE_EPSILON <= hit[face.span_a_axis] <= face.max_a + SURFACE_EPSILON
		and face.min_b - SURFACE_EPSILON <= hit[face.span_b_axis] <= face.max_b + SURFACE_EPSILON
	)


def _point_visible_for_one_face(eye: Vec3, point: Vec3, face: ScannedFace) -> bool:
	"""Return True when one scanned face does not block the eye from a point."""
	if _point_on_scanned_face(point, face):
		return True
	eye_offset = eye[face.axis] - face.coord
	point_offset = point[face.axis] - face.coord
	# Same side of the plane — nothing to block.
	if eye_offset * point_offset > VISIBILITY_EPSILON:
		return True
	return not _segment_hits_scanned_face(eye, point, face)


def _point_visible_from_scanned_faces(eye: Vec3, point: Vec3, faces: list[ScannedFace]) -> bool:
	"""Return True when every scanned face allows line of sight to a point."""
	for face in faces:
		if not _point_visible_for_one_face(eye, point, face):
			return False
	return True


def _segment_crosses_face_plane(start: Vec3, end: Vec3, face: ScannedFace) -> bool:
	"""Return True when a segment crosses a scanned face plane."""
	axis = face.axis
	d0 = start[axis] - face.coord
	d1 = end[axis] - face.coord
	return d0 * d1 < -(SURFACE_EPSILON * SURFACE_EPSILON)


def _segment_crosses_any_face_plane(start: Vec3, end: Vec3, faces: list[ScannedFace]) -> bool:
	"""Return True when a segment crosses any relevant face plane."""
	for face in faces:
		if _segment_crosses_face_plane(start, end, face):
			return True
	return False


def _collect_scanned_face_plane_breakpoints(
	start: Vec3,
	end: Vec3,
	faces: list[ScannedFace],
) -> list[float]:
	"""Collect parametric breakpoints where a segment crosses a face plane."""
	breakpoints = {0.0, 1.0}
	for face in faces:
		breakpoints.update(collect_axis_plane_breakpoints(start, end, face.coord, face.axis))
	return sorted(breakpoints)


def _collect_scanned_face_breakpoints(
	start: Vec3,
	end: Vec3,
	faces: list[ScannedFace],
) -> list[float]:
	"""Collect parametric breakpoints where scanned-face visibility may change."""
	breakpoints = {0.0, 1.0}
	for face in faces:
		breakpoints.update(collect_axis_plane_breakpoints(start, end, face.coord, face.axis))
		breakpoints.update(
			collect_axis_plane_breakpoints(start, end, face.min_a, face.span_a_axis)
		)
		breakpoints.update(
			collect_axis_plane_breakpoints(start, end, face.max_a, face.span_a_axis)
		)
		breakpoints.update(
			collect_axis_plane_breakpoints(start, end, face.min_b, face.span_b_axis)
		)
		breakpoints.update(
			collect_axis_plane_breakpoints(start, end, face.max_b, face.span_b_axis)
		)
	return sorted(breakpoints)


def clip_segment_scanned_occluder_intervals(
	eye: Vec3,
	start: Vec3,
	end: Vec3,
	scanned_faces: list[ScannedFace],
	visibility_cache: ScannedOccluderCache | None = None,
	candidate_indices: list[int] | None = None,
	exclude_hull_key: HullKey | None = None,
) -> list[Interval]:
	"""Return visible intervals along a segment, blocked only by scan-discovered faces."""
	if not scanned_faces:
		return [(0.0, 1.0)]

	if exclude_hull_key is not None:
		scanned_faces = [
			face for face in scanned_faces if face.hull_key != exclude_hull_key
		]
		if not scanned_faces:
			return [(0.0, 1.0)]

	indices = candidate_indices
	if indices is not None and not indices:
		indices = None

	if indices is None and exclude_hull_key is None and visibility_cache is not None and visibility_cache.face_index is not None:
		queried = visibility_cache.face_index.query_indices(start, end, eye)
		if queried:
			indices = queried

	relevant_faces = _faces_relevant_to_segment(
		eye,
		start,
		end,
		scanned_faces,
		indices,
	)
	if not relevant_faces:
		return [(0.0, 1.0)]

	cache = visibility_cache
	if cache is not None and exclude_hull_key is not None:
		cache = None
	if cache is None:
		cache = ScannedOccluderCache(eye, scanned_faces)

	# Endpoint-on-face shortcut: geometry lying on a scanned surface stays visible.
	if _point_on_any_scanned_face(start, relevant_faces) and _point_on_any_scanned_face(
		end, relevant_faces
	):
		return [(0.0, 1.0)]

	if _point_visible_from_scanned_faces(eye, start, relevant_faces) and _point_visible_from_scanned_faces(
		eye, end, relevant_faces
	):
		if not _segment_crosses_any_face_plane(start, end, relevant_faces):
			return [(0.0, 1.0)]

	breakpoints = _collect_scanned_face_breakpoints(start, end, relevant_faces)
	if len(breakpoints) <= 2:
		mid = lerp(start, end, 0.5)
		if cache.is_visible(mid):
			return [(0.0, 1.0)]
		return []

	visible_intervals: list[Interval] = []

	for index in range(len(breakpoints) - 1):
		t0 = breakpoints[index]
		t1 = breakpoints[index + 1]
		if t1 - t0 < 1e-10:
			continue
		mid_t = (t0 + t1) * 0.5
		if cache.is_visible(lerp(start, end, mid_t)):
			visible_intervals.append((t0, t1))

	return merge_intervals(visible_intervals)
