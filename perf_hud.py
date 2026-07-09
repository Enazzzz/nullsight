"""FPS and frame-phase timing overlay for NULLSIGHT profiling."""

from __future__ import annotations

import time
from collections import deque

import hyperlite

from settings import OsdSettings
from ui_text import rasterize_text_block

# HUD palette — matches scan phosphor green.
_HUD_COLOR = (48, 255, 120, 255)
_HUD_SHADOW = (0, 0, 0, 180)
_HUD_SCALE = 2
_HUD_MARGIN = 10
_LINE_HEIGHT = 7 * _HUD_SCALE + 4
# Rolling window for avg/min/max FPS (seconds).
_FPS_WINDOW_SECONDS = 6.0
# Rebuild the cached OSD bitmap at this interval; still blitted every frame.
_OSD_REFRESH_SECONDS = 0.5


class PerfHud:
	"""Smoothed FPS, 6s rolling stats, and per-phase millisecond breakdown."""

	def __init__(self, smoothing: float = 0.12) -> None:
		self._smoothing = smoothing
		self._fps_cur = 0.0
		self._elapsed = 0.0
		self._fps_window: deque[tuple[float, float]] = deque()
		self._phase_mark = 0.0
		self._inp_ms = 0.0
		self._sim_ms = 0.0
		self._scene_ms = 0.0
		self._ui_ms = 0.0
		self._rast_ms = 0.0
		self._queue_ms = 0.0
		self._present_ms = 0.0
		self._last_osd_refresh = -_OSD_REFRESH_SECONDS
		self._cached_osd_key: tuple[bool, ...] | None = None
		self._cached_rgba: bytes | None = None
		self._cached_w = 0
		self._cached_h = 0

	def begin_phases(self) -> None:
		"""Start phase timing for the current frame (call before poll/input)."""
		self._phase_mark = time.perf_counter()

	def stamp_input_phase(self) -> None:
		"""Close the input phase: poll, input, and pause menu logic."""
		self._inp_ms = self._smooth_ms(self._inp_ms, self._phase_mark)
		self._phase_mark = time.perf_counter()

	def stamp_sim_phase(self) -> None:
		"""Close the sim phase: camera movement and scan device update."""
		self._sim_ms = self._smooth_ms(self._sim_ms, self._phase_mark)
		self._phase_mark = time.perf_counter()

	def stamp_scene_phase(self) -> None:
		"""Close the scene phase: wireframe projection, occlusion, and line queue."""
		self._scene_ms = self._smooth_ms(self._scene_ms, self._phase_mark)
		self._phase_mark = time.perf_counter()

	def stamp_ui_phase(self) -> None:
		"""Close the UI phase: OSD and pause menu draw commands."""
		self._ui_ms = self._smooth_ms(self._ui_ms, self._phase_mark)
		self._phase_mark = time.perf_counter()

	def stamp_rast_phase(self) -> None:
		"""Close the raster phase: ``end_frame`` sort + CPU line raster."""
		self._rast_ms = self._smooth_ms(self._rast_ms, self._phase_mark)
		self._phase_mark = time.perf_counter()

	def stamp_present_phase(self) -> None:
		"""Close the present phase: GDI blit and optional vsync wait."""
		self._present_ms = self._smooth_ms(self._present_ms, self._phase_mark)
		self._phase_mark = time.perf_counter()

	def _smooth_ms(self, current: float, since: float) -> float:
		"""Return an exponential moving average of elapsed milliseconds."""
		elapsed_ms = (time.perf_counter() - since) * 1000.0
		if current <= 0.0:
			return elapsed_ms
		blend = self._smoothing
		return current * (1.0 - blend) + elapsed_ms * blend

	def _prune_fps_window(self) -> None:
		"""Drop FPS samples older than the rolling window."""
		cutoff = self._elapsed - _FPS_WINDOW_SECONDS
		while self._fps_window and self._fps_window[0][0] < cutoff:
			self._fps_window.popleft()

	def update(self, dt: float) -> None:
		"""Advance FPS stats from frame delta time."""
		if dt <= 0.0:
			return

		instant = 1.0 / dt
		self._elapsed += dt
		self._fps_window.append((self._elapsed, instant))
		self._prune_fps_window()

		if self._fps_cur <= 0.0:
			self._fps_cur = instant
			return

		blend = self._smoothing
		self._fps_cur = self._fps_cur * (1.0 - blend) + instant * blend

	@property
	def fps_cur(self) -> float:
		"""Smoothed instantaneous FPS."""
		return self._fps_cur

	@property
	def fps_avg(self) -> float:
		"""Mean instantaneous FPS over the last 6 seconds."""
		if not self._fps_window:
			return 0.0
		total = sum(fps for _time, fps in self._fps_window)
		return total / len(self._fps_window)

	@property
	def fps_min(self) -> float:
		"""Lowest instantaneous FPS in the last 6 seconds."""
		if not self._fps_window:
			return 0.0
		return min(fps for _time, fps in self._fps_window)

	@property
	def fps_max(self) -> float:
		"""Highest instantaneous FPS in the last 6 seconds."""
		if not self._fps_window:
			return 0.0
		return max(fps for _time, fps in self._fps_window)

	def sample_engine_timings(self, engine: hyperlite.Engine) -> None:
		"""Read Hyperlite queue time (begin_frame → start of end_frame, no raster)."""
		record_ms, _upload_ms, _kernel_ms, _readback_ms, _present_engine = engine.gpu_timings()
		blend = self._smoothing
		self._queue_ms = self._queue_ms * (1.0 - blend) + record_ms * blend
		# Present is measured in Python around present(); engine present_ms is similar.
		del _present_engine

	def _format_ms(self, milliseconds: float) -> str:
		"""Format milliseconds for the bitmap font (one decimal place)."""
		return f"{milliseconds:.1f}ms"

	def _osd_toggle_key(self, osd: OsdSettings) -> tuple[bool, ...]:
		"""Hash of which OSD lines are enabled — invalidates cache when toggles change."""
		return (
			osd.fps_cur,
			osd.fps_avg,
			osd.fps_min,
			osd.fps_max,
			osd.inp_ms,
			osd.sim_ms,
			osd.scene_ms,
			osd.ui_ms,
			osd.rast_ms,
			osd.rec_ms,
			osd.pre_ms,
		)

	def _build_osd_lines(self, osd: OsdSettings) -> list[str]:
		"""Format the enabled stat lines for the current sample."""
		lines: list[str] = []
		if osd.fps_cur:
			lines.append(f"fps cur {int(self._fps_cur + 0.5)}")
		if osd.fps_avg:
			lines.append(f"fps avg {int(self.fps_avg + 0.5)}")
		if osd.fps_min:
			lines.append(f"fps min {int(self.fps_min + 0.5)}")
		if osd.fps_max:
			lines.append(f"fps max {int(self.fps_max + 0.5)}")
		if osd.inp_ms:
			lines.append(f"inp {self._format_ms(self._inp_ms)}")
		if osd.sim_ms:
			lines.append(f"sim {self._format_ms(self._sim_ms)}")
		if osd.scene_ms:
			lines.append(f"scene {self._format_ms(self._scene_ms)}")
		if osd.ui_ms:
			lines.append(f"ui {self._format_ms(self._ui_ms)}")
		if osd.rast_ms:
			lines.append(f"rast {self._format_ms(self._rast_ms)}")
		if osd.rec_ms:
			lines.append(f"queue {self._format_ms(self._queue_ms)}")
		if osd.pre_ms:
			lines.append(f"pre {self._format_ms(self._present_ms)}")
		return lines

	def _clear_osd_cache(self) -> None:
		"""Drop cached OSD pixels when the overlay is hidden."""
		self._cached_rgba = None
		self._cached_w = 0
		self._cached_h = 0
		self._cached_osd_key = None
		self._last_osd_refresh = -_OSD_REFRESH_SECONDS

	def _refresh_osd_cache(self, osd: OsdSettings) -> None:
		"""Rasterize OSD text into a small RGBA buffer (expensive — throttled)."""
		lines = self._build_osd_lines(osd)
		rgba, width, height = rasterize_text_block(
			lines,
			scale=_HUD_SCALE,
			fg_rgb=_HUD_COLOR[:3],
			shadow_rgb=_HUD_SHADOW[:3],
			shadow_alpha=_HUD_SHADOW[3],
			line_height=_LINE_HEIGHT,
		)
		self._cached_rgba = rgba
		self._cached_w = width
		self._cached_h = height
		self._cached_osd_key = self._osd_toggle_key(osd)
		self._last_osd_refresh = self._elapsed

	def draw(self, engine: hyperlite.Engine, osd: OsdSettings) -> None:
		"""Blit cached OSD stats; refresh text at ``_OSD_REFRESH_SECONDS``."""
		if not osd.any_enabled():
			self._clear_osd_cache()
			return

		toggle_key = self._osd_toggle_key(osd)
		needs_refresh = (
			self._cached_rgba is None
			or toggle_key != self._cached_osd_key
			or self._elapsed - self._last_osd_refresh >= _OSD_REFRESH_SECONDS
		)
		if needs_refresh:
			self._refresh_osd_cache(osd)

		if self._cached_rgba and self._cached_w > 0 and self._cached_h > 0:
			engine.blit_rgba(
				self._cached_rgba,
				_HUD_MARGIN,
				_HUD_MARGIN,
				self._cached_w,
				self._cached_h,
			)
