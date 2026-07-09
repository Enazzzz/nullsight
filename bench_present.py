"""Compare DXGI vs GDI present paths on a realistic NULLSIGHT workload."""

from __future__ import annotations

import argparse
import statistics
import time

import hyperlite

from bench import _boot_bench_engine, run_scenario
from camera import Camera, DEFAULT_FOV_Y
from levels import load_level
from renderer import draw_wireframe_scene
from scan import ScanDevice
from settings import GameSettings


def _sample_phase_timings(
	engine: hyperlite.Engine,
	room,
	width: int,
	height: int,
	*,
	samples: int,
) -> tuple[float, float, float]:
	"""Return mean scene, present, and total milliseconds from wall-clock splits."""
	settings = GameSettings()
	camera = Camera(position=room.spawn, yaw=0.3, pitch=-0.05)
	scan = ScanDevice()
	scene_samples: list[float] = []
	present_samples: list[float] = []
	total_samples: list[float] = []

	for _ in range(samples):
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
		)
		engine.end_frame()
		t1 = time.perf_counter()
		engine.present()
		t2 = time.perf_counter()
		scene_samples.append((t1 - t0) * 1000.0)
		present_samples.append((t2 - t1) * 1000.0)
		total_samples.append((t2 - t0) * 1000.0)

	return (
		statistics.mean(scene_samples),
		statistics.mean(present_samples),
		statistics.mean(total_samples),
	)


def _run_path(
	label: str,
	room,
	*,
	width: int,
	height: int,
	fullscreen: bool,
	frames: int,
	warmup: int,
	dxgi: bool,
	async_present: bool,
	vsync: bool,
	timing_samples: int,
) -> dict[str, float | str | bool]:
	"""Benchmark one present configuration."""
	engine, bench_w, bench_h = _boot_bench_engine(width, height, fullscreen=fullscreen)
	engine.set_dxgi_present(dxgi)
	engine.set_double_buffered_present(async_present)
	engine.set_vsync(vsync)

	result = run_scenario(
		engine,
		bench_w,
		bench_h,
		label,
		room,
		frames=frames,
		warmup=warmup,
		wave_time=0.55,
		motion="move",
		fast_motion=True,
		multi_scan=True,
	)
	raster_ms, present_ms, total_ms = _sample_phase_timings(
		engine,
		room,
		bench_w,
		bench_h,
		samples=timing_samples,
	)
	frame_ms = 1000.0 / result.fps_avg if result.fps_avg > 0.0 else 0.0
	return {
		"label": label,
		"dxgi_active": engine.dxgi_present_enabled(),
		"width": bench_w,
		"height": bench_h,
		"fps_avg": result.fps_avg,
		"fps_p05": result.fps_p05,
		"frame_ms": frame_ms,
		"scene_ms": raster_ms,
		"present_ms": present_ms,
		"total_ms": total_ms,
	}


def main() -> None:
	"""Run DXGI vs GDI comparison scenarios."""
	parser = argparse.ArgumentParser(description="NULLSIGHT present-path benchmark")
	parser.add_argument("--frames", type=int, default=180)
	parser.add_argument("--warmup", type=int, default=40)
	parser.add_argument("--width", type=int, default=960)
	parser.add_argument("--height", type=int, default=540)
	parser.add_argument("--windowed", action="store_true")
	parser.add_argument("--timing-samples", type=int, default=40)
	parser.add_argument("--vsync", action="store_true", help="Enable vsync for all runs")
	args = parser.parse_args()

	fullscreen = not args.windowed
	room = load_level("large")
	configs = (
		("DXGI + async", True, True),
		("GDI + async", False, True),
		("DXGI sync", True, False),
		("GDI sync", False, False),
	)
	results = [
		_run_path(
			label,
			room,
			width=args.width,
			height=args.height,
			fullscreen=fullscreen,
			frames=args.frames,
			warmup=args.warmup,
			dxgi=dxgi,
			async_present=async_present,
			vsync=args.vsync,
			timing_samples=args.timing_samples,
		)
		for label, dxgi, async_present in configs
	]

	mode = "windowed" if args.windowed else "fullscreen"
	res_w = int(results[0]["width"]) if results else args.width
	res_h = int(results[0]["height"]) if results else args.height
	print(
		f"NULLSIGHT present-path comparison  {res_w}x{res_h} {mode}  large/multi+move"
	)
	print(f"frames={args.frames}  warmup={args.warmup}  vsync={'on' if args.vsync else 'off'}")
	print(
		f"{'path':16} {'dxgi':>5} {'fps':>7} {'p05':>7} {'frame':>7} "
		f"{'scene':>7} {'pre':>7} {'gain':>6}"
	)
	print("-" * 72)

	baseline_fps = 0.0
	for row in results:
		if row["label"] == "GDI + async":
			baseline_fps = float(row["fps_avg"])

	for row in results:
		gain = 0.0
		if baseline_fps > 0.0:
			gain = (float(row["fps_avg"]) / baseline_fps - 1.0) * 100.0
		print(
			f"{row['label']:16} {str(row['dxgi_active']):>5} "
			f"{float(row['fps_avg']):7.0f} {float(row['fps_p05']):7.0f} "
			f"{float(row['frame_ms']):7.2f} {float(row['scene_ms']):7.2f} "
			f"{float(row['present_ms']):7.2f} {gain:+5.0f}%"
		)


if __name__ == "__main__":
	main()
