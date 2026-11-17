"""NULLSIGHT Facility Level Editor.

Implements the editor requirements doc: multi-view cameras, select/move/rotate/scale,
room-first Facility JSON, object library, audio/entity placement, connections,
scanner preview, playtest launch, and automatic backups.

Run::

	python level_editor.py
	python level_editor.py --frames 60
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

import hyperlite

import keys
from camera import Camera, DEFAULT_FOV_Y
from editor_library import LIBRARY, LibraryItem, items_by_category, make_library_op
from engine_config import configure_bench_engine
from facility_io import (
	FacilityDocument,
	RoomDocument,
	ensure_facility_dirs,
	migrate_legacy_awakening,
	new_blank_room,
	rooms_dir,
)
from math3d import Vec3, length
from renderer import ProjectedVertexCache, _queue_world_segment_packed
from room import Edge, RoomData
from scan import ScanDevice
from scan_palette import SCAN_PACKED, faded_packed
from screen_batch import ScreenLineCollector
from ui_text import draw_text

EDITOR_WIDTH = 1280
EDITOR_HEIGHT = 720
FLY_SPEED = 8.0
LOOK_SENS = 0.003
GRID_SNAP = 0.25
GHOST = faded_packed(0.55)
SELECT = faded_packed(1.0)
SPAWN_C = 0xFFFFAA00
SOUND_C = 0xFF66CCFF
ENTITY_C = 0xFFFF6688
TRIGGER_C = 0xFFFFFF66


class ViewMode(Enum):
	"""Viewport camera mode."""

	FREE_FLY = auto()
	FIRST_PERSON = auto()
	TOP_DOWN = auto()


class EditTool(Enum):
	"""Deprecated — kept for type compatibility; hotbar drives mode now."""

	SELECT = auto()
	PLACE = auto()


class EditorTab(Enum):
	"""Primary editor tab."""

	WORLD = auto()
	ROOM = auto()
	AUDIO = auto()
	ENTITIES = auto()
	SCANNER = auto()


@dataclass
class Selection:
	"""What the user has selected."""

	kind: str  # op | sound | entity | trigger | spawn | none
	index: int = -1


@dataclass
class EditorState:
	"""Mutable editor session."""

	room: RoomDocument
	facility: FacilityDocument
	undo: list[RoomDocument] = field(default_factory=list)
	view: ViewMode = ViewMode.FREE_FLY
	tab: EditorTab = EditorTab.WORLD
	# 0 = select-only; 1-9 = library brush slots.
	hotbar: int = 1
	selection: Selection = field(default_factory=lambda: Selection("none"))
	snap: bool = True
	place_y: float = 0.0
	size: float = 1.0
	yaw_deg: float = 0.0
	dirty: bool = False
	status: str = "Ready"
	show_help: bool = True
	scanner_preview: bool = False
	baked: RoomData | None = None
	scan: ScanDevice | None = None
	_prev_keys: dict[str, bool] = field(default_factory=dict)
	_prev_lmb: bool = False
	_prev_rmb: bool = False
	_dragging: bool = False
	_drag_last: Vec3 | None = None
	_drag_undo_pushed: bool = False


# First nine library entries map to hotkeys 1-9.
_HOTBAR_ITEMS = LIBRARY[:9]


def _snap(value: float, step: float) -> float:
	"""Quantize to grid."""
	if step <= 0.0:
		return value
	return round(value / step) * step


def _ray_plane_y(eye: Vec3, direction: Vec3, plane_y: float) -> Vec3 | None:
	"""Ray ∩ horizontal plane."""
	dy = direction[1]
	if abs(dy) < 1e-6:
		return None
	t = (plane_y - eye[1]) / dy
	if t <= 0.0:
		return None
	return (eye[0] + direction[0] * t, plane_y, eye[2] + direction[2] * t)


def _camera_ray(camera: Camera, mx: int, my: int, width: int, height: int) -> tuple[Vec3, Vec3]:
	"""World eye + unit direction through a screen pixel."""
	eye = camera.eye_position()
	ndc_x = (mx + 0.5) / width * 2.0 - 1.0
	ndc_y = 1.0 - (my + 0.5) / height * 2.0
	tan_v = math.tan(DEFAULT_FOV_Y * 0.5)
	tan_h = tan_v * (width / max(1, height))
	cam_dir = (ndc_x * tan_h, ndc_y * tan_v, 1.0)
	norm = length(cam_dir) or 1.0
	cx, cy, cz = cam_dir[0] / norm, cam_dir[1] / norm, cam_dir[2] / norm
	cos_p, sin_p = math.cos(camera.pitch), math.sin(camera.pitch)
	cos_y, sin_y = math.cos(camera.yaw), math.sin(camera.yaw)
	ry = cy * cos_p + cz * sin_p
	rz = -cy * sin_p + cz * cos_p
	rx = cx
	wx = rx * cos_y + rz * sin_y
	wz = -rx * sin_y + rz * cos_y
	wy = ry
	direction = (wx, wy, wz)
	n = length(direction) or 1.0
	return eye, (direction[0] / n, direction[1] / n, direction[2] / n)


def _aabb_edges(bounds: tuple[Vec3, Vec3]) -> list[Edge]:
	"""AABB wireframe."""
	(x0, y0, z0), (x1, y1, z1) = bounds
	c = (
		(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1),
		(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1),
	)
	idx = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))
	return [(c[a], c[b]) for a, b in idx]


def _op_bounds(op: dict[str, Any], floor_y: float) -> tuple[Vec3, Vec3] | None:
	"""AABB for a geometry op."""
	kind = op.get("op")
	if kind in ("box", "environment_box") and "bounds" in op:
		x0, y0, z0, x1, y1, z1 = op["bounds"]
		return (min(x0, x1), min(y0, y1), min(z0, z1)), (max(x0, x1), max(y0, y1), max(z0, z1))
	if kind in ("table", "desk"):
		x0, z0, x1, z1 = op["x0"], op["z0"], op["x1"], op["z1"]
		top = float(op.get("top_y", 0.75))
		return (min(x0, x1), floor_y, min(z0, z1)), (max(x0, x1), top, max(z0, z1))
	if kind == "filing_cabinet":
		x0, z0, x1, z1 = op["x0"], op["z0"], op["x1"], op["z1"]
		h = float(op.get("height", 1.15))
		return (min(x0, x1), floor_y, min(z0, z1)), (max(x0, x1), floor_y + h, max(z0, z1))
	if kind == "pillar":
		x, z, s = float(op["x"]), float(op["z"]), float(op.get("size", 1.2)) * 0.5
		return (x - s, floor_y, z - s), (x + s, floor_y + 3.0, z + s)
	if kind == "table_radio":
		x, y, z = float(op["x"]), float(op["y"]), float(op["z"])
		return (x - 0.25, y, z - 0.25), (x + 0.25, y + 0.3, z + 0.25)
	if kind == "walkable_plate":
		x0, z0, x1, z1 = op["x0"], op["z0"], op["x1"], op["z1"]
		top, th = float(op["top_y"]), float(op.get("thickness", 0.12))
		return (min(x0, x1), top - th, min(z0, z1)), (max(x0, x1), top, max(z0, z1))
	if kind in ("wall_x", "divider_wall_x"):
		x, z0, z1 = float(op["x"]), float(op["z0"]), float(op["z1"])
		y0 = float(op.get("y0", floor_y))
		y1 = float(op.get("y1", floor_y + 3.0))
		return (x - 0.18, y0, min(z0, z1)), (x + 0.18, y1, max(z0, z1))
	if kind == "wall_z":
		z, x0, x1 = float(op["z"]), float(op["x0"]), float(op["x1"])
		y0 = float(op.get("y0", floor_y))
		y1 = float(op.get("y1", floor_y + 3.0))
		return (min(x0, x1), y0, z - 0.18), (max(x0, x1), y1, z + 0.18)
	return None


def _point_in_aabb(point: Vec3, bounds: tuple[Vec3, Vec3], pad: float = 0.2) -> bool:
	"""Inclusive AABB test."""
	(x0, y0, z0), (x1, y1, z1) = bounds
	x, y, z = point
	return x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad and z0 - pad <= z <= z1 + pad


def _translate_op(op: dict[str, Any], dx: float, dy: float, dz: float) -> None:
	"""Move a geometry op in place."""
	kind = op.get("op")
	if kind in ("box", "environment_box") and "bounds" in op:
		b = op["bounds"]
		op["bounds"] = [b[0] + dx, b[1] + dy, b[2] + dz, b[3] + dx, b[4] + dy, b[5] + dz]
	elif kind in ("table", "desk", "filing_cabinet", "walkable_plate"):
		op["x0"] = float(op["x0"]) + dx
		op["x1"] = float(op["x1"]) + dx
		op["z0"] = float(op["z0"]) + dz
		op["z1"] = float(op["z1"]) + dz
		if "top_y" in op and dy:
			op["top_y"] = float(op["top_y"]) + dy
	elif kind == "pillar":
		op["x"] = float(op["x"]) + dx
		op["z"] = float(op["z"]) + dz
	elif kind == "table_radio":
		op["x"] = float(op["x"]) + dx
		op["y"] = float(op["y"]) + dy
		op["z"] = float(op["z"]) + dz
	elif kind in ("wall_x", "divider_wall_x"):
		op["x"] = float(op["x"]) + dx
		op["z0"] = float(op["z0"]) + dz
		op["z1"] = float(op["z1"]) + dz
	elif kind == "wall_z":
		op["z"] = float(op["z"]) + dz
		op["x0"] = float(op["x0"]) + dx
		op["x1"] = float(op["x1"]) + dx


def _scale_op(op: dict[str, Any], factor: float) -> None:
	"""Uniform-ish scale of horizontal footprint about center."""
	bounds = _op_bounds(op, 0.0)
	if bounds is None or factor <= 0.05:
		return
	(x0, y0, z0), (x1, y1, z1) = bounds
	cx, cz = (x0 + x1) * 0.5, (z0 + z1) * 0.5
	kind = op.get("op")
	if kind in ("box", "environment_box") and "bounds" in op:
		b = op["bounds"]
		op["bounds"] = [
			cx + (b[0] - cx) * factor, b[1], cz + (b[2] - cz) * factor,
			cx + (b[3] - cx) * factor, b[4], cz + (b[5] - cz) * factor,
		]
	elif kind in ("table", "desk", "filing_cabinet", "walkable_plate"):
		op["x0"] = cx + (float(op["x0"]) - cx) * factor
		op["x1"] = cx + (float(op["x1"]) - cx) * factor
		op["z0"] = cz + (float(op["z0"]) - cz) * factor
		op["z1"] = cz + (float(op["z1"]) - cz) * factor
	elif kind == "pillar":
		op["size"] = max(0.3, float(op.get("size", 1.2)) * factor)


class LevelEditorApp:
	"""Full facility editor application."""

	HELP = (
		"RMB look   WASD move   Q/E up-down   Shift sprint",
		"LMB select / drag move   Shift+LMB place brush   Del delete",
		"0 select   1-9 brushes   Z/C rotate   -/= scale   [/] height   G grid",
		"V view   F2-F6 tabs   F6 scanner   F9 playtest   Ctrl+S save   Ctrl+Z undo",
	)

	def __init__(self, room: RoomDocument, facility: FacilityDocument) -> None:
		self.state = EditorState(room=room, facility=facility, place_y=room.floor_y)
		self._rebuild()

	@property
	def library_item(self) -> LibraryItem | None:
		"""Active brush, or None in select-only hotbar slot."""
		if self.state.hotbar <= 0:
			return None
		index = self.state.hotbar - 1
		if 0 <= index < len(_HOTBAR_ITEMS):
			return _HOTBAR_ITEMS[index]
		return None

	def _move_selection_to(self, hit: Vec3, *, push_undo: bool) -> None:
		"""Snap selection center XZ to hit (used by drag-move)."""
		sel = self.state.selection
		if sel.kind == "none":
			return
		if push_undo and not self.state._drag_undo_pushed:
			self._push_undo()
			self.state._drag_undo_pushed = True
		room = self.state.room
		if sel.kind == "op" and 0 <= sel.index < len(room.ops):
			bounds = _op_bounds(room.ops[sel.index], room.floor_y)
			if bounds:
				cx = (bounds[0][0] + bounds[1][0]) * 0.5
				cz = (bounds[0][2] + bounds[1][2]) * 0.5
				_translate_op(room.ops[sel.index], hit[0] - cx, 0.0, hit[2] - cz)
		elif sel.kind == "sound" and 0 <= sel.index < len(room.sounds):
			pos = room.sounds[sel.index].setdefault("position", [0.0, 0.0, 0.0])
			pos[0], pos[2] = hit[0], hit[2]
		elif sel.kind == "entity" and 0 <= sel.index < len(room.entities):
			pos = room.entities[sel.index].setdefault("position", [0.0, 0.0, 0.0])
			pos[0], pos[2] = hit[0], hit[2]
		elif sel.kind == "spawn":
			room.spawn = (hit[0], self.state.place_y, hit[2])
		elif sel.kind == "trigger":
			triggers = room.gameplay.get("triggers") or []
			if 0 <= sel.index < len(triggers):
				b = triggers[sel.index].get("bounds")
				if b:
					cx = (b[0] + b[3]) * 0.5
					cz = (b[2] + b[5]) * 0.5
					dx, dz = hit[0] - cx, hit[2] - cz
					triggers[sel.index]["bounds"] = [
						b[0] + dx, b[1], b[2] + dz, b[3] + dx, b[4], b[5] + dz,
					]
		self.state.dirty = True

	def _key_edge(self, engine: hyperlite.Engine, name: str, code: int) -> bool:
		"""Edge-triggered key."""
		down = engine.key_down(code)
		prev = self.state._prev_keys.get(name, False)
		self.state._prev_keys[name] = down
		return down and not prev

	def _push_undo(self) -> None:
		"""Snapshot room for undo."""
		self.state.undo.append(self.state.room.clone())
		if len(self.state.undo) > 64:
			self.state.undo.pop(0)

	def _rebuild(self) -> None:
		"""Bake RoomData (+ optional scan priming for preview)."""
		try:
			baked, _door = self.state.room.build()
			self.state.baked = baked
			self.state.status = f"{self.state.room.room_id} ops:{len(self.state.room.ops)} snd:{len(self.state.room.sounds)} ent:{len(self.state.room.entities)}"
			if self.state.scanner_preview:
				self._prime_scan()
		except Exception as exc:  # noqa: BLE001
			self.state.status = f"Build error: {exc}"

	def _prime_scan(self) -> None:
		"""Capture a full scan of the baked room for scanner preview."""
		if self.state.baked is None:
			return
		scan = ScanDevice()
		room = self.state.baked
		eye = room.spawn[0], room.spawn[1] + 1.6, room.spawn[2]
		scan.update(
			1 / 60, True, eye, 0.0, 0.2,
			room.environment_edges, room.occluder_hulls, room.occluders, room.boundaries,
			EDITOR_WIDTH, EDITOR_HEIGHT,
		)
		scan.update(2.0, False, eye, 0.0, 0.2)
		self.state.scan = scan

	def undo_once(self) -> None:
		"""Undo last edit."""
		if not self.state.undo:
			self.state.status = "Nothing to undo"
			return
		self.state.room = self.state.undo.pop()
		self.state.selection = Selection("none")
		self.state.dirty = True
		self._rebuild()
		self.state.status = "Undo"

	def save(self) -> None:
		"""Save room + facility index with backup."""
		ensure_facility_dirs()
		path = self.state.room.save(backup=True)
		if self.state.room.room_id not in self.state.facility.rooms:
			self.state.facility.rooms.append(self.state.room.room_id)
		# Sync connection list onto facility graph.
		for conn in self.state.room.connections:
			edge = {"from": self.state.room.room_id, "to": conn, "via": "door"}
			if edge not in self.state.facility.connections:
				self.state.facility.connections.append(edge)
		self.state.facility.save()
		self.state.dirty = False
		self.state.status = f"Saved {path.name} (+backup)"

	def playtest(self) -> None:
		"""Save then launch the game into this room immediately."""
		self.save()
		room_path = self.state.room.path or (rooms_dir() / f"{self.state.room.room_id}.json")
		cmd = [sys.executable, str(Path(__file__).resolve().parent / "main.py"), "--playtest", str(room_path)]
		self.state.status = "Launching playtest..."
		subprocess.Popen(cmd, cwd=str(Path(__file__).resolve().parent))

	def _cursor_hit(self, camera: Camera, engine: hyperlite.Engine, width: int, height: int) -> Vec3 | None:
		"""Hit on place plane (or top-down ground)."""
		mx, my = engine.mouse_pos()
		if self.state.view == ViewMode.TOP_DOWN:
			# Ortho-ish: map mouse to room XZ from camera height look-down.
			eye = camera.eye_position()
			# Approximate: use ray with pitch forced down.
			look_cam = Camera(position=camera.position, yaw=camera.yaw, pitch=-1.4)
			eye, direction = _camera_ray(look_cam, mx, my, width, height)
			hit = _ray_plane_y(eye, direction, self.state.place_y)
		else:
			eye, direction = _camera_ray(camera, mx, my, width, height)
			hit = _ray_plane_y(eye, direction, self.state.place_y)
		if hit is None:
			return None
		if self.state.snap:
			return (_snap(hit[0], GRID_SNAP), hit[1], _snap(hit[2], GRID_SNAP))
		return hit

	def _select_at(self, hit: Vec3) -> None:
		"""Pick op / sound / entity / trigger under cursor."""
		room = self.state.room
		best: Selection | None = None
		best_y = -1e9
		for i, op in enumerate(room.ops):
			bounds = _op_bounds(op, room.floor_y)
			if bounds and _point_in_aabb(hit, bounds):
				if bounds[1][1] >= best_y:
					best_y = bounds[1][1]
					best = Selection("op", i)
		for i, snd in enumerate(room.sounds):
			pos = snd.get("position") or [0, 0, 0]
			bounds = ((pos[0] - 0.3, pos[1] - 0.3, pos[2] - 0.3), (pos[0] + 0.3, pos[1] + 0.3, pos[2] + 0.3))
			if _point_in_aabb(hit, bounds) and pos[1] >= best_y:
				best_y = pos[1]
				best = Selection("sound", i)
		for i, ent in enumerate(room.entities):
			pos = ent.get("position") or [0, 0, 0]
			bounds = ((pos[0] - 0.4, pos[1], pos[2] - 0.4), (pos[0] + 0.4, pos[1] + 1.5, pos[2] + 0.4))
			if _point_in_aabb(hit, bounds) and pos[1] >= best_y:
				best_y = pos[1]
				best = Selection("entity", i)
		triggers = room.gameplay.get("triggers") or []
		for i, trig in enumerate(triggers):
			b = trig.get("bounds")
			if not b:
				continue
			bounds = ((b[0], b[1], b[2]), (b[3], b[4], b[5]))
			if _point_in_aabb(hit, bounds) and b[4] >= best_y:
				best_y = b[4]
				best = Selection("trigger", i)
		sx, sy, sz = room.spawn
		if _point_in_aabb(hit, ((sx - 0.3, sy, sz - 0.3), (sx + 0.3, sy + 1.6, sz + 0.3))):
			best = Selection("spawn", 0)
		self.state.selection = best or Selection("none")
		sel = self.state.selection
		self.state.status = f"Selected {sel.kind}" + (f"[{sel.index}]" if sel.index >= 0 else "")

	def _place_library(self, hit: Vec3) -> None:
		"""Place active library item at hit."""
		item = self.library_item
		if item is None:
			return
		payload = make_library_op(
			item, hit,
			size=self.state.size,
			place_y=self.state.place_y,
			floor_y=self.state.room.floor_y,
			yaw_deg=self.state.yaw_deg,
		)
		if payload is None:
			return
		self._push_undo()
		meta = payload.get("_meta")
		if meta == "spawn":
			self.state.room.spawn = (float(payload["x"]), float(payload["y"]), float(payload["z"]))
			self.state.selection = Selection("spawn", 0)
		elif meta == "sound":
			payload.pop("_meta", None)
			self.state.room.sounds.append(payload)
			self.state.selection = Selection("sound", len(self.state.room.sounds) - 1)
		elif meta == "entity":
			payload.pop("_meta", None)
			self.state.room.entities.append(payload)
			self.state.selection = Selection("entity", len(self.state.room.entities) - 1)
		elif meta == "trigger":
			payload.pop("_meta", None)
			triggers = self.state.room.gameplay.setdefault("triggers", [])
			triggers.append(payload)
			self.state.selection = Selection("trigger", len(triggers) - 1)
		else:
			self.state.room.ops.append(payload)
			self.state.selection = Selection("op", len(self.state.room.ops) - 1)
		self.state.dirty = True
		self._rebuild()
		self.state.status = f"Placed {item.label}"

	def _delete_selected(self) -> None:
		"""Delete selection."""
		sel = self.state.selection
		if sel.kind == "none":
			self.state.status = "Nothing selected"
			return
		self._push_undo()
		room = self.state.room
		if sel.kind == "op":
			if 0 <= sel.index < len(room.ops):
				if room.ops[sel.index].get("op") == "outer_shell":
					self.state.status = "Can't delete outer_shell"
					self.state.undo.pop()
					return
				room.ops.pop(sel.index)
		elif sel.kind == "sound" and 0 <= sel.index < len(room.sounds):
			room.sounds.pop(sel.index)
		elif sel.kind == "entity" and 0 <= sel.index < len(room.entities):
			room.entities.pop(sel.index)
		elif sel.kind == "trigger":
			triggers = room.gameplay.get("triggers") or []
			if 0 <= sel.index < len(triggers):
				triggers.pop(sel.index)
		self.state.selection = Selection("none")
		self.state.dirty = True
		self._rebuild()
		self.state.status = "Deleted"

	def _nudge_selected(self, dx: float, dy: float, dz: float) -> None:
		"""Apply MOVE tool translation."""
		sel = self.state.selection
		if sel.kind == "none":
			return
		self._push_undo()
		room = self.state.room
		if sel.kind == "op" and 0 <= sel.index < len(room.ops):
			_translate_op(room.ops[sel.index], dx, dy, dz)
		elif sel.kind == "sound" and 0 <= sel.index < len(room.sounds):
			pos = room.sounds[sel.index].setdefault("position", [0.0, 0.0, 0.0])
			pos[0] += dx
			pos[1] += dy
			pos[2] += dz
		elif sel.kind == "entity" and 0 <= sel.index < len(room.entities):
			pos = room.entities[sel.index].setdefault("position", [0.0, 0.0, 0.0])
			pos[0] += dx
			pos[1] += dy
			pos[2] += dz
		elif sel.kind == "spawn":
			sx, sy, sz = room.spawn
			room.spawn = (sx + dx, sy + dy, sz + dz)
		self.state.dirty = True
		self._rebuild()

	def _rotate_selected(self, delta_deg: float) -> None:
		"""ROTATE tool — yaw for entities / radio facing note."""
		sel = self.state.selection
		self.state.yaw_deg = (self.state.yaw_deg + delta_deg) % 360.0
		if sel.kind == "entity" and 0 <= sel.index < len(self.state.room.entities):
			self._push_undo()
			ent = self.state.room.entities[sel.index]
			rot = ent.setdefault("rotation", [0.0, 0.0, 0.0])
			rot[1] = (float(rot[1]) + delta_deg) % 360.0
			self.state.dirty = True
			self.state.status = f"Yaw {rot[1]:.0f}"
		else:
			self.state.status = f"Place yaw {self.state.yaw_deg:.0f}"

	def _scale_selected(self, factor: float) -> None:
		"""Scale selection, or brush size when nothing selected."""
		sel = self.state.selection
		if sel.kind == "op" and 0 <= sel.index < len(self.state.room.ops):
			self._push_undo()
			_scale_op(self.state.room.ops[sel.index], factor)
			self.state.dirty = True
			self._rebuild()
			self.state.status = f"Scaled x{factor:.2f}"
		else:
			self.state.size = max(0.25, min(8.0, self.state.size * factor))
			self.state.status = f"Brush size {self.state.size:.2f}"

	def handle_input(self, engine: hyperlite.Engine, camera: Camera, dt: float, width: int, height: int) -> bool:
		"""Return False to quit. Controls are intentionally boring and conflict-free."""
		st = self.state
		ctrl = engine.key_down(keys.CONTROL)
		shift = engine.key_down(keys.SHIFT)

		if self._key_edge(engine, "esc", keys.ESCAPE):
			return False

		# Tabs / modes
		if self._key_edge(engine, "f1", keys.F1):
			st.show_help = not st.show_help
		if self._key_edge(engine, "f2", 0x71):
			st.tab = EditorTab.WORLD
			st.scanner_preview = False
		if self._key_edge(engine, "f3", 0x72):
			st.tab = EditorTab.ROOM
		if self._key_edge(engine, "f4", 0x73):
			st.tab = EditorTab.AUDIO
		if self._key_edge(engine, "f5", 0x74):
			st.tab = EditorTab.ENTITIES
		if self._key_edge(engine, "f6", 0x75):
			st.tab = EditorTab.SCANNER
			st.scanner_preview = True
			self._prime_scan()
			st.status = "Scanner preview"
		if self._key_edge(engine, "f9", 0x78):
			self.playtest()

		if ctrl and self._key_edge(engine, "s", keys.S):
			self.save()
		if ctrl and self._key_edge(engine, "z", keys.Z):
			self.undo_once()

		# Hotbar 0-9
		digit_codes = (
			keys.D0, keys.D1, keys.D2, keys.D3, keys.D4,
			keys.D5, keys.D6, keys.D7, keys.D8, keys.D9,
		)
		for slot, code in enumerate(digit_codes):
			if self._key_edge(engine, f"hot_{slot}", code):
				st.hotbar = slot
				item = self.library_item
				st.status = "Brush: SELECT" if item is None else f"Brush: {item.label}"

		# View cycle (V)
		if self._key_edge(engine, "v", 0x56):
			order = [ViewMode.FREE_FLY, ViewMode.FIRST_PERSON, ViewMode.TOP_DOWN]
			st.view = order[(order.index(st.view) + 1) % len(order)]
			if st.view == ViewMode.TOP_DOWN:
				x, _y, z = camera.position
				camera.position = (x, max(12.0, st.room.ceiling_y + 8.0), z)
				camera.pitch = -1.45
			elif st.view == ViewMode.FIRST_PERSON:
				sx, sy, sz = st.room.spawn
				camera.position = (sx, sy, sz)
				camera.pitch = 0.0
			st.status = f"View: {st.view.name.replace('_', ' ').title()}"

		# Grid
		if self._key_edge(engine, "g", keys.G) and not ctrl:
			st.snap = not st.snap
			st.status = f"Grid snap {'ON' if st.snap else 'OFF'}"

		# Height plane
		step_y = GRID_SNAP if st.snap else 0.1
		if self._key_edge(engine, "height_down", keys.OEM4):
			st.place_y = _snap(st.place_y - step_y, GRID_SNAP) if st.snap else st.place_y - step_y
			st.status = f"Place height {st.place_y:.2f}"
		if self._key_edge(engine, "height_up", keys.OEM6):
			st.place_y = _snap(st.place_y + step_y, GRID_SNAP) if st.snap else st.place_y + step_y
			st.status = f"Place height {st.place_y:.2f}"

		# Rotate Z/C
		if self._key_edge(engine, "yaw_ccw", keys.Z) and not ctrl:
			self._rotate_selected(-15.0)
		if self._key_edge(engine, "yaw_cw", 0x43):
			self._rotate_selected(15.0)

		# Scale selected, or brush size if nothing selected
		if self._key_edge(engine, "minus", keys.OEM_MINUS):
			if st.selection.kind == "op":
				self._scale_selected(0.9)
			else:
				st.size = max(0.25, st.size - 0.25)
				st.status = f"Brush size {st.size:.2f}"
		if self._key_edge(engine, "plus", keys.OEM_PLUS):
			if st.selection.kind == "op":
				self._scale_selected(1.1)
			else:
				st.size = min(8.0, st.size + 0.25)
				st.status = f"Brush size {st.size:.2f}"

		if self._key_edge(engine, "del", keys.DELETE) or self._key_edge(engine, "x", keys.X):
			self._delete_selected()

		# Arrow keys always nudge selection
		if st.selection.kind != "none":
			nudge = GRID_SNAP if st.snap else 0.1
			if shift:
				nudge *= 4.0
			ndx = ndz = ndy = 0.0
			if self._key_edge(engine, "left", keys.LEFT):
				ndx = -nudge
			if self._key_edge(engine, "right", keys.RIGHT):
				ndx = nudge
			if self._key_edge(engine, "up", keys.UP):
				ndz = nudge
			if self._key_edge(engine, "down", keys.DOWN):
				ndz = -nudge
			if self._key_edge(engine, "pgup", 0x21):
				ndy = nudge
			if self._key_edge(engine, "pgdn", 0x22):
				ndy = -nudge
			if ndx or ndy or ndz:
				self._nudge_selected(ndx, ndy, ndz)

		# Connections on Room tab
		if st.tab == EditorTab.ROOM and self._key_edge(engine, "n", 0x4E):
			self._push_undo()
			if shift:
				st.room.connections.clear()
				st.status = "Connections cleared"
			else:
				name = f"hallway_{len(st.room.connections) + 1:02d}"
				st.room.connections.append(name)
				st.status = f"Connections: {', '.join(st.room.connections)}"
			st.dirty = True

		# Camera — WASD always means move (S = back)
		move_x = move_z = move_y = 0.0
		if engine.key_down(keys.W):
			move_z += 1.0
		if engine.key_down(keys.S) and not ctrl:
			move_z -= 1.0
		if engine.key_down(keys.A):
			move_x -= 1.0
		if engine.key_down(keys.D):
			move_x += 1.0
		if engine.key_down(keys.E) or engine.key_down(keys.SPACE):
			move_y += 1.0
		if engine.key_down(keys.Q):
			move_y -= 1.0

		rmb = engine.mouse_button_down(hyperlite.MouseButtons.Right)
		if rmb and not st._prev_rmb:
			engine.set_mouse_captured(True)
		if not rmb and st._prev_rmb:
			engine.set_mouse_captured(False)
		st._prev_rmb = rmb
		if rmb and engine.mouse_captured() and st.view != ViewMode.TOP_DOWN:
			dx, dy = engine.mouse_delta()
			camera.apply_look(dx, dy)

		speed = FLY_SPEED * (2.0 if shift else 1.0)
		if ctrl:
			speed *= 0.35
		camera.move_speed = speed
		if st.view == ViewMode.TOP_DOWN:
			camera.move(move_x, move_z, dt)
			x, _y, z = camera.position
			camera.position = (x, max(12.0, st.room.ceiling_y + 8.0), z)
			camera.pitch = -1.45
		elif st.view == ViewMode.FIRST_PERSON:
			camera.move(move_x, move_z, dt)
		else:
			camera.move(move_x, move_z, dt)
			if move_y:
				x, y, z = camera.position
				camera.position = (x, y + move_y * speed * dt, z)

		# Mouse: select, drag-move, Shift+click place
		lmb = engine.mouse_button_down(hyperlite.MouseButtons.Left)
		clicked = lmb and not st._prev_lmb
		released = (not lmb) and st._prev_lmb
		st._prev_lmb = lmb

		hit = None if rmb else self._cursor_hit(camera, engine, width, height)

		if clicked and hit is not None:
			if shift and self.library_item is not None:
				self._place_library(hit)
			else:
				self._select_at(hit)
				if st.selection.kind == "none" and self.library_item is not None:
					self._place_library(hit)
				elif st.selection.kind != "none":
					st._dragging = True
					st._drag_last = hit
					st._drag_undo_pushed = False

		if lmb and st._dragging and hit is not None and not shift:
			last = st._drag_last
			if last is None or abs(hit[0] - last[0]) > 1e-4 or abs(hit[2] - last[2]) > 1e-4:
				self._move_selection_to(hit, push_undo=True)
				st._drag_last = hit

		if released:
			if st._dragging and st._drag_undo_pushed:
				self._rebuild()
				st.status = "Moved"
			st._dragging = False
			st._drag_last = None
			st._drag_undo_pushed = False

		return True

	def draw(self, engine: hyperlite.Engine, camera: Camera, collector: ScreenLineCollector, width: int, height: int) -> None:
		"""Render viewport + HUD."""
		engine.clear(0, 0, 0, 255)
		cache = ProjectedVertexCache(camera, width, height, DEFAULT_FOV_Y)
		collector.clear()
		collector.reserve(8192)
		st = self.state

		if st.scanner_preview and st.scan is not None and st.scan.is_active:
			# Draw scanned segments only (true scanner look).
			from renderer import draw_wireframe_scene
			from settings_store import load_settings
			settings = load_settings()
			room = st.baked
			if room is not None:
				draw_wireframe_scene(
					engine, camera, room.environment_edges, room.occluder_hulls, st.scan,
					room.occluders, room.boundaries, width, height, settings,
					scanner_ratio=1.0, scan_time=st.scan.sim_time,
				)
				self._draw_hud(engine, width, height)
				return

		if st.baked is not None:
			for start, end in st.baked.environment_edges:
				_queue_world_segment_packed(collector, cache, start, end, SCAN_PACKED)
			for _b, edges in st.baked.occluder_hulls:
				for start, end in edges:
					_queue_world_segment_packed(collector, cache, start, end, SCAN_PACKED)

		# Selection highlight
		sel = st.selection
		if sel.kind == "op" and 0 <= sel.index < len(st.room.ops):
			bounds = _op_bounds(st.room.ops[sel.index], st.room.floor_y)
			if bounds:
				for a, b in _aabb_edges(bounds):
					_queue_world_segment_packed(collector, cache, a, b, SELECT)
		if sel.kind == "spawn":
			sx, sy, sz = st.room.spawn
			for a, b in _aabb_edges(((sx - 0.2, sy, sz - 0.2), (sx + 0.2, sy + 1.6, sz + 0.2))):
				_queue_world_segment_packed(collector, cache, a, b, SELECT)
		# Always show spawn marker.
		sx, sy, sz = st.room.spawn
		for a, b in _aabb_edges(((sx - 0.2, sy, sz - 0.2), (sx + 0.2, sy + 1.6, sz + 0.2))):
			_queue_world_segment_packed(collector, cache, a, b, SPAWN_C)

		for snd in st.room.sounds:
			pos = snd.get("position") or [0, 0, 0]
			for a, b in _aabb_edges(((pos[0] - 0.2, pos[1] - 0.2, pos[2] - 0.2), (pos[0] + 0.2, pos[1] + 0.2, pos[2] + 0.2))):
				_queue_world_segment_packed(collector, cache, a, b, SOUND_C)
		for ent in st.room.entities:
			pos = ent.get("position") or [0, 0, 0]
			for a, b in _aabb_edges(((pos[0] - 0.25, pos[1], pos[2] - 0.25), (pos[0] + 0.25, pos[1] + 1.4, pos[2] + 0.25))):
				_queue_world_segment_packed(collector, cache, a, b, ENTITY_C)
		for trig in st.room.gameplay.get("triggers") or []:
			b = trig.get("bounds")
			if b:
				for a, c in _aabb_edges(((b[0], b[1], b[2]), (b[3], b[4], b[5]))):
					_queue_world_segment_packed(collector, cache, a, c, TRIGGER_C)

		# Ghost
		hit = self._cursor_hit(camera, engine, width, height)
		if hit is not None and self.library_item is not None:
			payload = make_library_op(
				self.library_item, hit,
				size=st.size, place_y=st.place_y, floor_y=st.room.floor_y, yaw_deg=st.yaw_deg,
			)
			if payload and "op" in payload:
				bounds = _op_bounds(payload, st.room.floor_y)
				if bounds:
					for a, b in _aabb_edges(bounds):
						_queue_world_segment_packed(collector, cache, a, b, GHOST)
			hx, hy, hz = hit
			for a, b in (((hx - 0.15, hy, hz), (hx + 0.15, hy, hz)), ((hx, hy, hz - 0.15), (hx, hy, hz + 0.15))):
				_queue_world_segment_packed(collector, cache, a, b, GHOST)

		collector.flush(engine)
		self._draw_hud(engine, width, height)

	def _draw_hud(self, engine: hyperlite.Engine, width: int, height: int) -> None:
		"""Text overlays."""
		st = self.state
		dirty = "*" if st.dirty else ""
		draw_text(engine, 12, 10, f"FACILITY EDITOR{dirty}  {st.status}", 180, 255, 200, scale=1)
		brush = self.library_item.label if self.library_item else "SELECT"
		slots = ["0:SEL"] + [f"{i+1}:{it.label[:7]}" for i, it in enumerate(_HOTBAR_ITEMS)]
		hotbar = " ".join(("[" + s + "]") if i == st.hotbar else s for i, s in enumerate(slots))
		draw_text(
			engine, 12, 28,
			f"{st.tab.name} | {st.view.name} | brush:{brush} | Y:{st.place_y:.2f} size:{st.size:.2f} snap:{'ON' if st.snap else 'OFF'}",
			140, 200, 160, scale=1,
		)
		draw_text(engine, 12, height - 34, hotbar[:110], 100, 140, 120, scale=1)
		if st.tab == EditorTab.ROOM:
			ap = st.room.audio_profile
			draw_text(
				engine, 12, 46,
				f"Room {st.room.room_id} mat:{st.room.material} size:{st.room.size} conn:{','.join(st.room.connections) or '-'}",
				160, 180, 220, scale=1,
			)
			draw_text(
				engine, 12, 64,
				f"AudioProfile {ap.profile_id} rev:{ap.reverb:.2f} abs:{ap.absorption:.2f} T:{ap.temperature_c:.0f}C RH:{ap.humidity_percent:.0f}%",
				160, 180, 220, scale=1,
			)
		elif st.tab == EditorTab.AUDIO:
			draw_text(engine, 12, 46, f"Sounds: {len(st.room.sounds)}  (place Ambient/One-shot/Moving from library)", 160, 200, 255, scale=1)
			for i, snd in enumerate(st.room.sounds[:6]):
				pos = snd.get("position") or [0, 0, 0]
				draw_text(engine, 12, 64 + i * 16, f"  [{i}] {snd.get('asset_key')} @ ({pos[0]:.1f},{pos[1]:.1f},{pos[2]:.1f}) vol={snd.get('volume', 0)}", 120, 180, 220, scale=1)
		elif st.tab == EditorTab.ENTITIES:
			draw_text(engine, 12, 46, f"Entities: {len(st.room.entities)}", 255, 160, 160, scale=1)
			for i, ent in enumerate(st.room.entities[:6]):
				pos = ent.get("position") or [0, 0, 0]
				draw_text(engine, 12, 64 + i * 16, f"  [{i}] {ent.get('type')} {ent.get('behavior')} @ ({pos[0]:.1f},{pos[2]:.1f})", 220, 140, 140, scale=1)
		elif st.tab == EditorTab.WORLD:
			cats = items_by_category()
			y = 46
			for cat, items in cats.items():
				draw_text(engine, 12, y, f"{cat}: " + ", ".join(it.label for it in items[:6]), 130, 160, 140, scale=1)
				y += 16
		if st.show_help:
			y = height - 18 * (len(self.HELP) + 1)
			for line in self.HELP:
				draw_text(engine, 12, y, line, 110, 150, 130, scale=1)
				y += 16


def _parse_args() -> argparse.Namespace:
	"""CLI."""
	parser = argparse.ArgumentParser(description="NULLSIGHT facility level editor")
	parser.add_argument("room", nargs="?", default="", help="Room JSON path or room_id")
	parser.add_argument("--blank", action="store_true", help="New blank room")
	parser.add_argument("--frames", type=int, default=0, help="Smoke-test N frames then exit")
	return parser.parse_args()


def _load_session(args: argparse.Namespace) -> tuple[RoomDocument, FacilityDocument]:
	"""Resolve room + facility for this editor session."""
	ensure_facility_dirs()
	migrate_legacy_awakening()
	facility = FacilityDocument.load()
	if args.blank:
		room = new_blank_room(room_id="untitled")
		return room, facility
	if args.room:
		path = Path(args.room)
		if path.suffix == ".json" and path.is_file():
			return RoomDocument.load(path), facility
		candidate = rooms_dir() / f"{args.room}.json"
		if candidate.is_file():
			return RoomDocument.load(candidate), facility
	return facility.load_start_room(), facility


def main() -> None:
	"""Editor entry point — tested via ``--frames``."""
	args = _parse_args()
	room, facility = _load_session(args)

	engine = hyperlite.Engine(EDITOR_WIDTH, EDITOR_HEIGHT, "cpu", "NULLSIGHT Facility Editor")
	configure_bench_engine(engine, double_buffer=True)
	engine.set_mouse_captured(False)

	sx, sy, sz = room.spawn
	camera = Camera(position=(sx, sy + 1.2, sz), yaw=0.0, pitch=-0.25)
	camera.look_sensitivity = LOOK_SENS
	camera.move_speed = FLY_SPEED

	app = LevelEditorApp(room, facility)
	collector = ScreenLineCollector()
	prev = time.perf_counter()
	frames_left = args.frames if args.frames > 0 else None

	while engine.is_running():
		now = time.perf_counter()
		dt = min(0.05, now - prev)
		prev = now
		engine.poll_events()
		width, height = engine.window_size()
		if width <= 0 or height <= 0:
			width, height = EDITOR_WIDTH, EDITOR_HEIGHT
		if not app.handle_input(engine, camera, dt, width, height):
			break
		engine.begin_frame()
		app.draw(engine, camera, collector, width, height)
		engine.end_frame()
		engine.present()
		if frames_left is not None:
			frames_left -= 1
			if frames_left <= 0:
				break

	if app.state.dirty and args.frames <= 0:
		app.save()


if __name__ == "__main__":
	main()
