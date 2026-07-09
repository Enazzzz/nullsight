"""Frozen scan snapshot — visible wireframe captured once at trigger time."""

import math
from dataclasses import dataclass
from typing import Protocol

from camera import camera_from_scan_pose, DEFAULT_FOV_Y
from render_cull import ViewCullSource
from math3d import Interval, Vec3, clip_segment_to_sphere_interval, intersect_intervals, merge_intervals
from occlusion import (
	Aabb,
	BoundaryPlane,
	VisibilityCache,
	clip_hull_edge_intervals,
	clip_segment_visibility_intervals,
)
from scan_memory import hull_key as _aabb_key
from room import Edge, OccluderHull
from scan_occluders import ScannedFace, ScannedOccluderCache, build_scanned_occluders, clip_segment_scanned_occluder_intervals


class ScanPulseLike(Protocol):
	"""Scan pulse fields needed to bake a snapshot."""

	origin: Vec3
	yaw: float
	pitch: float
	max_radius: float
	view_width: int
	view_height: int
	fov_y: float


@dataclass(frozen=True)
class SnapshotSegment:
	"""One edge and the visible portions frozen at scan time."""

	start: Vec3
	end: Vec3
	intervals: list[Interval]
	hull_bounds: Aabb | None = None


@dataclass(frozen=True)
class ScanSnapshot:
	"""Frozen scan capture: visible wire plus discovered occluder surfaces."""

	segments: list[SnapshotSegment]
	scanned_faces: list[ScannedFace]


def forward_view_intervals(
	start: Vec3,
	end: Vec3,
	view: ViewCullSource,
	min_forward_z: float = 0.05,
) -> list[Interval]:
	"""Return parametric intervals of an edge that lie in front of the scan view."""
	z0 = view.world_to_camera(start)[2]
	z1 = view.world_to_camera(end)[2]

	if z0 < min_forward_z and z1 < min_forward_z:
		return []
	if z0 >= min_forward_z and z1 >= min_forward_z:
		return [(0.0, 1.0)]

	delta_z = z1 - z0
	if abs(delta_z) < 1e-10:
		return []

	t_clip = (min_forward_z - z0) / delta_z
	t_clip = max(0.0, min(1.0, t_clip))
	if z0 < min_forward_z:
		return [(t_clip, 1.0)]
	return [(0.0, t_clip)]


def _camera_point_in_frustum(
	point: Vec3,
	tan_h: float,
	tan_v: float,
	min_forward_z: float,
) -> bool:
	"""Return True when a camera-space point lies inside the view frustum."""
	x, y, z = point
	if z < min_forward_z:
		return False
	if abs(x) > z * tan_h + 1e-9:
		return False
	if abs(y) > z * tan_v + 1e-9:
		return False
	return True


def _frustum_breakpoints(
	cam_start: Vec3,
	cam_end: Vec3,
	tan_h: float,
	tan_v: float,
	min_forward_z: float,
) -> list[float]:
	"""Collect parametric t values where frustum membership may change."""
	x0, y0, z0 = cam_start
	x1, y1, z1 = cam_end
	dx = x1 - x0
	dy = y1 - y0
	dz = z1 - z0
	breakpoints = {0.0, 1.0}

	def add_plane_cross(numer: float, denom: float) -> None:
		if abs(denom) < 1e-10:
			return
		t = numer / denom
		if 0.0 <= t <= 1.0:
			breakpoints.add(t)

	if abs(dz) > 1e-10:
		add_plane_cross(min_forward_z - z0, dz)

	add_plane_cross(tan_h * z0 - x0, dx - tan_h * dz)
	add_plane_cross(-tan_h * z0 - x0, dx + tan_h * dz)
	add_plane_cross(tan_v * z0 - y0, dy - tan_v * dz)
	add_plane_cross(-tan_v * z0 - y0, dy + tan_v * dz)

	return sorted(breakpoints)


def screen_view_intervals(
	start: Vec3,
	end: Vec3,
	view: ViewCullSource,
	view_width: int,
	view_height: int,
	fov_y: float = DEFAULT_FOV_Y,
	min_forward_z: float = 0.05,
) -> list[Interval]:
	"""Return parametric intervals of an edge visible inside the screen frustum."""
	if view_width <= 0 or view_height <= 0:
		return []

	aspect = view_width / view_height
	tan_v = math.tan(fov_y * 0.5)
	tan_h = tan_v * aspect
	cam_start = view.world_to_camera(start)
	cam_end = view.world_to_camera(end)
	breakpoints = _frustum_breakpoints(cam_start, cam_end, tan_h, tan_v, min_forward_z)

	visible: list[Interval] = []
	for index in range(len(breakpoints) - 1):
		t0 = breakpoints[index]
		t1 = breakpoints[index + 1]
		if t1 - t0 < 1e-10:
			continue
		mid_t = (t0 + t1) * 0.5
		mid = (
			cam_start[0] + (cam_end[0] - cam_start[0]) * mid_t,
			cam_start[1] + (cam_end[1] - cam_start[1]) * mid_t,
			cam_start[2] + (cam_end[2] - cam_start[2]) * mid_t,
		)
		if _camera_point_in_frustum(mid, tan_h, tan_v, min_forward_z):
			visible.append((t0, t1))

	return merge_intervals(visible)


def intersect_interval_lists(*interval_lists: list[Interval]) -> list[Interval]:
	"""Intersect any number of parametric interval lists."""
	return _intersect_interval_lists(*interval_lists)


def _intersect_interval_lists(*interval_lists: list[Interval]) -> list[Interval]:
	"""Intersect any number of parametric interval lists."""
	if not interval_lists:
		return []

	result = interval_lists[0]
	for interval_list in interval_lists[1:]:
		next_result: list[Interval] = []
		for a0, a1 in result:
			for b0, b1 in interval_list:
				overlap = intersect_intervals((a0, a1), (b0, b1))
				if overlap is not None:
					next_result.append(overlap)
		result = merge_intervals(next_result)
		if not result:
			return []
	return result


def _visible_intervals_for_edge(
	start: Vec3,
	end: Vec3,
	scan_view: ViewCullSource,
	scan_eye: Vec3,
	pulse: ScanPulseLike,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
	visibility_cache: VisibilityCache,
	hull_bounds: Aabb | None = None,
) -> list[Interval]:
	"""Compute scan-time visible intervals (occlusion + range; frustum applied at draw)."""
	in_front = forward_view_intervals(start, end, scan_view)
	if not in_front:
		return []

	in_range = clip_segment_to_sphere_interval(start, end, pulse.origin, pulse.max_radius)
	if in_range is None:
		return []
	in_range_list = [in_range]

	if hull_bounds is not None:
		self_key = _aabb_key(hull_bounds)
		other_occluders = [occluder for occluder in occluders if _aabb_key(occluder) != self_key]
		hull_visible = clip_hull_edge_intervals(scan_eye, start, end, hull_bounds)
		if not hull_visible:
			return []

		if other_occluders:
			occlusion_visible = clip_segment_visibility_intervals(
				scan_eye,
				start,
				end,
				other_occluders,
				boundaries,
				visibility_cache,
			)
			return _intersect_interval_lists(in_front, in_range_list, hull_visible, occlusion_visible)

		return _intersect_interval_lists(in_front, in_range_list, hull_visible)

	occlusion_visible = clip_segment_visibility_intervals(
		scan_eye,
		start,
		end,
		occluders,
		boundaries,
		visibility_cache,
	)
	return _intersect_interval_lists(in_front, in_range_list, occlusion_visible)


def _edge_in_scan_range(start: Vec3, end: Vec3, origin: Vec3, max_radius: float) -> bool:
	"""Fast reject for edges entirely outside the scan sphere."""
	return clip_segment_to_sphere_interval(start, end, origin, max_radius) is not None


def capture_scan_snapshot(
	pulse: ScanPulseLike,
	environment_edges: list[Edge],
	occluder_hulls: list[OccluderHull],
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
) -> ScanSnapshot:
	"""Bake visible wireframe at scan trigger — forward view, occlusion, and range."""
	scan_eye = pulse.origin
	scan_view = camera_from_scan_pose(pulse.origin, pulse.yaw, pulse.pitch)
	visibility_cache = VisibilityCache(scan_eye, occluders, boundaries)
	segments: list[SnapshotSegment] = []

	for start, end in environment_edges:
		if not _edge_in_scan_range(start, end, scan_eye, pulse.max_radius):
			continue
		intervals = _visible_intervals_for_edge(
			start,
			end,
			scan_view,
			scan_eye,
			pulse,
			occluders,
			boundaries,
			visibility_cache,
		)
		if intervals:
			segments.append(SnapshotSegment(start, end, intervals, hull_bounds=None))

	for bounds, hull_edges in occluder_hulls:
		# Exclude the hull being scanned so its own slab does not hide its wireframe.
		self_key = _aabb_key(bounds)
		hull_occluders = [occluder for occluder in occluders if _aabb_key(occluder) != self_key]
		hull_cache = VisibilityCache(scan_eye, hull_occluders, boundaries)
		for start, end in hull_edges:
			if not _edge_in_scan_range(start, end, scan_eye, pulse.max_radius):
				continue
			intervals = _visible_intervals_for_edge(
				start,
				end,
				scan_view,
				scan_eye,
				pulse,
				occluders,
				boundaries,
				hull_cache,
				hull_bounds=bounds,
			)
			if intervals:
				segments.append(SnapshotSegment(start, end, intervals, hull_bounds=bounds))

	scanned_faces = build_scanned_occluders(segments, scan_eye, boundaries, occluders)
	return ScanSnapshot(segments, scanned_faces)


def live_occlusion_intervals(
	start: Vec3,
	end: Vec3,
	intervals: list[Interval],
	hull_bounds: Aabb | None,
	eye: Vec3,
	scanned_faces: list[ScannedFace],
	visibility_cache: ScannedOccluderCache | None,
) -> list[Interval]:
	"""Intersect baked scan intervals with live occlusion from scan-discovered faces."""
	if not scanned_faces:
		return list(intervals)

	from scan_memory import hull_key as _aabb_key

	exclude_hull = _aabb_key(hull_bounds) if hull_bounds is not None else None
	live_visible = clip_segment_scanned_occluder_intervals(
		eye,
		start,
		end,
		scanned_faces,
		visibility_cache,
		exclude_hull_key=exclude_hull,
	)
	return _intersect_interval_lists(intervals, live_visible)
