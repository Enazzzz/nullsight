"""Player movement collision against axis-aligned solids."""

from __future__ import annotations

import math

from math3d import Vec3
from occlusion import Aabb

# Feet-circle radius on the XZ plane (matches outer clamp margin).
PLAYER_RADIUS = 0.35
# Body height used to reject tall props the player can walk under (none yet).
PLAYER_BODY_TOP = 1.85
BOUNDARY_MARGIN = 0.35


def _aabb_overlaps_player_height(aabb: Aabb) -> bool:
	"""Return True when a solid intersects the standing player vertical span."""
	lo, hi = aabb
	return lo[1] <= PLAYER_BODY_TOP and hi[1] >= 0.0


def _circle_aabb_penetration(px: float, pz: float, radius: float, aabb: Aabb) -> tuple[float, float] | None:
	"""Return a minimum push vector (dx, dz) to separate circle from AABB, or None."""
	lo, hi = aabb
	closest_x = max(lo[0], min(px, hi[0]))
	closest_z = max(lo[2], min(pz, hi[2]))
	delta_x = px - closest_x
	delta_z = pz - closest_z
	dist_sq = delta_x * delta_x + delta_z * delta_z
	radius_sq = radius * radius

	if dist_sq > radius_sq:
		return None

	if dist_sq > 1e-10:
		dist = math.sqrt(dist_sq)
		push = radius - dist
		return delta_x / dist * push, delta_z / dist * push

	# Circle center inside the AABB footprint — exit along shallowest axis.
	left = px - lo[0]
	right = hi[0] - px
	back = pz - lo[2]
	front = hi[2] - pz
	overlap_x = min(left, right)
	overlap_z = min(back, front)
	if overlap_x < overlap_z:
		return (-overlap_x if left < right else overlap_x), 0.0
	return 0.0, (-overlap_z if back < front else overlap_z)


def _resolve_penetration(px: float, pz: float, radius: float, collidables: list[Aabb]) -> tuple[float, float]:
	"""Push the player circle out of any overlapping collidables."""
	for _ in range(4):
		moved = False
		for aabb in collidables:
			if not _aabb_overlaps_player_height(aabb):
				continue
			push = _circle_aabb_penetration(px, pz, radius, aabb)
			if push is None:
				continue
			px += push[0]
			pz += push[1]
			moved = True
		if not moved:
			break
	return px, pz


def _clamp_bounds(px: float, pz: float, bounds: tuple[float, float, float, float]) -> tuple[float, float]:
	"""Keep the player circle inside the outer room rectangle."""
	min_x, max_x, min_z, max_z = bounds
	margin = BOUNDARY_MARGIN
	px = max(min_x + margin, min(max_x - margin, px))
	pz = max(min_z + margin, min(max_z - margin, pz))
	return px, pz


def resolve_xz_move(
	x: float,
	z: float,
	dx: float,
	dz: float,
	collidables: list[Aabb],
	bounds: tuple[float, float, float, float],
	radius: float = PLAYER_RADIUS,
) -> tuple[float, float]:
	"""Apply a horizontal delta with axis-separated sliding against solids."""
	px, pz = x, z

	if dx != 0.0:
		px += dx
		px, pz = _resolve_penetration(px, pz, radius, collidables)
		px, pz = _clamp_bounds(px, pz, bounds)

	if dz != 0.0:
		pz += dz
		px, pz = _resolve_penetration(px, pz, radius, collidables)
		px, pz = _clamp_bounds(px, pz, bounds)

	return px, pz


def resolve_spawn_position(
	position: Vec3,
	collidables: list[Aabb],
	bounds: tuple[float, float, float, float],
) -> Vec3:
	"""Ensure spawn feet are inside bounds and not embedded in geometry."""
	x, y, z = position
	x, z = _clamp_bounds(x, z, bounds)
	x, z = _resolve_penetration(x, z, PLAYER_RADIUS, collidables)
	return x, y, z
