"""Room geometry types shared by level authoring and rendering."""

from dataclasses import dataclass, field

from math3d import Vec3
from occlusion import Aabb, BoundaryPlane

Edge = tuple[Vec3, Vec3]
OccluderHull = tuple[Aabb, list[Edge]]


@dataclass(frozen=True)
class RoomData:
	"""Complete level payload for gameplay and scanning."""

	environment_edges: list[Edge]
	occluder_hulls: list[OccluderHull]
	bounds: tuple[float, float, float, float]
	occluders: list[Aabb]
	# Horizontal blockers (walls, props, rim walls) — not used as floors.
	collidables: list[Aabb]
	boundaries: list[BoundaryPlane]
	spawn: Vec3
	name: str
	# Parallel to ``occluders`` — acoustic material names (concrete, drywall, …).
	occluder_materials: list[str] = field(default_factory=list)
	# Room-shell material for Sabine/Eyring RT60 (walls/floor/ceiling).
	shell_material: str = "concrete"
	# Lowest fallback floor and ceiling for gravity / jump.
	floor_y: float = 0.0
	ceiling_y: float = 4.0
	# Explicit walkable tops (deck plates, stairs, pit floor). Empty → use floor_y only.
	walkables: list[Aabb] = field(default_factory=list)
	# Authored editor metadata (Facility room JSON).
	authored_sounds: list[dict] = field(default_factory=list)
	audio_profile: dict = field(default_factory=dict)
	entities: list[dict] = field(default_factory=list)
	connections: list[str] = field(default_factory=list)
