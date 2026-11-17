"""Facility / room JSON I/O — first-class rooms for the NULLSIGHT editor.

Layout on disk::

	Facility/
		facility.json
		rooms/
			awakening.json
			backups/
		objects/     # reserved for shared prefabs
		audio/       # reserved for room audio overrides
		entities/    # reserved for shared entity defs

Room JSON is the single source of truth for geometry, connections, audio,
gameplay, story, and entities. Geometry ops still replay through LevelBuilder.
"""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from interactable_door import InteractableDoor
from level_format import LevelDocument, apply_op, build_door
from level_builder import LevelBuilder
from room import RoomData

FACILITY_VERSION = 1
ROOM_VERSION = 2
MAX_BACKUPS = 20


def facility_root() -> Path:
	"""Project ``Facility/`` directory."""
	return Path(__file__).resolve().parent / "Facility"


def rooms_dir() -> Path:
	"""``Facility/rooms/``."""
	return facility_root() / "rooms"


def backups_dir() -> Path:
	"""``Facility/rooms/backups/``."""
	return rooms_dir() / "backups"


@dataclass
class AcousticProfile:
	"""Room acoustic knobs consumed by Spherion / AcousticSystem."""

	reverb: float = 0.35
	absorption: float = 0.25
	diffusion: float = 0.40
	temperature_c: float = 18.0
	humidity_percent: float = 55.0
	profile_id: str = "industrial_small"

	def to_dict(self) -> dict[str, Any]:
		"""Serialize."""
		return {
			"profile_id": self.profile_id,
			"reverb": self.reverb,
			"absorption": self.absorption,
			"diffusion": self.diffusion,
			"temperature_c": self.temperature_c,
			"humidity_percent": self.humidity_percent,
		}

	@staticmethod
	def from_dict(data: dict[str, Any] | None) -> AcousticProfile:
		"""Deserialize with defaults."""
		data = data or {}
		return AcousticProfile(
			profile_id=str(data.get("profile_id", "industrial_small")),
			reverb=float(data.get("reverb", 0.35)),
			absorption=float(data.get("absorption", 0.25)),
			diffusion=float(data.get("diffusion", 0.40)),
			temperature_c=float(data.get("temperature_c", 18.0)),
			humidity_percent=float(data.get("humidity_percent", 55.0)),
		)


@dataclass
class RoomDocument:
	"""First-class editable room — geometry + connections + audio + gameplay + story."""

	room_id: str = "untitled"
	# size = [width, height, depth] meters
	size: tuple[float, float, float] = (14.0, 3.6, 12.0)
	material: str = "concrete"
	edge_subdivisions: int = 1
	floor_y: float = 0.0
	gravity_floor: float | None = None
	connections: list[str] = field(default_factory=list)
	audio_profile: AcousticProfile = field(default_factory=AcousticProfile)
	lighting: dict[str, Any] = field(default_factory=lambda: {"ambient": 0.0})
	gameplay: dict[str, Any] = field(default_factory=dict)
	story: dict[str, Any] = field(default_factory=lambda: {"notes": []})
	# Geometry ops (LevelBuilder replay) + optional interactable door.
	ops: list[dict[str, Any]] = field(default_factory=list)
	door: dict[str, Any] | None = None
	# Authored sound sources (editor Audio tab).
	sounds: list[dict[str, Any]] = field(default_factory=list)
	# Entity spawn points (type, pose, behavior).
	entities: list[dict[str, Any]] = field(default_factory=list)
	path: Path | None = None

	@property
	def width(self) -> float:
		"""Room width (X)."""
		return self.size[0]

	@property
	def ceiling_y(self) -> float:
		"""Room height (Y extent from floor)."""
		return self.size[1]

	@property
	def depth(self) -> float:
		"""Room depth (Z)."""
		return self.size[2]

	@property
	def spawn(self) -> tuple[float, float, float]:
		"""Player spawn from gameplay block."""
		raw = self.gameplay.get("spawn") or [self.width * 0.5, self.floor_y, self.depth * 0.5]
		return float(raw[0]), float(raw[1]), float(raw[2])

	@spawn.setter
	def spawn(self, value: tuple[float, float, float]) -> None:
		"""Write spawn into gameplay."""
		self.gameplay["spawn"] = [float(value[0]), float(value[1]), float(value[2])]

	def clone(self) -> RoomDocument:
		"""Deep copy for undo."""
		return RoomDocument.from_dict(self.to_dict(), path=self.path)

	def to_dict(self) -> dict[str, Any]:
		"""Serialize full room document."""
		data: dict[str, Any] = {
			"version": ROOM_VERSION,
			"room_id": self.room_id,
			"size": list(self.size),
			"material": self.material,
			"floor_y": self.floor_y,
			"edge_subdivisions": self.edge_subdivisions,
			"connections": list(self.connections),
			"audio_profile": self.audio_profile.to_dict(),
			"lighting": copy.deepcopy(self.lighting),
			"gameplay": copy.deepcopy(self.gameplay),
			"story": copy.deepcopy(self.story),
			"geometry": {
				"ops": copy.deepcopy(self.ops),
			},
			"sounds": copy.deepcopy(self.sounds),
			"entities": copy.deepcopy(self.entities),
		}
		if self.gravity_floor is not None:
			data["gravity_floor"] = self.gravity_floor
		if self.door is not None:
			data["geometry"]["door"] = copy.deepcopy(self.door)
		return data

	@staticmethod
	def from_dict(data: dict[str, Any], *, path: Path | None = None) -> RoomDocument:
		"""Load v2 room, or migrate legacy LevelDocument JSON."""
		# Legacy level_format v1 (name + footprint + ops).
		if "footprint" in data and "room_id" not in data:
			return RoomDocument.from_legacy_level(data, path=path)

		geo = data.get("geometry") or {}
		size_raw = data.get("size") or [14.0, 3.6, 12.0]
		size = (float(size_raw[0]), float(size_raw[1]), float(size_raw[2]))
		gameplay = dict(data.get("gameplay") or {})
		if "spawn" not in gameplay and data.get("spawn"):
			gameplay["spawn"] = list(data["spawn"])
		return RoomDocument(
			room_id=str(data.get("room_id", data.get("name", "untitled"))),
			size=size,
			material=str(data.get("material", "concrete")),
			floor_y=float(data.get("floor_y", 0.0)),
			gravity_floor=(
				float(data["gravity_floor"]) if "gravity_floor" in data else None
			),
			edge_subdivisions=int(data.get("edge_subdivisions", 1)),
			connections=list(data.get("connections") or []),
			audio_profile=AcousticProfile.from_dict(data.get("audio_profile")),
			lighting=dict(data.get("lighting") or {"ambient": 0.0}),
			gameplay=gameplay,
			story=dict(data.get("story") or {"notes": []}),
			ops=list(geo.get("ops") or data.get("ops") or []),
			door=geo.get("door", data.get("door")),
			sounds=list(data.get("sounds") or []),
			entities=list(data.get("entities") or []),
			path=path,
		)

	@staticmethod
	def from_legacy_level(data: dict[str, Any], *, path: Path | None = None) -> RoomDocument:
		"""Convert old levels/*.json into a RoomDocument."""
		legacy = LevelDocument.from_dict(data, path=path)
		return RoomDocument(
			room_id=legacy.name,
			size=(legacy.width, legacy.ceiling_y, legacy.depth),
			material=legacy.shell_material,
			floor_y=legacy.floor_y,
			gravity_floor=legacy.gravity_floor,
			edge_subdivisions=legacy.edge_subdivisions,
			connections=[],
			audio_profile=AcousticProfile(profile_id="industrial_small"),
			gameplay={"spawn": list(legacy.spawn)},
			ops=copy.deepcopy(legacy.ops),
			door=copy.deepcopy(legacy.door),
			sounds=[],
			entities=[],
			path=path,
		)

	@staticmethod
	def load(path: Path) -> RoomDocument:
		"""Read a room JSON file."""
		data = json.loads(path.read_text(encoding="utf-8"))
		return RoomDocument.from_dict(data, path=path)

	def save(self, path: Path | None = None, *, backup: bool = True) -> Path:
		"""Write room JSON; optionally rotate numbered backups first."""
		target = path or self.path
		if target is None:
			target = rooms_dir() / f"{self.room_id}.json"
		target.parent.mkdir(parents=True, exist_ok=True)
		if backup and target.is_file():
			_write_backup(target)
		target.write_text(json.dumps(self.to_dict(), indent="\t") + "\n", encoding="utf-8")
		self.path = target
		return target

	def build(self) -> tuple[RoomData, InteractableDoor | None]:
		"""Bake geometry into RoomData and attach authored metadata."""
		builder = LevelBuilder(
			self.width,
			self.depth,
			floor_y=self.floor_y,
			ceiling_y=self.ceiling_y,
			edge_subdivisions=self.edge_subdivisions,
		)
		builder.name(self.room_id)
		builder.shell_material(self.material)
		if self.gravity_floor is not None:
			builder.gravity_floor(self.gravity_floor)
		sx, sy, sz = self.spawn
		builder.spawn_at(sx, sz, y=sy)

		for op in self.ops:
			apply_op(builder, op)

		door = build_door(self.door, default_floor_y=self.floor_y)
		if door is not None:
			builder.decor_edges(door.frame_edges, occludes=True, material="metal_door")

		room = builder.build()
		# Attach editor metadata without breaking frozen RoomData — rebuild with extras.
		return (
			RoomData(
				environment_edges=room.environment_edges,
				occluder_hulls=room.occluder_hulls,
				bounds=room.bounds,
				occluders=room.occluders,
				collidables=room.collidables,
				boundaries=room.boundaries,
				spawn=room.spawn,
				name=room.name,
				occluder_materials=room.occluder_materials,
				shell_material=room.shell_material,
				floor_y=room.floor_y,
				ceiling_y=room.ceiling_y,
				walkables=room.walkables,
				authored_sounds=copy.deepcopy(self.sounds),
				audio_profile=self.audio_profile.to_dict(),
				entities=copy.deepcopy(self.entities),
				connections=list(self.connections),
			),
			door,
		)


@dataclass
class FacilityDocument:
	"""Top-level facility graph linking rooms."""

	facility_id: str = "nullsight_facility"
	start_room: str = "awakening"
	rooms: list[str] = field(default_factory=list)
	# Explicit edges: {"from": "a", "to": "b", "via": "door_west"}
	connections: list[dict[str, str]] = field(default_factory=list)
	path: Path | None = None

	def to_dict(self) -> dict[str, Any]:
		"""Serialize facility index."""
		return {
			"version": FACILITY_VERSION,
			"facility_id": self.facility_id,
			"start_room": self.start_room,
			"rooms": list(self.rooms),
			"connections": copy.deepcopy(self.connections),
		}

	@staticmethod
	def from_dict(data: dict[str, Any], *, path: Path | None = None) -> FacilityDocument:
		"""Deserialize facility index."""
		return FacilityDocument(
			facility_id=str(data.get("facility_id", "nullsight_facility")),
			start_room=str(data.get("start_room", "awakening")),
			rooms=list(data.get("rooms") or []),
			connections=list(data.get("connections") or []),
			path=path,
		)

	@staticmethod
	def load(path: Path | None = None) -> FacilityDocument:
		"""Load facility.json (creates a default if missing)."""
		target = path or (facility_root() / "facility.json")
		if not target.is_file():
			doc = FacilityDocument()
			doc.path = target
			return doc
		data = json.loads(target.read_text(encoding="utf-8"))
		return FacilityDocument.from_dict(data, path=target)

	def save(self, path: Path | None = None) -> Path:
		"""Write facility.json."""
		target = path or self.path or (facility_root() / "facility.json")
		target.parent.mkdir(parents=True, exist_ok=True)
		target.write_text(json.dumps(self.to_dict(), indent="\t") + "\n", encoding="utf-8")
		self.path = target
		return target

	def load_start_room(self) -> RoomDocument:
		"""Load the start room document from disk."""
		ensure_facility_dirs()
		room_path = rooms_dir() / f"{self.start_room}.json"
		if not room_path.is_file():
			raise FileNotFoundError(f"Start room missing: {room_path}")
		return RoomDocument.load(room_path)


def ensure_facility_dirs() -> None:
	"""Create Facility folder tree if needed."""
	for path in (
		facility_root(),
		rooms_dir(),
		backups_dir(),
		facility_root() / "objects",
		facility_root() / "audio",
		facility_root() / "entities",
	):
		path.mkdir(parents=True, exist_ok=True)


def _write_backup(room_path: Path) -> Path:
	"""Copy ``room.json`` to ``backups/room_backup_NNN.json``."""
	backups_dir().mkdir(parents=True, exist_ok=True)
	stem = room_path.stem
	existing = sorted(backups_dir().glob(f"{stem}_backup_*.json"))
	next_index = 1
	if existing:
		last = existing[-1].stem
		try:
			next_index = int(last.rsplit("_", 1)[-1]) + 1
		except ValueError:
			next_index = len(existing) + 1
	dest = backups_dir() / f"{stem}_backup_{next_index:03d}.json"
	shutil.copy2(room_path, dest)
	# Prune oldest beyond MAX_BACKUPS.
	all_backups = sorted(backups_dir().glob(f"{stem}_backup_*.json"))
	while len(all_backups) > MAX_BACKUPS:
		all_backups[0].unlink(missing_ok=True)
		all_backups = sorted(backups_dir().glob(f"{stem}_backup_*.json"))
	return dest


def new_blank_room(
	*,
	room_id: str = "untitled",
	width: float = 14.0,
	height: float = 3.6,
	depth: float = 12.0,
) -> RoomDocument:
	"""Blank concrete shell ready for editing."""
	return RoomDocument(
		room_id=room_id,
		size=(width, height, depth),
		material="concrete",
		gameplay={"spawn": [width * 0.5, 0.0, depth * 0.5]},
		ops=[{"op": "outer_shell", "floor": True}],
		audio_profile=AcousticProfile(),
		path=rooms_dir() / f"{room_id}.json",
	)


def migrate_legacy_awakening() -> Path:
	"""Copy legacy ``levels/awakening.json`` into Facility if needed."""
	ensure_facility_dirs()
	dest = rooms_dir() / "awakening.json"
	if dest.is_file():
		return dest
	legacy = Path(__file__).resolve().parent / "levels" / "awakening.json"
	if legacy.is_file():
		room = RoomDocument.from_legacy_level(json.loads(legacy.read_text(encoding="utf-8")))
		# Seed radio sound + west door connection placeholder.
		room.sounds = [
			{
				"id": "old_radio_1",
				"asset_key": "old_radio",
				"file": "old-radio.wav",
				"position": [0.85, 0.68, 5.40],
				"volume": 0.10,
				"falloff": 12.0,
				"loop": True,
				"propagation": {
					"directivity": "cardioid",
					"axis": [1.0, 0.0, 0.0],
					"occlusion": True,
					"reflection": True,
				},
			}
		]
		room.connections = []
		room.audio_profile = AcousticProfile(
			profile_id="industrial_small",
			reverb=0.30,
			absorption=0.28,
			diffusion=0.35,
			temperature_c=16.0,
			humidity_percent=60.0,
		)
		room.story = {"notes": ["Threshold / Wake — first room."]}
		room.save(dest, backup=False)
	else:
		new_blank_room(room_id="awakening").save(dest, backup=False)

	fac = FacilityDocument(
		facility_id="nullsight_wing_a",
		start_room="awakening",
		rooms=["awakening"],
		connections=[],
	)
	fac.save()
	return dest


__all__ = [
	"AcousticProfile",
	"FacilityDocument",
	"RoomDocument",
	"backups_dir",
	"ensure_facility_dirs",
	"facility_root",
	"migrate_legacy_awakening",
	"new_blank_room",
	"rooms_dir",
]
