"""Shared wireframe edge templates for furniture props.

Builders call these for geometry, then register collision / occlusion themselves.
"""

from __future__ import annotations

import math

from math3d import Vec3
from room import Edge

# Axis-aligned bounds as ((min), (max)) — matches occlusion.Aabb without importing it.
AabbLike = tuple[tuple[float, float, float], tuple[float, float, float]]


def _box_edges(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> list[Edge]:
	"""Twelve edges of an axis-aligned box."""
	corners = (
		(x0, y0, z0),
		(x1, y0, z0),
		(x1, y0, z1),
		(x0, y0, z1),
		(x0, y1, z0),
		(x1, y1, z0),
		(x1, y1, z1),
		(x0, y1, z1),
	)
	indices = (
		(0, 1), (1, 2), (2, 3), (3, 0),
		(4, 5), (5, 6), (6, 7), (7, 4),
		(0, 4), (1, 5), (2, 6), (3, 7),
	)
	return [(corners[i0], corners[i1]) for i0, i1 in indices]


def _rotate_y(point: Vec3, origin: Vec3, radians: float) -> Vec3:
	"""Rotate a point around ``origin`` in the XZ plane."""
	dx = point[0] - origin[0]
	dz = point[2] - origin[2]
	c = math.cos(radians)
	s = math.sin(radians)
	return (origin[0] + dx * c - dz * s, point[1], origin[2] + dx * s + dz * c)


def _rotate_edges_y(edges: list[Edge], origin: Vec3, radians: float) -> list[Edge]:
	"""Rotate every segment around ``origin`` about Y."""
	if abs(radians) < 1e-9:
		return list(edges)
	return [
		(_rotate_y(start, origin, radians), _rotate_y(end, origin, radians))
		for start, end in edges
	]


def table_edges(
	x0: float,
	z0: float,
	x1: float,
	z1: float,
	*,
	top_y: float,
	floor_y: float = 0.0,
	leg_inset: float = 0.08,
	top_thickness: float = 0.05,
	leg_size: float = 0.06,
) -> list[Edge]:
	"""Upright table: thin top slab + four corner legs down to ``floor_y``."""
	y_lo = top_y - top_thickness
	edges = _box_edges(x0, y_lo, z0, x1, top_y, z1)
	legs = (
		(x0 + leg_inset, z0 + leg_inset),
		(x1 - leg_inset - leg_size, z0 + leg_inset),
		(x0 + leg_inset, z1 - leg_inset - leg_size),
		(x1 - leg_inset - leg_size, z1 - leg_inset - leg_size),
	)
	for lx, lz in legs:
		edges.extend(_box_edges(lx, floor_y, lz, lx + leg_size, y_lo, lz + leg_size))
	return edges


def _rotate_z(point: Vec3, origin: Vec3, radians: float) -> Vec3:
	"""Rotate a point around ``origin`` in the XY plane."""
	dx = point[0] - origin[0]
	dy = point[1] - origin[1]
	c = math.cos(radians)
	s = math.sin(radians)
	return (origin[0] + dx * c - dy * s, origin[1] + dx * s + dy * c, point[2])


def _rotate_edges_z(edges: list[Edge], origin: Vec3, radians: float) -> list[Edge]:
	"""Rotate every segment around ``origin`` about Z."""
	if abs(radians) < 1e-9:
		return list(edges)
	return [
		(_rotate_z(start, origin, radians), _rotate_z(end, origin, radians))
		for start, end in edges
	]


def _shift_edges_to_floor(edges: list[Edge], floor_y: float = 0.0) -> list[Edge]:
	"""Translate edges so the lowest Y sits on ``floor_y``."""
	min_y = min(min(start[1], end[1]) for start, end in edges)
	dy = floor_y - min_y
	if abs(dy) < 1e-9:
		return edges
	return [
		((s[0], s[1] + dy, s[2]), (e[0], e[1] + dy, e[2]))
		for s, e in edges
	]


def desk_edges(
	x0: float,
	z0: float,
	x1: float,
	z1: float,
	*,
	top_y: float,
	tip_radians: float = 0.0,
	fallen: bool = False,
	drawer_count: int = 2,
	apron_depth: float = 0.18,
	floor_y: float = 0.0,
) -> list[Edge]:
	"""Office desk with apron / drawer lines.

	``tip_radians`` yaws the silhouette in place. ``fallen=True`` tips it onto
	its side (≈90° about Z) and settles it onto ``floor_y``.
	"""
	top_thickness = 0.06
	y_lo = top_y - top_thickness
	edges = _box_edges(x0, y_lo, z0, x1, top_y, z1)

	# Front apron / drawer face under the top (toward -Z).
	apron_z1 = z0 + apron_depth
	edges.extend(_box_edges(x0 + 0.04, y_lo - 0.28, z0 + 0.02, x1 - 0.04, y_lo, apron_z1))
	for index in range(1, max(1, drawer_count)):
		t = index / drawer_count
		y = (y_lo - 0.28) + t * 0.28
		edges.append(((x0 + 0.06, y, z0 + 0.02), (x1 - 0.06, y, z0 + 0.02)))

	leg = 0.05
	inset = 0.07
	for lx, lz in (
		(x0 + inset, z0 + inset),
		(x1 - inset - leg, z0 + inset),
		(x0 + inset, z1 - inset - leg),
		(x1 - inset - leg, z1 - inset - leg),
	):
		edges.extend(_box_edges(lx, floor_y, lz, lx + leg, y_lo - 0.28, lz + leg))

	cx = (x0 + x1) * 0.5
	cz = (z0 + z1) * 0.5
	edges = _rotate_edges_y(edges, (cx, 0.0, cz), tip_radians)
	if fallen:
		# Tip onto the +X side, then drop onto the floor.
		edges = _rotate_edges_z(edges, (cx, top_y * 0.5, cz), math.pi * 0.5)
		edges = _shift_edges_to_floor(edges, floor_y)
	return edges


def filing_cabinet_edges(
	x0: float,
	z0: float,
	x1: float,
	z1: float,
	*,
	height: float = 1.15,
	drawers: int = 3,
	floor_y: float = 0.0,
) -> list[Edge]:
	"""Tall filing cabinet with horizontal drawer splits and short handle stubs."""
	y1 = floor_y + height
	edges = _box_edges(x0, floor_y, z0, x1, y1, z1)
	# Drawer lines across the +X face (room-facing when cabinet sits mid-room).
	face_x = x1
	for index in range(1, drawers):
		y = floor_y + height * (index / drawers)
		edges.append(((face_x, y, z0 + 0.02), (face_x, y, z1 - 0.02)))
	# Handle stubs on each drawer.
	for index in range(drawers):
		y = floor_y + height * ((index + 0.5) / drawers)
		z_mid = (z0 + z1) * 0.5
		edges.append(((face_x, y, z_mid - 0.04), (face_x + 0.03, y, z_mid - 0.04)))
		edges.append(((face_x, y, z_mid + 0.04), (face_x + 0.03, y, z_mid + 0.04)))
		edges.append(((face_x + 0.03, y, z_mid - 0.04), (face_x + 0.03, y, z_mid + 0.04)))
	return edges


def radio_body_edges(
	x: float,
	y: float,
	z: float,
	*,
	width: float,
	height: float,
	depth: float,
	face_axis: str = "-z",
) -> tuple[list[Edge], AabbLike]:
	"""Old radio cabinet + grille. ``face_axis`` is ``+x``, ``-x``, ``+z``, or ``-z``.

	Returns edges and the body AABB.
	"""
	# Local extents: width along the wall-facing span, depth into the room.
	half_w = width * 0.5
	half_d = depth * 0.5
	y0 = y
	y1 = y + height

	if face_axis in ("+x", "-x"):
		# Wide face in Z; depth along X.
		x0, x1 = x - half_d, x + half_d
		z0, z1 = z - half_w, z + half_w
	else:
		x0, x1 = x - half_w, x + half_w
		z0, z1 = z - half_d, z + half_d

	edges = _box_edges(x0, y0, z0, x1, y1, z1)
	margin = 0.03
	grille_w = width - margin * 2.0
	grille_h = height - margin * 2.2
	grille_d = 0.04 / 3.0
	gy0 = y0 + margin
	gy1 = gy0 + grille_h

	if face_axis == "+x":
		edges.extend(
			_box_edges(
				x1, gy0, z - grille_w * 0.5,
				x1 + grille_d, gy1, z + grille_w * 0.5,
			)
		)
	elif face_axis == "-x":
		edges.extend(
			_box_edges(
				x0 - grille_d, gy0, z - grille_w * 0.5,
				x0, gy1, z + grille_w * 0.5,
			)
		)
	elif face_axis == "+z":
		edges.extend(
			_box_edges(
				x - grille_w * 0.5, gy0, z1,
				x + grille_w * 0.5, gy1, z1 + grille_d,
			)
		)
	else:
		# Default: grille on -Z (faces into room from a north table).
		edges.extend(
			_box_edges(
				x - grille_w * 0.5, gy0, z0 - grille_d,
				x + grille_w * 0.5, gy1, z0,
			)
		)

	return edges, ((x0, y0, z0), (x1, y1, z1))


def west_wall_radio_cord(
	back_x: float,
	y0: float,
	z: float,
	wall_x: float,
) -> list[Edge]:
	"""Sagging cord from radio back (−X) to a west-wall outlet."""
	span = max(0.12, back_x - wall_x)
	plug_y = y0 + 0.05
	path: list[Vec3] = [
		(back_x, y0 + 0.11, z),
		(back_x - span * 0.22, y0 + 0.07, z + 0.02),
		(back_x - span * 0.45, y0 + 0.035, z - 0.03),
		(back_x - span * 0.68, y0 + 0.02, z + 0.015),
		(wall_x + 0.04, plug_y, z - 0.01),
		(wall_x, plug_y, z),
	]
	return [(path[index], path[index + 1]) for index in range(len(path) - 1)]


__all__ = [
	"desk_edges",
	"filing_cabinet_edges",
	"radio_body_edges",
	"table_edges",
	"west_wall_radio_cord",
]
