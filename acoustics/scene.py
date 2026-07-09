"""Build acoustic occluder lists from room geometry and the interactable door."""

from __future__ import annotations

from typing import TYPE_CHECKING

from acoustics.portals import door_open_fraction, door_portal_bleed
from occlusion import Aabb, BoundaryPlane
from room import RoomData

if TYPE_CHECKING:
	from interactable_door import InteractableDoor

# Door angle must change by this much (radians) to bump geometry revision.
_DOOR_ANGLE_REVISION_EPS = 0.02
# Match LevelBuilder decor pad so frame AABB identity compares cleanly.
_FRAME_AABB_PAD = 0.04
# Door panel material when it occludes (MAM double-leaf transmission).
_DOOR_PANEL_MATERIAL = "metal_door"


def _door_frame_aabb(door: InteractableDoor) -> Aabb:
	"""Axis-aligned bounds of the static door frame (fills the opening if used as a solid)."""
	z0 = door.hinge_z
	z1 = door.hinge_z + door.width
	y0 = door.floor_y
	y1 = door.floor_y + door.height
	x = door.wall_x
	pad = _FRAME_AABB_PAD
	return (
		(x - pad, y0 - pad, z0 - pad),
		(x + pad, y1 + pad, z1 + pad),
	)


def _aabb_near(a: Aabb, b: Aabb, eps: float = 0.02) -> bool:
	"""Return True when two AABBs match within ``eps`` on all corners."""
	(a0, a1), (b0, b1) = a, b
	for i in range(3):
		if abs(a0[i] - b0[i]) > eps or abs(a1[i] - b1[i]) > eps:
			return False
	return True


class AcousticScene:
	"""Tracks static room solids and dynamic door panel for ray obstruction."""

	def __init__(self) -> None:
		self._geometry_revision = 0
		self._last_door_angle: float | None = None
		self._level_token: object | None = None

	@property
	def geometry_revision(self) -> int:
		"""Monotonic counter bumped when acoustic geometry changes."""
		return self._geometry_revision

	def notify_level_loaded(self, room: RoomData) -> None:
		"""Reset revision tracking when a new level becomes active."""
		token = id(room)
		if token != self._level_token:
			self._level_token = token
			self._last_door_angle = None
			self._geometry_revision += 1

	def build_occluders(
		self,
		room: RoomData,
		door: InteractableDoor | None,
	) -> tuple[list[Aabb], list[str]]:
		"""Return (occluders, materials) including the door panel when it still blocks.

		The static door *frame* AABB from ``decor_edges(..., occludes=True)`` fills the
		doorway opening and must be excluded so open-door rays can pass through.
		"""
		solids = list(room.occluders)
		materials = list(room.occluder_materials) if room.occluder_materials else []
		# Pad materials list if older rooms omit tags.
		while len(materials) < len(solids):
			materials.append("concrete")

		if door is not None:
			frame = _door_frame_aabb(door)
			kept_solids: list[Aabb] = []
			kept_materials: list[str] = []
			for bounds, material in zip(solids, materials):
				if _aabb_near(bounds, frame):
					continue
				kept_solids.append(bounds)
				kept_materials.append(material)
			solids = kept_solids
			materials = kept_materials

			panel = door.collidable()
			if panel is not None:
				solids.append(panel)
				materials.append(_DOOR_PANEL_MATERIAL)
			self._maybe_bump_door_revision(door.open_angle)

		return solids, materials

	def portal_state(self, door: InteractableDoor | None) -> tuple[float, float]:
		"""Return doorway portal bleed (gain, lpf) from door angle."""
		return door_portal_bleed(door)

	def door_open_frac(self, door: InteractableDoor | None) -> float:
		"""Normalized door open fraction for dynamic RT60 / portal graph."""
		return door_open_fraction(door)

	def boundaries(self, room: RoomData) -> list[BoundaryPlane]:
		"""Return room boundary planes used for acoustic line-of-sight."""
		return list(room.boundaries)

	def _maybe_bump_door_revision(self, open_angle: float) -> None:
		"""Bump revision when the door swings enough to change occlusion."""
		if self._last_door_angle is None:
			self._last_door_angle = open_angle
			return
		if abs(open_angle - self._last_door_angle) >= _DOOR_ANGLE_REVISION_EPS:
			self._last_door_angle = open_angle
			self._geometry_revision += 1
