"""View frustum helpers for skipping off-screen scanned geometry."""

from __future__ import annotations

import math
from typing import Protocol

from math3d import Vec3

NEAR_PLANE_Z = 0.05
_FRUSTUM_EPSILON = 1e-9


class ViewCullSource(Protocol):
	"""Minimal camera projection interface for culling."""

	def world_to_camera(self, point: Vec3) -> Vec3: ...

	def project_world(self, world: Vec3) -> tuple[int, int] | None: ...

	@property
	def width(self) -> int: ...

	@property
	def height(self) -> int: ...


def _camera_point_in_frustum(
	point: Vec3,
	tan_h: float,
	tan_v: float,
	min_forward_z: float = NEAR_PLANE_Z,
) -> bool:
	"""Return True when a camera-space point lies inside the view frustum."""
	x, y, z = point
	if z < min_forward_z:
		return False
	if abs(x) > z * tan_h + _FRUSTUM_EPSILON:
		return False
	if abs(y) > z * tan_v + _FRUSTUM_EPSILON:
		return False
	return True


def segment_wholly_in_view_frustum(
	view: ViewCullSource,
	start: Vec3,
	end: Vec3,
	fov_y: float,
	min_forward_z: float = NEAR_PLANE_Z,
) -> bool:
	"""Return True when an entire segment lies inside the view frustum.

	A convex frustum contains every point on a segment once both endpoints are
	inside and in front of the near plane, so this skips expensive per-edge
	frustum clipping for the common in-view case.
	"""
	if view.width <= 0 or view.height <= 0:
		return False

	cam_start = view.world_to_camera(start)
	cam_end = view.world_to_camera(end)
	if cam_start[2] < min_forward_z or cam_end[2] < min_forward_z:
		return False

	tan_v = math.tan(fov_y * 0.5)
	tan_h = tan_v * (view.width / view.height)
	return _camera_point_in_frustum(cam_start, tan_h, tan_v, min_forward_z) and _camera_point_in_frustum(
		cam_end, tan_h, tan_v, min_forward_z
	)


def segment_behind_near_plane(view: ViewCullSource, start: Vec3, end: Vec3) -> bool:
	"""Return True when both endpoints are behind the near clip plane."""
	cam_start = view.world_to_camera(start)
	cam_end = view.world_to_camera(end)
	return cam_start[2] < NEAR_PLANE_Z and cam_end[2] < NEAR_PLANE_Z


def segment_fully_off_screen(
	view: ViewCullSource,
	start: Vec3,
	end: Vec3,
	margin: int = 48,
) -> bool:
	"""Return True when both projected endpoints lie outside the screen (with margin)."""
	screen_start = view.project_world(start)
	screen_end = view.project_world(end)
	if screen_start is None or screen_end is None:
		return False

	width = view.width + margin * 2
	height = view.height + margin * 2
	min_x = -margin
	min_y = -margin

	for x, y in (screen_start, screen_end):
		if min_x <= x < width and min_y <= y < height:
			return False
	return True
