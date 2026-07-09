"""Declarative level authoring — solid walls get automatic occlusion.

Example::

	from level_builder import LevelBuilder

	room = (
		LevelBuilder(24, 18, ceiling_y=4.0)
		.name("my_map")
		.spawn_at(2.0, 2.0)
		.outer_shell()                    # outer walls use boundary planes
		.wall_x(12.0, 0.0, 18.0)         # solid divider — blocks scans
		.wall_z(9.0, 12.0, 18.0, see_through=True)  # glass / water — no occlusion
		.pillar(6.0, 6.0)
		.box(16.0, 0.0, 4.0, 19.0, 1.0, 7.0)
		.build()
	)

Every call to ``wall_x``, ``wall_z``, ``box``, or ``pillar`` registers a thin
occluder hull so bake-time and live scan occlusion both work. Pass
``see_through=True`` only for glass, water, and other non-occluding props.
Solids are **collidable** by default; pass ``collidable=False`` to allow
walking through while still blocking scans.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from math3d import Vec3, subdivide_edges
from occlusion import Aabb, BoundaryPlane
from room import Edge, OccluderHull, RoomData

# Wall slab thickness for interior solid panels (bunker-grade; ≥150 mm → τ≈0).
WALL_THICKNESS = 0.35
EDGE_SUBDIVISIONS = 4
GRID_SPACING = 1.0

# Default acoustic material names (must match acoustics.materials presets).
DEFAULT_WALL_MATERIAL = "concrete"
DEFAULT_PILLAR_MATERIAL = "concrete"
DEFAULT_PROP_MATERIAL = "wood"
DEFAULT_DECOR_MATERIAL = "metal"

# Small room — low table along the west wall and radio placement on top.
SMALL_TABLE_BOUNDS = (1.0, 0.0, 11.05, 4.5, 0.6, 11.85)
SMALL_TABLE_RADIO_POS = (2.55, 0.73, 11.45)


def _box_edges(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> list[Edge]:
	"""Build the twelve edges of an axis-aligned box."""
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
		(0, 1),
		(1, 2),
		(2, 3),
		(3, 0),
		(4, 5),
		(5, 6),
		(6, 7),
		(7, 4),
		(0, 4),
		(1, 5),
		(2, 6),
		(3, 7),
	)
	return [(corners[i0], corners[i1]) for i0, i1 in indices]


def _grid_floor(y: float, x0: float, z0: float, x1: float, z1: float, spacing: float) -> list[Edge]:
	"""Floor or ceiling grid."""
	edges: list[Edge] = []
	x = x0
	while x <= x1 + 1e-6:
		edges.append(((x, y, z0), (x, y, z1)))
		x += spacing
	z = z0
	while z <= z1 + 1e-6:
		edges.append(((x0, y, z), (x1, y, z)))
		z += spacing
	return edges


def _grid_wall_x(x: float, y0: float, z0: float, y1: float, z1: float, spacing: float) -> list[Edge]:
	"""Grid on a wall perpendicular to X."""
	edges: list[Edge] = []
	y = y0
	while y <= y1 + 1e-6:
		edges.append(((x, y, z0), (x, y, z1)))
		y += spacing
	z = z0
	while z <= z1 + 1e-6:
		edges.append(((x, y0, z), (x, y1, z)))
		z += spacing
	return edges


def _grid_wall_z(z: float, x0: float, y0: float, x1: float, y1: float, spacing: float) -> list[Edge]:
	"""Grid on a wall perpendicular to Z."""
	edges: list[Edge] = []
	y = y0
	while y <= y1 + 1e-6:
		edges.append(((x0, y, z), (x1, y, z)))
		y += spacing
	x = x0
	while x <= x1 + 1e-6:
		edges.append(((x, y0, z), (x, y1, z)))
		x += spacing
	return edges


def _densify(edges: list[Edge], subdivisions: int = EDGE_SUBDIVISIONS) -> list[Edge]:
	"""Subdivide edges for a denser wireframe."""
	return subdivide_edges(edges, subdivisions)


def _edges_aabb(edges: list[Edge], *, pad: float = 0.04) -> Aabb:
	"""Tight axis-aligned bounds around a set of wire segments."""
	xs: list[float] = []
	ys: list[float] = []
	zs: list[float] = []
	for start, end in edges:
		for point in (start, end):
			xs.append(point[0])
			ys.append(point[1])
			zs.append(point[2])
	return (
		(min(xs) - pad, min(ys) - pad, min(zs) - pad),
		(max(xs) + pad, max(ys) + pad, max(zs) + pad),
	)


def _radio_power_cord(
	x: float,
	table_y: float,
	back_z: float,
	wall_z: float,
) -> list[Edge]:
	"""Sagging power cord from the radio back to a north-wall outlet."""
	span = max(0.15, wall_z - back_z)
	plug_y = table_y + 0.05
	# Control points — gentle droop and slight side-to-side slack, not a straight line.
	path: list[Vec3] = [
		(x, table_y + 0.11, back_z),
		(x + 0.025, table_y + 0.07, back_z + span * 0.18),
		(x - 0.035, table_y + 0.04, back_z + span * 0.38),
		(x + 0.02, table_y + 0.02, back_z + span * 0.58),
		(x - 0.015, plug_y, back_z + span * 0.78),
		(x + 0.01, plug_y - 0.01, wall_z - 0.06),
		(x, plug_y, wall_z),
	]
	return [(path[index], path[index + 1]) for index in range(len(path) - 1)]


def _boundaries_for_box(
	min_x: float,
	max_x: float,
	floor_y: float,
	ceiling_y: float,
	min_z: float,
	max_z: float,
) -> list[BoundaryPlane]:
	"""Outer room boundary planes (infinite slabs at shell faces)."""
	return [
		BoundaryPlane(0, min_x),
		BoundaryPlane(0, max_x),
		BoundaryPlane(1, floor_y),
		BoundaryPlane(1, ceiling_y),
		BoundaryPlane(2, min_z),
		BoundaryPlane(2, max_z),
	]


@dataclass
class LevelBuilder:
	"""Fluent API for rooms: every solid wall is an occluder hull automatically."""

	width: float
	depth: float
	floor_y: float = 0.0
	ceiling_y: float = 4.0
	grid_spacing: float = GRID_SPACING
	edge_subdivisions: int = EDGE_SUBDIVISIONS
	wall_thickness: float = WALL_THICKNESS

	_spawn: Vec3 | None = None
	_name: str = "level"
	_environment_edges: list[Edge] = field(default_factory=list)
	_occluder_hulls: list[OccluderHull] = field(default_factory=list)
	_occluders: list[Aabb] = field(default_factory=list)
	_occluder_materials: list[str] = field(default_factory=list)
	# Room-shell material for Sabine/Eyring (walls/floor/ceiling).
	_shell_material: str = "concrete"
	_collidables: list[Aabb] = field(default_factory=list)

	@property
	def min_x(self) -> float:
		return 0.0

	@property
	def max_x(self) -> float:
		return self.width

	@property
	def min_z(self) -> float:
		return 0.0

	@property
	def max_z(self) -> float:
		return self.depth

	def spawn_at(self, x: float, z: float, y: float = 0.0) -> LevelBuilder:
		"""Set player spawn on the XZ floor plane."""
		self._spawn = (x, y, z)
		return self

	def name(self, value: str) -> LevelBuilder:
		"""Set level identifier."""
		self._name = value
		return self

	def shell_material(self, material: str) -> LevelBuilder:
		"""Set acoustic material for the room shell (Sabine/Eyring RT60)."""
		self._shell_material = material
		return self

	def divider_wall_x(
		self,
		x: float,
		z0: float,
		z1: float,
		*,
		y0: float | None = None,
		y1: float | None = None,
		material: str = DEFAULT_WALL_MATERIAL,
	) -> LevelBuilder:
		"""Solid interior wall segment on constant X — blocks scans and movement."""
		return self.wall_x(x, z0, z1, y0=y0, y1=y1, material=material)

	def outer_shell(self) -> LevelBuilder:
		"""Add outer room wireframe (shell occlusion uses boundary planes)."""
		x0, x1 = self.min_x, self.max_x
		z0, z1 = self.min_z, self.max_z
		y0, y1 = self.floor_y, self.ceiling_y
		sp = self.grid_spacing

		edges: list[Edge] = []
		edges.extend(_box_edges(x0, y0, z0, x1, y1, z1))
		edges.extend(_grid_floor(y0, x0, z0, x1, z1, sp))
		edges.extend(_grid_floor(y1, x0, z0, x1, z1, sp))
		edges.extend(_grid_wall_x(x0, y0, z0, y1, z1, sp))
		edges.extend(_grid_wall_x(x1, y0, z0, y1, z1, sp))
		edges.extend(_grid_wall_z(z0, x0, y0, x1, y1, sp))
		edges.extend(_grid_wall_z(z1, x0, y0, x1, y1, sp))
		self._environment_edges.extend(_densify(edges, self.edge_subdivisions))
		return self

	def wall_x(
		self,
		x: float,
		z0: float,
		z1: float,
		*,
		y0: float | None = None,
		y1: float | None = None,
		see_through: bool = False,
		collidable: bool = True,
		material: str = DEFAULT_WALL_MATERIAL,
	) -> LevelBuilder:
		"""Wall panel on constant X. ``see_through`` skips occlusion (glass/water only)."""
		y0 = self.floor_y if y0 is None else y0
		y1 = self.ceiling_y if y1 is None else y1
		z_lo, z_hi = min(z0, z1), max(z0, z1)
		edges = _grid_wall_x(x, y0, z_lo, y1, z_hi, self.grid_spacing)

		if see_through:
			self._environment_edges.extend(_densify(edges, self.edge_subdivisions))
			return self

		half = self.wall_thickness * 0.5
		bounds: Aabb = ((x - half, y0, z_lo), (x + half, y1, z_hi))
		self._add_solid(bounds, edges, collidable=collidable, material=material)
		return self

	def wall_z(
		self,
		z: float,
		x0: float,
		x1: float,
		*,
		y0: float | None = None,
		y1: float | None = None,
		see_through: bool = False,
		collidable: bool = True,
		material: str = DEFAULT_WALL_MATERIAL,
	) -> LevelBuilder:
		"""Wall panel on constant Z. ``see_through`` skips occlusion (glass/water only)."""
		y0 = self.floor_y if y0 is None else y0
		y1 = self.ceiling_y if y1 is None else y1
		x_lo, x_hi = min(x0, x1), max(x0, x1)
		edges = _grid_wall_z(z, x_lo, y0, x_hi, y1, self.grid_spacing)

		if see_through:
			self._environment_edges.extend(_densify(edges, self.edge_subdivisions))
			return self

		half = self.wall_thickness * 0.5
		bounds: Aabb = ((x_lo, y0, z - half), (x_hi, y1, z + half))
		self._add_solid(bounds, edges, collidable=collidable, material=material)
		return self

	def box(
		self,
		x0: float,
		y0: float,
		z0: float,
		x1: float,
		y1: float,
		z1: float,
		*,
		see_through: bool = False,
		collidable: bool = True,
		material: str = DEFAULT_PROP_MATERIAL,
	) -> LevelBuilder:
		"""Axis-aligned box. ``see_through`` skips occlusion (glass/water only)."""
		x_lo, x_hi = min(x0, x1), max(x0, x1)
		y_lo, y_hi = min(y0, y1), max(y0, y1)
		z_lo, z_hi = min(z0, z1), max(z0, z1)
		edges = _box_edges(x_lo, y_lo, z_lo, x_hi, y_hi, z_hi)

		if see_through:
			self._environment_edges.extend(_densify(edges, self.edge_subdivisions))
			return self

		bounds: Aabb = ((x_lo, y_lo, z_lo), (x_hi, y_hi, z_hi))
		self._add_solid(bounds, edges, collidable=collidable, material=material)
		return self

	def decor_edges(
		self,
		edges: list[Edge],
		*,
		occludes: bool = False,
		material: str = DEFAULT_DECOR_MATERIAL,
	) -> LevelBuilder:
		"""Add decorative wire segments. Set ``occludes=True`` for thin scanned solids."""
		dense = _densify(edges, self.edge_subdivisions)
		self._environment_edges.extend(dense)
		if occludes:
			bounds = _edges_aabb(dense)
			self._occluder_hulls.append((bounds, dense))
			self._occluders.append(bounds)
			self._occluder_materials.append(material)
		return self

	def pillar(
		self,
		x: float,
		z: float,
		size: float = 1.2,
		*,
		collidable: bool = True,
		material: str = DEFAULT_PILLAR_MATERIAL,
	) -> LevelBuilder:
		"""Short-hand floor-to-ceiling column."""
		return self.box(
			x,
			self.floor_y,
			z,
			x + size,
			self.ceiling_y,
			z + size,
			collidable=collidable,
			material=material,
		)

	def table_radio(self, x: float, y: float, z: float, *, wall_z: float | None = None) -> LevelBuilder:
		"""Wireframe old radio on a surface with grille, power cord, and wall outlet."""
		body_w, body_h, body_d = 0.40, 0.26, 0.30
		x0 = x - body_w * 0.5
		y0 = y
		z0 = z - body_d * 0.5
		x1 = x + body_w * 0.5
		y1 = y + body_h
		z1 = z + body_d * 0.5
		self.box(x0, y0, z0, x1, y1, z1, see_through=True)

		margin = 0.03
		grille_w = body_w - margin * 2.0
		grille_h = body_h - margin * 2.2
		grille_d = 0.04 / 3.0
		# Grille on the -Z face so the radio faces into the room.
		self.box(
			x - grille_w * 0.5,
			y0 + margin,
			z0 - grille_d,
			x + grille_w * 0.5,
			y0 + margin + grille_h,
			z0,
			see_through=True,
		)

		north_wall_z = self.max_z if wall_z is None else wall_z
		cord_edges = _radio_power_cord(x, y0, z1, north_wall_z)
		self.decor_edges(cord_edges)

		outlet_w, outlet_h, outlet_d = 0.07, 0.05, 0.02
		outlet_y = y0 + 0.05
		self.box(
			x - outlet_w * 0.5,
			outlet_y,
			north_wall_z - outlet_d,
			x + outlet_w * 0.5,
			outlet_y + outlet_h,
			north_wall_z,
			see_through=True,
		)
		return self

	def _add_solid(
		self,
		bounds: Aabb,
		wire_edges: list[Edge],
		*,
		collidable: bool = True,
		material: str = DEFAULT_WALL_MATERIAL,
	) -> None:
		"""Register a solid: scan wireframe + bake/live occlusion hull + material."""
		dense = _densify(wire_edges, self.edge_subdivisions)
		self._occluder_hulls.append((bounds, dense))
		self._occluders.append(bounds)
		self._occluder_materials.append(material)
		if collidable:
			self._collidables.append(bounds)

	def build(self) -> RoomData:
		"""Finalize level data for gameplay and scanning."""
		spawn = self._spawn
		if spawn is None:
			spawn = (self.width * 0.5, 0.0, self.depth * 0.5)

		return RoomData(
			environment_edges=list(self._environment_edges),
			occluder_hulls=list(self._occluder_hulls),
			bounds=(self.min_x, self.max_x, self.min_z, self.max_z),
			occluders=list(self._occluders),
			collidables=list(self._collidables),
			boundaries=_boundaries_for_box(
				self.min_x,
				self.max_x,
				self.floor_y,
				self.ceiling_y,
				self.min_z,
				self.max_z,
			),
			spawn=spawn,
			name=self._name,
			occluder_materials=list(self._occluder_materials),
			shell_material=self._shell_material,
		)
