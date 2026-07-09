"""NULLSIGHT render benchmarks — static baselines plus realistic gameplay stress."""

from __future__ import annotations

import argparse
import cProfile
import io
import math
import pstats
import statistics
import time
from dataclasses import dataclass

import hyperlite

from camera import Camera, DEFAULT_FOV_Y
from engine_config import configure_bench_engine
from renderer import (
	ProjectedVertexCache,
	_draw_fading_intervals,
	draw_wireframe_scene,
)
from levels import count_edges, load_level
from room import RoomData
from scan import ScanDevice
from scan_occluders import ScannedOccluderCache
from screen_batch import ScreenLineCollector
from settings import GameSettings


@dataclass
class BenchResult:
	"""One benchmark scenario outcome."""

	label: str
	fps_avg: float
	fps_p05: float
	fps_min: float
	frames: int
	core_lines: int
	native_lines: int
	active_segments: int
	environment_edges: int
	total_edges: int


@dataclass(frozen=True)
class CameraMotion:
	"""Per-frame look and move inputs simulating active gameplay."""

	look_dx: float
	look_dy: float
	move_x: float
	move_z: float


def _prime_scan(
	scan: ScanDevice,
	camera: Camera,
	room: RoomData,
	width: int,
	height: int,
	wave_time: float,
) -> None:
	"""Trigger one scan and advance to a wave/hold/fade phase."""
	scan.update(
		0.0,
		True,
		camera.eye_position(),
		camera.yaw,
		camera.pitch,
		room.environment_edges,
		room.occluder_hulls,
		room.occluders,
		room.boundaries,
		width,
		height,
		DEFAULT_FOV_Y,
	)
	scan.update(
		wave_time,
		False,
		camera.eye_position(),
		camera.yaw,
		camera.pitch,
	)


def _prime_multi_scan(
	scan: ScanDevice,
	camera: Camera,
	room: RoomData,
	width: int,
	height: int,
	positions: list[tuple[float, float, float]],
) -> None:
	"""Fire several scans from different positions to build dense persistent geometry."""
	for position in positions:
		camera.position = list(position)
		scan.update(
			0.0,
			True,
			camera.eye_position(),
			camera.yaw,
			camera.pitch,
			room.environment_edges,
			room.occluder_hulls,
			room.occluders,
			room.boundaries,
			width,
			height,
			DEFAULT_FOV_Y,
		)
		scan.update(0.01, False, camera.eye_position(), camera.yaw, camera.pitch)


def _gameplay_motion(frame: int, dt: float, *, fast: bool) -> CameraMotion:
	"""Return strafe + look inputs that mimic fast exploration."""
	look_scale = 1400.0 if fast else 900.0
	move_scale = 4.5 if fast else 3.0
	phase = frame * 0.11
	return CameraMotion(
		look_dx=look_scale * dt * math.sin(frame * 0.17),
		look_dy=look_scale * 0.45 * dt * math.cos(frame * 0.13),
		move_x=move_scale * math.sin(phase),
		move_z=move_scale * math.cos(phase * 0.83),
	)


def _apply_motion(camera: Camera, room: RoomData, motion: CameraMotion, dt: float) -> None:
	"""Apply one frame of simulated player input."""
	camera.apply_look(motion.look_dx, motion.look_dy)
	camera.move(motion.move_x, motion.move_z, dt)
	camera.clamp_to_room(*room.bounds[:2], *room.bounds[2:])


def _count_draw_load(
	camera: Camera,
	room: RoomData,
	scan: ScanDevice,
	width: int,
	height: int,
	settings: GameSettings,
	engine: hyperlite.Engine,
) -> tuple[int, int]:
	"""Count core and native line counts for one frame without presenting."""
	if not scan.is_active:
		return 0, 0

	live_eye = camera.eye_position()
	scanned_faces = scan.scanned_faces
	live_cache = (
		ScannedOccluderCache(live_eye, scanned_faces, scan.memory.face_index)
		if scanned_faces
		else None
	)
	cache = ProjectedVertexCache(camera, width, height, DEFAULT_FOV_Y)
	collector = ScreenLineCollector()

	scan.begin_render_frame()
	for segment, draw_intervals in scan.prepare_segment_draws(
		live_eye,
		scanned_faces,
		live_cache,
		view_cache=cache,
		fov_y=DEFAULT_FOV_Y,
	):
		_draw_fading_intervals(
			collector,
			cache,
			scan,
			segment,
			segment.start,
			segment.end,
			draw_intervals,
		)

	core = len(collector)
	native = collector.flush(engine)
	return core, native


def _fps_stats(frame_times: list[float]) -> tuple[float, float, float]:
	"""Return average, 5th percentile, and minimum FPS from frame durations."""
	if not frame_times:
		return 0.0, 0.0, 0.0
	fps_samples = [1.0 / duration for duration in frame_times if duration > 0.0]
	if not fps_samples:
		return 0.0, 0.0, 0.0
	fps_samples.sort()
	p05_index = max(0, int(len(fps_samples) * 0.05) - 1)
	return (
		statistics.mean(fps_samples),
		fps_samples[p05_index],
		fps_samples[0],
	)


def _boot_bench_engine(
	initial_width: int,
	initial_height: int,
	*,
	fullscreen: bool,
) -> tuple[hyperlite.Engine, int, int]:
	"""Create the bench engine; default matches in-game borderless fullscreen (F11)."""
	engine = hyperlite.Engine(initial_width, initial_height, "cpu", "NULLSIGHT bench")
	configure_bench_engine(engine)
	if fullscreen:
		engine.set_fullscreen(True)
		for _ in range(8):
			engine.poll_events()
	width, height = engine.window_size()
	return engine, width, height


def run_scenario(
	engine: hyperlite.Engine,
	width: int,
	height: int,
	label: str,
	room: RoomData,
	*,
	frames: int,
	warmup: int,
	wave_time: float,
	camera_position: tuple[float, float, float] | None = None,
	camera_yaw: float = 0.3,
	camera_pitch: float = -0.05,
	motion: str = "static",
	fast_motion: bool = False,
	multi_scan: bool = False,
) -> BenchResult:
	"""Benchmark one level configuration on an existing engine."""
	settings = GameSettings()
	spawn = camera_position if camera_position is not None else room.spawn
	camera = Camera(position=spawn, yaw=camera_yaw, pitch=camera_pitch)
	scan = ScanDevice()

	if multi_scan:
		center_x = (room.bounds[0] + room.bounds[1]) * 0.5
		center_z = (room.bounds[2] + room.bounds[3]) * 0.5
		positions = [
			(center_x, 0.0, center_z),
			(center_x + 8.0, 0.0, center_z + 6.0),
			(center_x - 6.0, 0.0, center_z - 4.0),
			(center_x + 3.0, 0.0, center_z - 8.0),
		]
		_prime_multi_scan(scan, camera, room, width, height, positions)
		scan.update(wave_time, False, camera.eye_position(), camera.yaw, camera.pitch)
	else:
		_prime_scan(scan, camera, room, width, height, wave_time)

	core, native = _count_draw_load(camera, room, scan, width, height, settings, engine)
	dt = 1.0 / 85.0
	total_frames = warmup + frames
	frame_times: list[float] = []
	prev_eye = list(camera.eye_position())
	camera_speed_sq = 0.0

	for frame in range(total_frames):
		if motion != "static":
			motion_input = _gameplay_motion(frame, dt, fast=fast_motion)
			_apply_motion(camera, room, motion_input, dt)
			scan.update(dt, False, camera.eye_position(), camera.yaw, camera.pitch)
			eye = camera.eye_position()
			dx = eye[0] - prev_eye[0]
			dy = eye[1] - prev_eye[1]
			dz = eye[2] - prev_eye[2]
			speed = (dx * dx + dy * dy + dz * dz) ** 0.5 / dt
			camera_speed_sq = speed * speed
			prev_eye[0], prev_eye[1], prev_eye[2] = eye[0], eye[1], eye[2]
		else:
			camera_speed_sq = 0.0

		t0 = time.perf_counter()
		engine.begin_frame()
		draw_wireframe_scene(
			engine,
			camera,
			room.environment_edges,
			room.occluder_hulls,
			scan,
			room.occluders,
			room.boundaries,
			width,
			height,
			settings,
			camera_speed_sq=camera_speed_sq,
		)
		engine.end_frame()
		engine.present()
		elapsed = time.perf_counter() - t0
		if frame >= warmup:
			frame_times.append(elapsed)

	fps_avg, fps_p05, fps_min = _fps_stats(frame_times)
	return BenchResult(
		label=label,
		fps_avg=fps_avg,
		fps_p05=fps_p05,
		fps_min=fps_min,
		frames=frames,
		core_lines=core,
		native_lines=native,
		active_segments=len(scan.active_segments),
		environment_edges=len(room.environment_edges),
		total_edges=count_edges(room),
	)


def _print_result(result: BenchResult) -> None:
	"""Print one benchmark row."""
	print(
		f"{result.label:32} avg={result.fps_avg:6.0f}  p05={result.fps_p05:6.0f}  "
		f"min={result.fps_min:6.0f}  seg={result.active_segments:4}  "
		f"core={result.core_lines:4}  native={result.native_lines:5}"
	)


def main() -> None:
	"""Run the benchmark suite."""
	parser = argparse.ArgumentParser(description="NULLSIGHT render benchmark")
	parser.add_argument("--frames", type=int, default=300, help="Measured frames per scenario")
	parser.add_argument("--warmup", type=int, default=60, help="Warmup frames excluded from stats")
	parser.add_argument("--width", type=int, default=960, help="Windowed startup width (fullscreen uses monitor size)")
	parser.add_argument("--height", type=int, default=540, help="Windowed startup height")
	parser.add_argument(
		"--windowed",
		action="store_true",
		help="Stay windowed at --width x --height instead of borderless fullscreen (F11 path)",
	)
	parser.add_argument("--wave-time", type=float, default=0.55, help="Seconds after scan trigger (static cases)")
	parser.add_argument("--profile", action="store_true", help="cProfile the realistic large scenario")
	args = parser.parse_args()

	fullscreen = not args.windowed
	engine, bench_w, bench_h = _boot_bench_engine(args.width, args.height, fullscreen=fullscreen)
	mode_label = "fullscreen" if fullscreen else "windowed"

	levels = (
		("small", load_level("small")),
		("large", load_level("large")),
	)
	modes = ("default",)

	print(
		f"NULLSIGHT benchmark  {bench_w}x{bench_h} {mode_label}  "
		f"frames={args.frames}  warmup={args.warmup}"
	)
	print(f"{'scenario':32} {'avg':>6}  {'p05':>6}  {'min':>6}   details")
	print("-" * 88)

	results: list[BenchResult] = []
	for level_name, room in levels:
		for mode_name in modes:
			label = f"{level_name}/{mode_name}"
			result = run_scenario(
				engine,
				bench_w,
				bench_h,
				label,
				room,
				frames=args.frames,
				warmup=args.warmup,
				wave_time=args.wave_time,
			)
			results.append(result)
			_print_result(result)

	large = load_level("large")
	center_x = (large.bounds[0] + large.bounds[1]) * 0.5
	center_z = (large.bounds[2] + large.bounds[3]) * 0.5
	static_stress = (
		("large/center-hold", 1.4),
		("large/center-fade", 2.6),
	)
	print("-" * 88)
	print("Static stress (center, no movement)")
	for label, wave in static_stress:
		result = run_scenario(
			engine,
			bench_w,
			bench_h,
			label,
			large,
			frames=args.frames,
			warmup=args.warmup,
			wave_time=wave,
			camera_position=(center_x, 0.0, center_z),
		)
		results.append(result)
		_print_result(result)

	realistic_cases = (
		("large/move-hold", 1.4, False, False),
		("large/move-fade", 2.6, False, False),
		("large/move-fast", 2.6, True, False),
		("large/multi+move", 2.6, True, True),
	)
	print("-" * 88)
	print("Realistic stress (movement during hold/fade, longer runs)")
	for label, wave, fast, multi in realistic_cases:
		result = run_scenario(
			engine,
			bench_w,
			bench_h,
			label,
			large,
			frames=args.frames,
			warmup=args.warmup,
			wave_time=wave,
			camera_position=(center_x, 0.0, center_z),
			motion="gameplay",
			fast_motion=fast,
			multi_scan=multi,
		)
		results.append(result)
		_print_result(result)

	if args.profile:
		print("\n--- cProfile: large/multi+move ---")
		room = load_level("large")
		settings = GameSettings()
		center_x = (room.bounds[0] + room.bounds[1]) * 0.5
		center_z = (room.bounds[2] + room.bounds[3]) * 0.5
		camera = Camera(position=(center_x, 0.0, center_z), yaw=0.3, pitch=-0.05)
		scan = ScanDevice()
		positions = [
			(center_x, 0.0, center_z),
			(center_x + 8.0, 0.0, center_z + 6.0),
			(center_x - 6.0, 0.0, center_z - 4.0),
			(center_x + 3.0, 0.0, center_z - 8.0),
		]
		_prime_multi_scan(scan, camera, room, bench_w, bench_h, positions)
		scan.update(2.6, False, camera.eye_position(), camera.yaw, camera.pitch)
		dt = 1.0 / 85.0

		profiler = cProfile.Profile()
		profiler.enable()
		for frame in range(args.warmup + args.frames):
			motion = _gameplay_motion(frame, dt, fast=True)
			_apply_motion(camera, room, motion, dt)
			scan.update(dt, False, camera.eye_position(), camera.yaw, camera.pitch)
			engine.begin_frame()
			draw_wireframe_scene(
				engine,
				camera,
				room.environment_edges,
				room.occluder_hulls,
				scan,
				room.occluders,
				room.boundaries,
				bench_w,
				bench_h,
				settings,
			)
			engine.end_frame()
			engine.present()
		profiler.disable()

		stream = io.StringIO()
		stats = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
		stats.print_stats(16)
		print(stream.getvalue())


if __name__ == "__main__":
	main()
