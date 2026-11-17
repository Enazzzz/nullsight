"""Frame budget breakdown — target 230 FPS (~4.35 ms)."""

from __future__ import annotations

import time

import hyperlite

from acoustics import AcousticSystem
from acoustics.types import AcousticSource
from camera import Camera
from engine_config import configure_bench_engine
from levels import load_world
from renderer import draw_wireframe_scene
from scan import ScanDevice
from settings_store import load_settings


def p50(xs: list[float]) -> float:
	"""Median of a sample list."""
	ordered = sorted(xs)
	return ordered[len(ordered) // 2]


def main() -> None:
	"""Run a timed gameplay-like loop and print per-phase costs."""
	settings = load_settings()
	room, door = load_world()
	eng = hyperlite.Engine(1280, 720, "cpu", "bench")
	# Uncapped present — vsync on would floor the bench near display refresh.
	configure_bench_engine(eng, double_buffer=True)
	cam = Camera(position=(6.0, 0.0, 6.0))
	scan = ScanDevice()
	eye = cam.eye_position()
	scan.set_frame_hints(settings, 0.0)
	scan.update(
		1 / 60,
		True,
		eye,
		0.0,
		0.2,
		room.environment_edges,
		room.occluder_hulls,
		room.occluders,
		room.boundaries,
		1280,
		720,
	)
	scan.update(0.8, False, eye, 0.0, 0.2)
	print(
		"segs",
		len(scan.memory.segments),
		"faces",
		len(scan.memory.scanned_faces),
		"active",
		len(scan.active_segments),
	)

	sys = AcousticSystem()
	sys.notify_level_loaded(room)
	src = [AcousticSource(source_id=1, position=(0.85, 0.7, 5.4), enabled=True)]

	parts: dict[str, list[float]] = {
		name: []
		for name in ("poll", "move", "scan_upd", "ac", "draw", "present", "total")
	}
	for i in range(200):
		t0 = time.perf_counter()

		t = time.perf_counter()
		eng.poll_events()
		eng.begin_frame()
		parts["poll"].append((time.perf_counter() - t) * 1000)

		t = time.perf_counter()
		cam.move_with_collision(
			0.4 if (i // 40) % 2 == 0 else -0.4,
			0.2,
			1 / 60,
			room.collidables,
			room.bounds,
			base_floor_y=room.floor_y,
			ceiling_y=room.ceiling_y,
			walkables=room.walkables,
		)
		cam.apply_look(0.8, 0.0)
		parts["move"].append((time.perf_counter() - t) * 1000)

		eye = cam.eye_position()
		t = time.perf_counter()
		scan.set_frame_hints(settings, 4.0)
		scan.update(
			0.001,
			False,
			eye,
			cam.yaw,
			cam.pitch,
			room.environment_edges,
			room.occluder_hulls,
			room.occluders,
			room.boundaries,
			1280,
			720,
		)
		parts["scan_upd"].append((time.perf_counter() - t) * 1000)

		t = time.perf_counter()
		sys.update(sys.snapshot(room, door, src, cam), 0.016)
		parts["ac"].append((time.perf_counter() - t) * 1000)

		t = time.perf_counter()
		draw_wireframe_scene(
			eng,
			cam,
			room.environment_edges,
			room.occluder_hulls,
			scan,
			room.occluders,
			room.boundaries,
			1280,
			720,
			settings,
			camera_speed_sq=4.0,
			scanner_ratio=1.0,
			scan_time=scan.sim_time,
		)
		parts["draw"].append((time.perf_counter() - t) * 1000)

		t = time.perf_counter()
		eng.end_frame()
		eng.present()
		parts["present"].append((time.perf_counter() - t) * 1000)

		parts["total"].append((time.perf_counter() - t0) * 1000)

	print("TARGET 230fps = 4.35ms")
	for name, samples in parts.items():
		print(f"  {name:10s} p50={p50(samples):.3f}ms")
	print(f"  fps p50={1000.0 / p50(parts['total']):.0f}")


if __name__ == "__main__":
	main()
