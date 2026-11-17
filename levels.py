"""Level layouts built with ``LevelBuilder``."""

from pathlib import Path

from math3d import Vec3
from level_builder import (
	AWAKENING_RADIO_POS,
	AWAKENING_RADIO_SHELF_TOP,
	LevelBuilder,
	SMALL_TABLE_BOUNDS,
	SMALL_TABLE_RADIO_POS,
)
from level_format import LevelDocument, default_levels_dir
from interactable_door import InteractableDoor
from room import Edge, OccluderHull, RoomData
from occlusion import Aabb, BoundaryPlane

# East divider doorway in the small connected layout.
_SMALL_DOOR_Z0 = 2.5
_SMALL_DOOR_Z1 = 3.5
_SMALL_DOOR_HEIGHT = 2.15

def build_small_level() -> tuple[RoomData, InteractableDoor]:
	"""12×12 entry room plus 12×12 east annex, linked by a hinged door.

	Dense bunker: thick concrete shell/dividers, wood furniture, metal crates,
	brick stub wall, drywall niche, carpet rug, glass cabinet.
	"""
	table_x0, table_y0, table_z0, table_x1, table_y1, table_z1 = SMALL_TABLE_BOUNDS
	floor = 0.0
	ceiling = 4.0
	door = InteractableDoor.east_wall(
		room_extent=12.0,
		z_center=(_SMALL_DOOR_Z0 + _SMALL_DOOR_Z1) * 0.5,
		width=_SMALL_DOOR_Z1 - _SMALL_DOOR_Z0,
		height=_SMALL_DOOR_HEIGHT,
		floor_y=floor,
	)
	room = (
		LevelBuilder(24.0, 12.0, floor_y=floor, ceiling_y=ceiling)
		.name("small")
		.shell_material("concrete")
		.spawn_at(6.0, 6.0)
		.outer_shell()
		# Shared divider — thick concrete bunker wall around the doorway.
		.divider_wall_x(12.0, 0.0, _SMALL_DOOR_Z0, material="concrete")
		.divider_wall_x(12.0, _SMALL_DOOR_Z1, 12.0, material="concrete")
		.divider_wall_x(
			12.0, _SMALL_DOOR_Z0, _SMALL_DOOR_Z1,
			y0=_SMALL_DOOR_HEIGHT, y1=ceiling, material="concrete",
		)
		# West cell — structural + furniture.
		.pillar(8.0, 3.0, material="concrete")
		.box(table_x0, table_y0, table_z0, table_x1, table_y1, table_z1, material="wood")
		.table_radio(SMALL_TABLE_RADIO_POS[0], table_y1, SMALL_TABLE_RADIO_POS[2])
		# Soft rug under the table (absorbs mid/HF; low collision slab).
		.box(1.2, floor, 10.6, 4.3, 0.04, 11.9, material="carpet", collidable=False)
		# Wood chair / stool near spawn.
		.box(4.5, floor, 5.0, 5.2, 0.55, 5.7, material="wood")
		# Glass display cabinet (thin, reflective) along south wall.
		.box(7.0, floor, 0.15, 9.5, 1.8, 0.35, material="glass")
		# Curtain strip on west wall (soft HF absorber).
		.box(0.05, 1.0, 4.0, 0.12, 3.2, 7.0, material="curtain", collidable=False)
		# East annex — denser mixed materials.
		.pillar(16.0, 3.0, material="brick")
		.box(18.0, floor, 7.0, 21.0, 1.0, 10.0, material="metal")
		# Second metal shelf / locker.
		.box(14.5, floor, 9.5, 16.0, 2.2, 11.5, material="metal")
		# Drywall office niche (lighter partition timbre).
		.box(20.5, floor, 1.0, 23.5, 2.4, 1.25, material="drywall")
		# Brick stub wall for scatter variety.
		.box(15.0, floor, 5.5, 17.5, 1.4, 5.85, material="brick")
		# Wood workbench.
		.box(21.0, floor, 4.0, 23.2, 0.85, 6.5, material="wood")
		# Plaster-finished low plinth.
		.box(13.5, floor, 0.2, 15.0, 0.45, 2.0, material="plaster")
		.decor_edges(door.frame_edges, occludes=True, material="metal_door")
		.build()
	)
	return room, door


def build_small_level_room() -> RoomData:
	"""Room only — for legacy call sites."""
	return build_small_level()[0]


def build_large_level() -> RoomData:
	"""40×40 warehouse — dense props with correct material tags."""
	floor = 0.0
	ceiling = 4.0

	builder = (
		LevelBuilder(40.0, 40.0, floor_y=floor, ceiling_y=ceiling)
		.name("large")
		.shell_material("concrete")
		.spawn_at(4.0, 4.0)
		.outer_shell()
		# Solid corridor walls — thick concrete bunker slabs.
		.wall_x(14.0, 0.0, 16.0, material="concrete")
		.wall_x(14.0, 20.0, 40.0, material="concrete")
		.wall_x(26.0, 8.0, 32.0, material="concrete")
		.wall_z(18.0, 0.0, 22.0, material="concrete")
		.wall_z(18.0, 28.0, 40.0, material="concrete")
		.wall_z(30.0, 10.0, 30.0, material="concrete")
	)

	# Structural columns — alternate concrete / brick for timbre variety.
	for i, px in enumerate(range(8, 33, 8)):
		for j, pz in enumerate(range(8, 33, 8)):
			if abs(px - 14.0) < 0.6 and pz < 18.0:
				continue
			col_mat = "brick" if (i + j) % 3 == 0 else "concrete"
			builder.pillar(float(px), float(pz), material=col_mat)

	# Furniture / crates — wood, metal, drywall, glass, carpet, plaster.
	builder.box(2.0, floor, 2.0, 5.0, 1.0, 4.5, material="wood")
	builder.box(32.0, floor, 4.0, 36.5, 0.8, 7.0, material="metal")
	builder.box(4.0, floor, 32.0, 8.0, 1.2, 35.0, material="wood")
	builder.box(30.0, floor, 30.0, 34.0, 0.7, 33.5, material="metal")
	builder.box(18.0, floor, 22.0, 22.0, 1.5, 25.0, material="wood")
	builder.box(22.0, floor, 34.0, 27.0, 0.9, 37.0, material="drywall")
	# Extra density — warehouse clutter.
	builder.box(6.0, floor, 14.0, 9.0, 1.1, 16.5, material="metal")
	builder.box(10.0, floor, 22.0, 12.5, 0.6, 24.0, material="wood")
	builder.box(28.0, floor, 12.0, 31.0, 2.0, 14.0, material="metal")
	builder.box(15.5, floor, 34.0, 17.5, 1.8, 38.0, material="brick")
	builder.box(35.0, floor, 20.0, 38.0, 0.9, 23.0, material="wood")
	builder.box(1.5, floor, 18.0, 3.5, 2.5, 20.0, material="drywall")
	# Glass office partition (thin reflective).
	builder.box(20.0, floor, 8.0, 24.0, 2.2, 8.2, material="glass")
	# Carpeted break area (soft absorption).
	builder.box(3.0, floor, 8.0, 7.0, 0.05, 11.0, material="carpet", collidable=False)
	# Plaster-finished low wall / plinth.
	builder.box(24.0, floor, 2.0, 28.0, 0.5, 3.5, material="plaster")
	# Curtain bay (soft HF).
	builder.box(38.5, 0.5, 28.0, 38.9, 3.5, 34.0, material="curtain", collidable=False)
	# Stacked wood pallets.
	builder.box(12.0, floor, 2.0, 14.0, 0.4, 4.0, material="wood")
	builder.box(12.0, 0.4, 2.0, 14.0, 0.8, 4.0, material="wood")
	# Steel rack.
	builder.box(33.0, floor, 34.0, 38.0, 2.4, 35.2, material="metal")

	return builder.build()


# Awakening / First room — main deck at y=0; sunken bay is a hole with stairs.
_AWK_WIDTH = 14.0
_AWK_DEPTH = 12.0
_AWK_PIT_Y = -1.2
_AWK_DECK_Y = 0.0
_AWK_CEILING = 3.6
_AWK_PIT_X0, _AWK_PIT_X1 = 8.0, 13.5
_AWK_PIT_Z0, _AWK_PIT_Z1 = 0.5, 4.0
_AWK_DOOR_Z0, _AWK_DOOR_Z1 = 8.5, 9.5
_AWK_DOOR_HEIGHT = 2.15
_AWK_DECK_THICK = 0.12
_AWK_PIT_WALL = 0.12


def _awakening_floors(builder: LevelBuilder) -> None:
	"""Deck plates around the bay, pit floor, stairs, rim walls, and floor grids."""
	deck = _AWK_DECK_Y
	pit_y = _AWK_PIT_Y
	thick = _AWK_DECK_THICK
	w, d = _AWK_WIDTH, _AWK_DEPTH
	x0, x1 = _AWK_PIT_X0, _AWK_PIT_X1
	z0, z1 = _AWK_PIT_Z0, _AWK_PIT_Z1

	# Main deck plates (walkable; no scan occluders — grids supply lines).
	builder.walkable_plate(0.0, 0.0, x0, d, top_y=deck, thickness=thick)
	builder.walkable_plate(x1, 0.0, w, d, top_y=deck, thickness=thick)
	builder.walkable_plate(x0, z1, x1, d, top_y=deck, thickness=thick)
	builder.walkable_plate(x0, 0.0, x1, z0, top_y=deck, thickness=thick)
	# Pit floor.
	builder.walkable_plate(x0, z0, x1, z1, top_y=pit_y, thickness=0.08)

	# Stairs: walkable tops + top-face wire only (full boxes densify into lag).
	steps = (
		(-0.9, 3.10, 3.50),
		(-0.6, 3.50, 3.90),
		(-0.3, 3.90, 4.30),
	)
	step_thick = 0.14
	for top_y, z_lo, z_hi in steps:
		builder.box(
			x0, top_y - step_thick, z_lo, x1, top_y, z_hi,
			walkable=True, occludes=False, collidable=False, material="concrete",
		)
		# Top rectangle only — enough to read the tread in a scan.
		builder.environment_box(x0, top_y, z_lo, x1, top_y, z_hi)

	# Rim walls — collision only (under the deck lip; no scan occluders).
	rw = _AWK_PIT_WALL
	builder.box(
		x0 - rw, pit_y, z0, x0, deck, z1,
		occludes=False, collidable=True, material="concrete",
	)
	builder.box(
		x1, pit_y, z0, x1 + rw, deck, z1,
		occludes=False, collidable=True, material="concrete",
	)
	builder.box(
		x0, pit_y, z0 - rw, x1, deck, z0,
		occludes=False, collidable=True, material="concrete",
	)
	# Thin visible rim outline so the hole edge reads in scans.
	builder.environment_box(x0, deck, z0, x0 + 0.02, deck, z1)
	builder.environment_box(x1 - 0.02, deck, z0, x1, deck, z1)
	builder.environment_box(x0, deck, z0, x1, deck, z0 + 0.02)

	# Scanable floor grids (undensified — densify explodes scan bake cost).
	builder.floor_grid(deck, 0.0, 0.0, x0, d)
	builder.floor_grid(deck, x1, 0.0, w, d)
	builder.floor_grid(deck, x0, z1, x1, d)
	builder.floor_grid(deck, x0, 0.0, x1, z0)
	builder.floor_grid(pit_y, x0, z0, x1, z1)


def build_awakening_room() -> tuple[RoomData, InteractableDoor]:
	"""Threshold / Wake — first room from the whiteboard (real stairs + bay).

	Top of the board = +Z. West wall door, north-wall window, center-left pillar,
	radio on the west wall, tipped furniture mid-right, spawn in the SE sunken bay.
	Shell is the main deck; the bay is a hole with its own walkable floor.
	"""
	door = InteractableDoor.west_wall(
		z_center=(_AWK_DOOR_Z0 + _AWK_DOOR_Z1) * 0.5,
		width=_AWK_DOOR_Z1 - _AWK_DOOR_Z0,
		height=_AWK_DOOR_HEIGHT,
		floor_y=_AWK_DECK_Y,
	)
	builder = (
		LevelBuilder(
			_AWK_WIDTH,
			_AWK_DEPTH,
			floor_y=_AWK_DECK_Y,
			ceiling_y=_AWK_CEILING,
			# 1 keeps wall/ceiling grids readable without densify cost.
			edge_subdivisions=1,
		)
		.name("awakening")
		.shell_material("concrete")
		.gravity_floor(_AWK_PIT_Y)
		.spawn_at(10.5, 2.0, y=_AWK_PIT_Y)
		.outer_shell(floor=False)
	)
	_awakening_floors(builder)
	# West-wall doorway lintel / jambs.
	builder.divider_wall_x(0.0, 0.0, _AWK_DOOR_Z0, y0=_AWK_DECK_Y, material="concrete")
	builder.divider_wall_x(0.0, _AWK_DOOR_Z1, _AWK_DEPTH, y0=_AWK_DECK_Y, material="concrete")
	builder.divider_wall_x(
		0.0, _AWK_DOOR_Z0, _AWK_DOOR_Z1,
		y0=_AWK_DOOR_HEIGHT, y1=_AWK_CEILING, material="concrete",
	)
	# North-wall window to hallway (see-through glass band).
	builder.box(
		5.0, 1.15, _AWK_DEPTH - 0.12,
		8.5, 2.45, _AWK_DEPTH - 0.02,
		see_through=True, material="glass",
	)
	# Center-left structural pillar.
	builder.box(4.0, _AWK_DECK_Y, 5.2, 5.0, _AWK_CEILING, 6.2, material="concrete")
	# West-wall radio shelf + radio (below the door).
	# Audio spawn is body center; geometry sits on the shelf top.
	rx, _ry, rz = AWAKENING_RADIO_POS
	shelf_top = AWAKENING_RADIO_SHELF_TOP
	builder.box(0.15, shelf_top - 0.10, rz - 0.35, 1.15, shelf_top, rz + 0.35, material="wood")
	builder.table_radio(rx, shelf_top, rz, wall_x=0.0)
	# North-side upright table (was a solid metal box).
	builder.table(10.5, 8.5, 12.2, 10.2, top_y=0.75, material="wood")
	# NE alcove notch.
	builder.wall_x(12.0, 10.0, _AWK_DEPTH, material="concrete")
	builder.wall_z(10.0, 12.0, _AWK_WIDTH, material="concrete")
	builder.decor_edges(door.frame_edges, occludes=True, material="metal_door")
	return builder.build(), door

def load_world() -> tuple[RoomData, InteractableDoor]:
	"""Load the contiguous world used at game start.

	Prefers ``Facility/rooms/<start>.json``, then legacy ``levels/awakening.json``,
	then the hand-authored Python builder.
	"""
	from facility_io import FacilityDocument, ensure_facility_dirs, migrate_legacy_awakening

	ensure_facility_dirs()
	migrate_legacy_awakening()
	try:
		facility = FacilityDocument.load()
		room_doc = facility.load_start_room()
		room, door = room_doc.build()
		if door is None:
			raise RuntimeError(f"{room_doc.room_id} is missing a door block")
		return room, door
	except FileNotFoundError:
		pass

	json_path = default_levels_dir() / "awakening.json"
	if json_path.is_file():
		room, door = LevelDocument.load(json_path).build()
		if door is None:
			raise RuntimeError(f"{json_path} is missing a door block")
		return room, door
	return build_awakening_room()


def load_level(name: str) -> RoomData:
	"""Load a named layout (dev/bench only — not exposed to players)."""
	from facility_io import RoomDocument, rooms_dir

	facility_path = rooms_dir() / f"{name}.json"
	if facility_path.is_file():
		return RoomDocument.load(facility_path).build()[0]
	json_path = default_levels_dir() / f"{name}.json"
	if json_path.is_file():
		return LevelDocument.load(json_path).build()[0]
	if name == "large":
		return build_large_level()
	if name == "awakening":
		return build_awakening_room()[0]
	if name in ("small", "test", "default"):
		return build_small_level_room()
	raise ValueError(f"Unknown level: {name!r}. Use 'awakening', 'small', or 'large'.")


def load_room_file(path: str | Path) -> tuple[RoomData, InteractableDoor | None]:
	"""Load a Facility room JSON by path (playtest / editor)."""
	from facility_io import RoomDocument

	return RoomDocument.load(Path(path)).build()


def build_test_room() -> tuple[
	list[Edge],
	list[OccluderHull],
	tuple[float, float, float, float],
	list[Aabb],
	list[BoundaryPlane],
]:
	"""Legacy tuple return for older call sites."""
	room = build_awakening_room()[0]
	return (
		room.environment_edges,
		room.occluder_hulls,
		room.bounds,
		room.occluders,
		room.boundaries,
	)


def default_spawn() -> Vec3:
	"""Player start in the Awakening Room sunken bay."""
	return 10.5, _AWK_PIT_Y, 2.0


def count_edges(room: RoomData) -> int:
	"""Total scanable edge count."""
	total = len(room.environment_edges)
	for _, edges in room.occluder_hulls:
		total += len(edges)
	return total
