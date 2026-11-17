"""JSON level documents that replay through ``LevelBuilder``.

Levels are an ordered list of placement ops — not baked edge lists — so the
editor can round-trip cleanly and stay human-editable.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from interactable_door import InteractableDoor
from level_builder import LevelBuilder
from room import RoomData

LEVEL_FORMAT_VERSION = 1

# Op name → (LevelBuilder method name, optional arg remapping).
_OP_METHODS: dict[str, str] = {
	"outer_shell": "outer_shell",
	"floor_grid": "floor_grid",
	"environment_box": "environment_box",
	"wall_x": "wall_x",
	"wall_z": "wall_z",
	"divider_wall_x": "divider_wall_x",
	"box": "box",
	"walkable_plate": "walkable_plate",
	"pillar": "pillar",
	"table": "table",
	"desk": "desk",
	"filing_cabinet": "filing_cabinet",
	"table_radio": "table_radio",
}


@dataclass
class LevelDocument:
	"""Editable level: footprint + ordered placement ops + optional door."""

	name: str = "untitled"
	width: float = 14.0
	depth: float = 12.0
	floor_y: float = 0.0
	ceiling_y: float = 3.6
	edge_subdivisions: int = 1
	shell_material: str = "concrete"
	gravity_floor: float | None = None
	spawn: tuple[float, float, float] = (7.0, 0.0, 6.0)
	ops: list[dict[str, Any]] = field(default_factory=list)
	door: dict[str, Any] | None = None
	path: Path | None = None

	def clone(self) -> LevelDocument:
		"""Deep copy for undo stacks."""
		return LevelDocument(
			name=self.name,
			width=self.width,
			depth=self.depth,
			floor_y=self.floor_y,
			ceiling_y=self.ceiling_y,
			edge_subdivisions=self.edge_subdivisions,
			shell_material=self.shell_material,
			gravity_floor=self.gravity_floor,
			spawn=self.spawn,
			ops=copy.deepcopy(self.ops),
			door=copy.deepcopy(self.door),
			path=self.path,
		)

	def to_dict(self) -> dict[str, Any]:
		"""Serialize for JSON."""
		data: dict[str, Any] = {
			"version": LEVEL_FORMAT_VERSION,
			"name": self.name,
			"footprint": {
				"width": self.width,
				"depth": self.depth,
				"floor_y": self.floor_y,
				"ceiling_y": self.ceiling_y,
				"edge_subdivisions": self.edge_subdivisions,
				"shell_material": self.shell_material,
			},
			"spawn": list(self.spawn),
			"ops": self.ops,
		}
		if self.gravity_floor is not None:
			data["footprint"]["gravity_floor"] = self.gravity_floor
		if self.door is not None:
			data["door"] = self.door
		return data

	@staticmethod
	def from_dict(data: dict[str, Any], *, path: Path | None = None) -> LevelDocument:
		"""Deserialize from JSON dict."""
		fp = data.get("footprint") or {}
		spawn_raw = data.get("spawn") or [7.0, 0.0, 6.0]
		spawn = (float(spawn_raw[0]), float(spawn_raw[1]), float(spawn_raw[2]))
		return LevelDocument(
			name=str(data.get("name", "untitled")),
			width=float(fp.get("width", 14.0)),
			depth=float(fp.get("depth", 12.0)),
			floor_y=float(fp.get("floor_y", 0.0)),
			ceiling_y=float(fp.get("ceiling_y", 3.6)),
			edge_subdivisions=int(fp.get("edge_subdivisions", 1)),
			shell_material=str(fp.get("shell_material", "concrete")),
			gravity_floor=(
				float(fp["gravity_floor"]) if "gravity_floor" in fp else None
			),
			spawn=spawn,
			ops=list(data.get("ops") or []),
			door=data.get("door"),
			path=path,
		)

	def save(self, path: Path | None = None) -> Path:
		"""Write JSON to disk."""
		target = path or self.path
		if target is None:
			raise ValueError("No path to save level document")
		target.parent.mkdir(parents=True, exist_ok=True)
		target.write_text(json.dumps(self.to_dict(), indent="\t") + "\n", encoding="utf-8")
		self.path = target
		return target

	@staticmethod
	def load(path: Path) -> LevelDocument:
		"""Read a level JSON file."""
		data = json.loads(path.read_text(encoding="utf-8"))
		return LevelDocument.from_dict(data, path=path)

	def build(self) -> tuple[RoomData, InteractableDoor | None]:
		"""Replay ops into a fresh ``LevelBuilder`` and return room + door."""
		builder = LevelBuilder(
			self.width,
			self.depth,
			floor_y=self.floor_y,
			ceiling_y=self.ceiling_y,
			edge_subdivisions=self.edge_subdivisions,
		)
		builder.name(self.name)
		builder.shell_material(self.shell_material)
		if self.gravity_floor is not None:
			builder.gravity_floor(self.gravity_floor)
		sx, sy, sz = self.spawn
		builder.spawn_at(sx, sz, y=sy)

		for op in self.ops:
			_apply_op(builder, op)

		door = _build_door(self.door, default_floor_y=self.floor_y)
		if door is not None:
			builder.decor_edges(door.frame_edges, occludes=True, material="metal_door")

		return builder.build(), door


def _apply_op(builder: LevelBuilder, op: dict[str, Any]) -> None:
	"""Dispatch one placement op onto the builder."""
	kind = str(op.get("op", ""))
	if kind == "spawn":
		# Spawn is already applied from document.spawn; ignore stray spawn ops.
		return
	method_name = _OP_METHODS.get(kind)
	if method_name is None:
		raise ValueError(f"Unknown level op: {kind!r}")

	method: Callable[..., LevelBuilder] = getattr(builder, method_name)
	args = dict(op)
	args.pop("op", None)
	# Allow compact ``bounds: [x0,y0,z0,x1,y1,z1]`` for box / environment_box.
	if "bounds" in args and kind in ("box", "environment_box"):
		x0, y0, z0, x1, y1, z1 = args.pop("bounds")
		method(x0, y0, z0, x1, y1, z1, **args)
		return
	# ``floor_grid`` first positional is y.
	if kind == "floor_grid":
		y = args.pop("y")
		method(y, **args)
		return
	method(**args)


def _build_door(spec: dict[str, Any] | None, *, default_floor_y: float) -> InteractableDoor | None:
	"""Construct an interactable door from the document door block."""
	if not spec:
		return None
	wall = str(spec.get("wall", "west"))
	z_center = float(spec["z_center"])
	width = float(spec.get("width", 1.0))
	height = float(spec.get("height", 2.15))
	floor_y = float(spec.get("floor_y", default_floor_y))
	if wall == "west":
		return InteractableDoor.west_wall(
			z_center=z_center,
			width=width,
			height=height,
			floor_y=floor_y,
			wall_x=float(spec.get("wall_x", 0.04)),
		)
	if wall == "east":
		extent = float(spec["room_extent"])
		return InteractableDoor.east_wall(
			room_extent=extent,
			z_center=z_center,
			width=width,
			height=height,
			floor_y=floor_y,
		)
	raise ValueError(f"Unsupported door wall: {wall!r}")


def new_blank_level(
	*,
	name: str = "untitled",
	width: float = 14.0,
	depth: float = 12.0,
	ceiling_y: float = 3.6,
) -> LevelDocument:
	"""Start a room with an outer shell and center spawn."""
	return LevelDocument(
		name=name,
		width=width,
		depth=depth,
		ceiling_y=ceiling_y,
		spawn=(width * 0.5, 0.0, depth * 0.5),
		ops=[{"op": "outer_shell", "floor": True}],
	)


def default_levels_dir() -> Path:
	"""Project ``levels/`` folder next to this module."""
	return Path(__file__).resolve().parent / "levels"


__all__ = [
	"LEVEL_FORMAT_VERSION",
	"LevelDocument",
	"default_levels_dir",
	"new_blank_level",
	"apply_op",
	"build_door",
]


def apply_op(builder: LevelBuilder, op: dict[str, Any]) -> None:
	"""Public wrapper — dispatch one placement op onto the builder."""
	_apply_op(builder, op)


def build_door(spec: dict[str, Any] | None, *, default_floor_y: float) -> InteractableDoor | None:
	"""Public wrapper for door construction."""
	return _build_door(spec, default_floor_y=default_floor_y)
