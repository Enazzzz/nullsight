"""Hyperlite engine tuning for NULLSIGHT (see guide §7 present paths + vsync)."""

import hyperlite

from settings import GameSettings

# Match Hyperlite defaults; explicit so dense scan frames grow predictably.
COMMAND_BUFFER_RESERVE = 65536
LINE_SORT_THRESHOLD = 64


def apply_engine_sync(engine: hyperlite.Engine, settings: GameSettings) -> None:
	"""Push vsync and async present settings to Hyperlite.

	Keep double-buffered present enabled alongside vsync when requested — that
	combination previously locked 165 Hz on this project. Disabling async when
	vsync is on forces a synchronous full-frame GDI blit on the main thread and
	costs enough headroom to fall back to ~82 FPS (half of 165 Hz).
	"""
	engine.set_vsync(settings.vsync)
	engine.set_double_buffered_present(settings.double_buffered_present)


def configure_engine(engine: hyperlite.Engine, settings: GameSettings) -> None:
	"""Apply NULLSIGHT Hyperlite settings once at startup."""
	apply_engine_sync(engine, settings)
	engine.set_line_sort_threshold(LINE_SORT_THRESHOLD)
	engine.set_command_buffer_reserve(COMMAND_BUFFER_RESERVE)


def configure_bench_engine(engine: hyperlite.Engine, *, double_buffer: bool = True) -> None:
	"""Hyperlite tuning for uncapped benchmarks (vsync off, optional async present)."""
	engine.set_vsync(False)
	engine.set_line_sort_threshold(LINE_SORT_THRESHOLD)
	engine.set_command_buffer_reserve(COMMAND_BUFFER_RESERVE)
	engine.set_double_buffered_present(double_buffer)


def sync_vsync(engine: hyperlite.Engine, settings: GameSettings) -> None:
	"""Push vsync toggle to Hyperlite."""
	apply_engine_sync(engine, settings)


def sync_present_mode(engine: hyperlite.Engine, settings: GameSettings) -> None:
	"""Push async present preference to Hyperlite."""
	apply_engine_sync(engine, settings)
