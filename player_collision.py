"""Player movement collision against axis-aligned solids."""

from __future__ import annotations

import math

from math3d import Vec3
from occlusion import Aabb

# Feet-circle radius on the XZ plane (matches outer clamp margin).
PLAYER_RADIUS = 0.35
# Standing capsule height from feet to head (eye sits below this).
PLAYER_HEIGHT = 1.85
# How high the player can step onto a ledge without jumping.
STEP_HEIGHT = 0.45
# Snap / ground probe tolerance.
GROUND_SNAP = 0.08
BOUNDARY_MARGIN = 0.35


def _circle_overlaps_aabb_xz(px: float, pz: float, radius: float, aabb: Aabb) -> bool:
	"""Return True when the feet circle overlaps an AABB's XZ footprint."""
	lo, hi = aabb
	closest_x = max(lo[0], min(px, hi[0]))
	closest_z = max(lo[2], min(pz, hi[2]))
	dx = px - closest_x
	dz = pz - closest_z
	return dx * dx + dz * dz <= radius * radius


def _aabb_blocks_body(aabb: Aabb, feet_y: float) -> bool:
	"""Return True when a solid intersects the body above the step-up band.

	The lower ``STEP_HEIGHT`` of the capsule ignores solids so the player can
	walk onto stair treads and low ledges without being stopped by their volume.
	"""
	lo, hi = aabb
	body_lo = feet_y + STEP_HEIGHT
	body_hi = feet_y + PLAYER_HEIGHT
	return lo[1] < body_hi and hi[1] > body_lo


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


def _resolve_penetration(
	px: float,
	pz: float,
	feet_y: float,
	radius: float,
	collidables: list[Aabb],
) -> tuple[float, float]:
	"""Push the player circle out of solids that overlap the standing body."""
	for _ in range(4):
		moved = False
		for aabb in collidables:
			if not _aabb_blocks_body(aabb, feet_y):
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


def ground_height_at(
	px: float,
	pz: float,
	walkables: list[Aabb],
	base_floor_y: float,
	*,
	feet_y: float | None = None,
	radius: float = PLAYER_RADIUS,
	max_rise: float = STEP_HEIGHT,
) -> float:
	"""Return the highest authored walkable top under the feet circle.

	Only ``walkables`` count as floors — never furniture or wall tops.
	When ``feet_y`` is set, surfaces above ``feet_y + max_rise`` are ignored.
	"""
	best = base_floor_y
	ceiling = None if feet_y is None else feet_y + max_rise
	for aabb in walkables:
		if not _circle_overlaps_aabb_xz(px, pz, radius, aabb):
			continue
		top = aabb[1][1]
		if top < best - 1e-4:
			continue
		if ceiling is not None and top > ceiling + 1e-4:
			continue
		best = top
	return best


def resolve_xz_move(
	x: float,
	z: float,
	dx: float,
	dz: float,
	collidables: list[Aabb],
	bounds: tuple[float, float, float, float],
	*,
	feet_y: float = 0.0,
	radius: float = PLAYER_RADIUS,
) -> tuple[float, float]:
	"""Apply a horizontal delta with axis-separated sliding against solids."""
	px, pz = x, z

	if dx != 0.0:
		px += dx
		px, pz = _resolve_penetration(px, pz, feet_y, radius, collidables)
		px, pz = _clamp_bounds(px, pz, bounds)

	if dz != 0.0:
		pz += dz
		px, pz = _resolve_penetration(px, pz, feet_y, radius, collidables)
		px, pz = _clamp_bounds(px, pz, bounds)

	return px, pz


def resolve_vertical(
	x: float,
	y: float,
	z: float,
	vy: float,
	dt: float,
	walkables: list[Aabb],
	base_floor_y: float,
	*,
	ceiling_y: float | None = None,
	radius: float = PLAYER_RADIUS,
) -> tuple[float, float, bool]:
	"""Integrate vertical velocity with floor snap and optional ceiling clamp.

	Returns ``(new_feet_y, new_vy, grounded)``.
	"""
	new_y = y + vy * dt
	probe = max(y, new_y)
	ground = ground_height_at(
		x,
		z,
		walkables,
		base_floor_y,
		feet_y=probe,
		radius=radius,
		max_rise=STEP_HEIGHT,
	)

	grounded = False

	if ceiling_y is not None:
		head = new_y + PLAYER_HEIGHT
		if head > ceiling_y:
			new_y = ceiling_y - PLAYER_HEIGHT
			if vy > 0.0:
				vy = 0.0

	if new_y <= ground + GROUND_SNAP and vy <= 0.0:
		new_y = ground
		vy = 0.0
		grounded = True
	else:
		grounded = False

	return new_y, vy, grounded


def resolve_spawn_position(
	position: Vec3,
	collidables: list[Aabb],
	bounds: tuple[float, float, float, float],
	*,
	base_floor_y: float | None = None,
	walkables: list[Aabb] | None = None,
) -> Vec3:
	"""Ensure spawn feet are inside bounds, clear of walls, and on a walkable."""
	x, y, z = position
	floor = base_floor_y if base_floor_y is not None else min(y, 0.0)
	floors = walkables if walkables is not None else []
	x, z = _clamp_bounds(x, z, bounds)
	x, z = _resolve_penetration(x, z, y, PLAYER_RADIUS, collidables)
	ground = ground_height_at(x, z, floors, floor, feet_y=None)
	return x, ground, z
