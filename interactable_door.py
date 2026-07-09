"""Interactable hinged door — scan-culled panel, collision, and open animation."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from camera import Camera
from level_builder import EDGE_SUBDIVISIONS
from math3d import Vec3, subdivide_edges
from occlusion import Aabb
from room import Edge, OccluderHull
from scan_memory import hull_key

if TYPE_CHECKING:
	from scan import ScanDevice
	from scan_memory import ScannedSegment

# Full open sweep (radians) and how long the swing takes.
_OPEN_ANGLE = math.pi * 0.5
_OPEN_DURATION_SEC = 0.9
# Player must be within this distance and roughly facing the door.
_INTERACT_RANGE = 2.75
_INTERACT_FACING_MIN = 0.42
_COLLISION_DISABLE_ANGLE = math.radians(68.0)
_PANEL_THICKNESS = 0.08
_BIND_EPSILON = 0.08


def _rotate_y(point: Vec3, hinge: Vec3, angle: float) -> Vec3:
	"""Rotate ``point`` around a vertical axis through ``hinge``."""
	x, y, z = point
	hx, _, hz = hinge
	dx = x - hx
	dz = z - hz
	cos_a = math.cos(angle)
	sin_a = math.sin(angle)
	return (hx + dx * cos_a - dz * sin_a, y, hz + dx * sin_a + dz * cos_a)


def _edge_near(a: Edge, b: Edge, epsilon: float) -> bool:
	"""Return True when two edges share endpoints within ``epsilon``."""
	(a0, a1), (b0, b1) = a, b

	def near(p: Vec3, q: Vec3) -> bool:
		return (
			abs(p[0] - q[0]) <= epsilon
			and abs(p[1] - q[1]) <= epsilon
			and abs(p[2] - q[2]) <= epsilon
		)

	return (near(a0, b0) and near(a1, b1)) or (near(a0, b1) and near(a1, b0))


class InteractableDoor:
	"""A single hinged door on the east wall of the small room."""

	def __init__(
		self,
		*,
		wall_x: float,
		hinge_z: float,
		width: float,
		height: float,
		floor_y: float = 0.0,
		swing_sign: float = 1.0,
	) -> None:
		self.wall_x = wall_x
		self.hinge_z = hinge_z
		self.width = width
		self.height = height
		self.floor_y = floor_y
		self.swing_sign = swing_sign
		self.hinge: Vec3 = (wall_x, floor_y, hinge_z)
		self.open_angle = 0.0
		self._target_angle = 0.0
		self._open_speed = _OPEN_ANGLE / _OPEN_DURATION_SEC
		self.frame_edges = self._build_frame_edges()
		self._tracked_panel_segments: list[ScannedSegment] = []
		self._closed_panel_hull_key: tuple | None = None
		self.interact_point: Vec3 = (
			wall_x - 0.35,
			floor_y + height * 0.45,
			hinge_z + width * 0.5,
		)

	@classmethod
	def east_wall(
		cls,
		*,
		room_extent: float,
		z_center: float,
		width: float = 1.0,
		height: float = 2.15,
		floor_y: float = 0.0,
	) -> InteractableDoor:
		"""Door on the east wall of a square room, swinging inward."""
		wall_x = room_extent - 0.04
		hinge_z = z_center - width * 0.5
		return cls(
			wall_x=wall_x,
			hinge_z=hinge_z,
			width=width,
			height=height,
			floor_y=floor_y,
			swing_sign=1.0,
		)

	def _build_frame_edges(self) -> list[Edge]:
		"""Static jamb and lintel outline on the wall."""
		z0 = self.hinge_z
		z1 = self.hinge_z + self.width
		y0 = self.floor_y
		y1 = self.floor_y + self.height
		x = self.wall_x
		return [
			((x, y0, z0), (x, y1, z0)),
			((x, y1, z0), (x, y1, z1)),
			((x, y1, z1), (x, y0, z1)),
			((x, y0, z1), (x, y0, z0)),
		]

	def _closed_panel_corners(self) -> tuple[Vec3, Vec3, Vec3, Vec3]:
		"""Bottom/top hinge and outer corners when the door is shut."""
		z_outer = self.hinge_z + self.width
		y0 = self.floor_y
		y1 = self.floor_y + self.height
		x = self.wall_x
		return (
			(x, y0, self.hinge_z),
			(x, y1, self.hinge_z),
			(x, y0, z_outer),
			(x, y1, z_outer),
		)

	def panel_edges(self) -> list[Edge]:
		"""Current door panel wireframe after swing animation."""
		angle = self.open_angle * self.swing_sign
		corners = self._closed_panel_corners()
		rotated = tuple(_rotate_y(corner, self.hinge, angle) for corner in corners)
		hinge_bottom, hinge_top, outer_bottom, outer_top = rotated
		thick = _PANEL_THICKNESS
		edges: list[Edge] = [
			(hinge_bottom, hinge_top),
			(outer_bottom, outer_top),
			(hinge_bottom, outer_bottom),
			(hinge_top, outer_top),
		]
		# Thin depth lines on the panel face.
		normal_offset = (-thick, 0.0, 0.0)
		for start, end in list(edges):
			s2 = (start[0] + normal_offset[0], start[1], start[2])
			e2 = (end[0] + normal_offset[0], end[1], end[2])
			edges.append((start, s2))
			edges.append((end, e2))
			edges.append((s2, e2))
		return edges

	def _panel_occluder_bounds(self) -> Aabb | None:
		"""Axis-aligned bounds for scan occlusion while the panel still blocks sight."""
		if self.open_angle >= _COLLISION_DISABLE_ANGLE:
			return None
		corners = self._closed_panel_corners()
		angle = self.open_angle * self.swing_sign
		rotated = [_rotate_y(corner, self.hinge, angle) for corner in corners]
		xs = [point[0] for point in rotated]
		zs = [point[2] for point in rotated]
		z_pad = 0.04
		# West face sits one panel thickness behind the visible wireframe so scan
		# registers a solid face on the same plane as the drawn edges.
		return (
			(min(xs) - _PANEL_THICKNESS, self.floor_y, min(zs) - z_pad),
			(max(xs), self.floor_y + self.height, max(zs) + z_pad),
		)

	def _closed_panel_occluder_bounds(self) -> Aabb | None:
		"""Panel bounds at the shut pose — used to bind scanned hull segments."""
		saved_angle = self.open_angle
		self.open_angle = 0.0
		bounds = self._panel_occluder_bounds()
		self.open_angle = saved_angle
		return bounds

	def _densified_panel_edges(self) -> list[Edge]:
		"""Panel wireframe subdivided like other solid level geometry."""
		return subdivide_edges(self.panel_edges(), EDGE_SUBDIVISIONS)

	def panel_occluder_hull(self) -> OccluderHull | None:
		"""Solid scan hull for the door panel — blocks line of sight like a wall."""
		bounds = self._panel_occluder_bounds()
		if bounds is None:
			return None
		return bounds, self._densified_panel_edges()

	@property
	def is_open(self) -> bool:
		"""Return True once the door has finished opening."""
		return self.open_angle >= _OPEN_ANGLE - 0.02

	@property
	def is_animating(self) -> bool:
		"""Return True while the door is mid-swing."""
		return self.open_angle < self._target_angle - 1e-4

	def update(self, dt: float) -> None:
		"""Advance the swing animation toward the target angle."""
		if dt <= 0.0:
			return
		if self.open_angle < self._target_angle:
			self.open_angle = min(self._target_angle, self.open_angle + self._open_speed * dt)

	def try_open(self) -> bool:
		"""Begin opening when not already open or animating."""
		if self.is_open or self.is_animating:
			return False
		self._target_angle = _OPEN_ANGLE
		return True

	def can_interact(self, camera: Camera) -> bool:
		"""Return True when the player is close enough and facing the door."""
		if self.is_open or self.is_animating:
			return False
		px, _, pz = camera.position
		tx, _, tz = self.interact_point
		dx = tx - px
		dz = tz - pz
		dist_sq = dx * dx + dz * dz
		if dist_sq > _INTERACT_RANGE * _INTERACT_RANGE:
			return False
		dist = math.sqrt(dist_sq)
		if dist <= 1e-6:
			return True
		to_door_x = dx / dist
		to_door_z = dz / dist
		fwd_x = math.sin(camera.yaw)
		fwd_z = math.cos(camera.yaw)
		facing = fwd_x * to_door_x + fwd_z * to_door_z
		return facing >= _INTERACT_FACING_MIN

	def collidable(self) -> Aabb | None:
		"""Tight axis-aligned bounds for the swung panel, or None when passable."""
		return self._panel_occluder_bounds()

	def _closed_panel_edges(self) -> list[Edge]:
		"""Panel edges at the shut pose — used to bind scanned segments."""
		saved_angle = self.open_angle
		self.open_angle = 0.0
		edges = self.panel_edges()
		self.open_angle = saved_angle
		return edges

	def _bind_panel_segments(self, scan: ScanDevice) -> None:
		"""Link scanned panel segments in the same order as densified panel edges."""
		closed_bounds = self._closed_panel_occluder_bounds()
		if closed_bounds is None:
			self._tracked_panel_segments = []
			self._closed_panel_hull_key = None
			return
		self._closed_panel_hull_key = hull_key(closed_bounds)
		closed_edges = subdivide_edges(self._closed_panel_edges(), EDGE_SUBDIVISIONS)
		candidates = [
			segment
			for segment in scan.memory.segments
			if segment.hull_bounds is not None
			and hull_key(segment.hull_bounds) == self._closed_panel_hull_key
		]
		matched: list[ScannedSegment] = []
		used: set[int] = set()
		for edge in closed_edges:
			for segment in candidates:
				segment_id = id(segment)
				if segment_id in used:
					continue
				if _edge_near(edge, (segment.start, segment.end), _BIND_EPSILON):
					matched.append(segment)
					used.add(segment_id)
					break
		self._tracked_panel_segments = matched

	def sync_scanned_panel(self, scan: ScanDevice) -> None:
		"""Move already-scanned panel segments and refresh occlusion with the swing."""
		if not scan.is_active:
			self._tracked_panel_segments = []
			self._closed_panel_hull_key = None
			return

		panel_edges = self._densified_panel_edges()
		if not self._tracked_panel_segments:
			self._bind_panel_segments(scan)

		if not self._tracked_panel_segments:
			return

		if len(self._tracked_panel_segments) != len(panel_edges):
			self._bind_panel_segments(scan)
		if len(self._tracked_panel_segments) != len(panel_edges):
			return

		bounds = self._panel_occluder_bounds()
		moved = False
		for segment, (start, end) in zip(self._tracked_panel_segments, panel_edges):
			if segment.start != start or segment.end != end or segment.hull_bounds != bounds:
				segment.start = start
				segment.end = end
				segment.hull_bounds = bounds
				moved = True

		if moved or self.is_animating:
			scan.rebuild_scanned_faces()

	def reset(self) -> None:
		"""Close the door for a new run or return to title."""
		self.open_angle = 0.0
		self._target_angle = 0.0
		self._tracked_panel_segments = []
		self._closed_panel_hull_key = None

	def draw_interact_prompt(self, engine, width: int, height: int) -> None:
		"""Show E / right-click hint when the player can open the door."""
		from ui_text import draw_text, text_width

		label = "E / RMB"
		scale = 2
		label_w = text_width(label, scale)
		draw_text(
			engine,
			(width - label_w) // 2,
			height - 48,
			label,
			48,
			255,
			120,
			scale=scale,
		)
