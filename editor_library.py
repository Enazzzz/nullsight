"""Object library for the NULLSIGHT level editor — placeable catalog entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LibraryItem:
	"""One placeable from the Environment / Gameplay / Audio / Entity catalogs."""

	id: str
	category: str
	label: str
	kind: str
	# Default footprint half-size used when placing (meters).
	default_size: float = 1.0


# Curated library matching the requirements doc.
LIBRARY: list[LibraryItem] = [
	# Environment
	LibraryItem("wall_x", "Environment", "Wall X", "wall_x", 2.0),
	LibraryItem("wall_z", "Environment", "Wall Z", "wall_z", 2.0),
	LibraryItem("floor_plate", "Environment", "Floor plate", "walkable_plate", 2.0),
	LibraryItem("ceiling_box", "Environment", "Ceiling slab", "box", 2.0),
	LibraryItem("pillar", "Environment", "Pillar", "pillar", 1.2),
	LibraryItem("pipe_box", "Environment", "Pipe run", "box", 1.5),
	LibraryItem("machinery", "Environment", "Machinery", "box", 1.8),
	LibraryItem("table", "Environment", "Table", "table", 1.0),
	LibraryItem("desk", "Environment", "Desk", "desk", 1.2),
	LibraryItem("cabinet", "Environment", "Filing cabinet", "filing_cabinet", 0.8),
	LibraryItem("crate", "Environment", "Crate", "box", 0.8),
	# Gameplay
	LibraryItem("radio", "Gameplay", "Radio", "table_radio", 0.4),
	LibraryItem("spawn", "Gameplay", "Player spawn", "spawn", 0.4),
	LibraryItem("repair", "Gameplay", "Scanner repair", "trigger_zone", 1.0),
	LibraryItem("locked_door", "Gameplay", "Locked door marker", "trigger_zone", 1.0),
	LibraryItem("key_item", "Gameplay", "Key / objective", "trigger_zone", 0.5),
	LibraryItem("trigger", "Gameplay", "Trigger zone", "trigger_zone", 1.5),
	# Audio
	LibraryItem("ambient", "Audio", "Ambient loop", "sound_ambient", 0.5),
	LibraryItem("oneshot", "Audio", "One-shot sound", "sound_oneshot", 0.5),
	LibraryItem("moving_snd", "Audio", "Moving sound", "sound_moving", 0.5),
	# Entities
	LibraryItem("crawler", "Entity", "Crawler spawn", "entity_crawler", 0.6),
]


def items_by_category() -> dict[str, list[LibraryItem]]:
	"""Group library items for the HUD."""
	grouped: dict[str, list[LibraryItem]] = {}
	for item in LIBRARY:
		grouped.setdefault(item.category, []).append(item)
	return grouped


def make_library_op(
	item: LibraryItem,
	hit: tuple[float, float, float],
	*,
	size: float,
	place_y: float,
	floor_y: float,
	yaw_deg: float = 0.0,
) -> dict[str, Any] | None:
	"""Build a geometry op, sound, entity, or gameplay marker from a library item."""
	x, _y, z = hit
	half = max(0.2, size * 0.5)
	kind = item.kind

	if kind == "table":
		return {
			"op": "table",
			"x0": x - half * 1.4, "z0": z - half,
			"x1": x + half * 1.4, "z1": z + half,
			"top_y": 0.75, "material": "wood",
		}
	if kind == "desk":
		return {
			"op": "desk",
			"x0": x - half * 1.5, "z0": z - half,
			"x1": x + half * 1.5, "z1": z + half,
			"top_y": 0.72, "material": "wood",
		}
	if kind == "filing_cabinet":
		return {
			"op": "filing_cabinet",
			"x0": x - 0.35, "z0": z - 0.4,
			"x1": x + 0.35, "z1": z + 0.4,
			"height": 1.15, "material": "metal",
		}
	if kind == "pillar":
		return {"op": "pillar", "x": x, "z": z, "size": max(0.6, size), "material": "concrete"}
	if kind == "box":
		material = "metal" if item.id in ("pipe_box", "machinery", "ceiling_box") else "wood"
		h = max(0.4, size * (0.3 if item.id == "ceiling_box" else 1.0))
		y0 = (floor_y + 3.0) if item.id == "ceiling_box" else floor_y
		y1 = y0 + (0.25 if item.id == "ceiling_box" else h)
		if item.id == "pipe_box":
			y0, y1 = floor_y + 2.2, floor_y + 2.45
		return {
			"op": "box",
			"bounds": [x - half, y0, z - half, x + half, y1, z + half],
			"material": material,
		}
	if kind == "table_radio":
		return {"op": "table_radio", "x": x, "y": place_y, "z": z, "wall_x": 0.0}
	if kind == "spawn":
		return {"_meta": "spawn", "x": x, "y": place_y, "z": z}
	if kind == "wall_x":
		return {
			"op": "wall_x", "x": x,
			"z0": z - max(0.5, size), "z1": z + max(0.5, size),
			"y0": floor_y, "material": "concrete",
		}
	if kind == "wall_z":
		return {
			"op": "wall_z", "z": z,
			"x0": x - max(0.5, size), "x1": x + max(0.5, size),
			"y0": floor_y, "material": "concrete",
		}
	if kind == "walkable_plate":
		return {
			"op": "walkable_plate",
			"x0": x - half, "z0": z - half,
			"x1": x + half, "z1": z + half,
			"top_y": place_y, "thickness": 0.12,
		}
	if kind.startswith("sound_"):
		loop = kind != "sound_oneshot"
		return {
			"_meta": "sound",
			"id": f"{item.id}_{int(x * 10)}_{int(z * 10)}",
			"asset_key": "hum" if kind == "sound_ambient" else "old_radio",
			"file": "hum.wav" if kind == "sound_ambient" else "old-radio.wav",
			"position": [x, place_y, z],
			"volume": 0.25,
			"falloff": 15.0,
			"loop": loop,
			"moving": kind == "sound_moving",
			"propagation": {
				"directivity": "omni",
				"axis": [0.0, 0.0, -1.0],
				"occlusion": True,
				"reflection": True,
			},
		}
	if kind.startswith("entity_"):
		etype = kind.replace("entity_", "")
		return {
			"_meta": "entity",
			"id": f"{etype}_{int(x * 10)}_{int(z * 10)}",
			"type": etype.capitalize(),
			"position": [x, place_y, z],
			"rotation": [0.0, yaw_deg, 0.0],
			"behavior": "patrol",
			"audio": {"footsteps": "crawler_steps"},
		}
	if kind == "trigger_zone":
		return {
			"_meta": "trigger",
			"id": f"{item.id}_{int(x * 10)}_{int(z * 10)}",
			"type": item.id,
			"bounds": [x - half, place_y, z - half, x + half, place_y + 2.0, z + half],
		}
	return None


__all__ = ["LIBRARY", "LibraryItem", "items_by_category", "make_library_op"]
