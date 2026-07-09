"""Frame-rate limiting helpers for unfocused background play."""

from __future__ import annotations

import ctypes
import sys
import time

UNFOCUSED_FPS_CAP = 10.0


def enable_precise_sleep() -> None:
	"""Request 1 ms timer resolution on Windows so short sleeps are usable."""
	if sys.platform != "win32":
		return
	try:
		ctypes.windll.winmm.timeBeginPeriod(1)
	except (AttributeError, OSError):
		pass


def disable_precise_sleep() -> None:
	"""Restore default timer resolution."""
	if sys.platform != "win32":
		return
	try:
		ctypes.windll.winmm.timeEndPeriod(1)
	except (AttributeError, OSError):
		pass


def wait_until_frame_cap(frame_start: float, fps_cap: float) -> None:
	"""Sleep until ``frame_start + 1/fps_cap`` for a simple background FPS limit."""
	if fps_cap <= 0.0:
		return

	deadline = frame_start + (1.0 / fps_cap)
	while True:
		remaining = deadline - time.perf_counter()
		if remaining <= 0.0:
			return
		if remaining > 0.003:
			time.sleep(remaining - 0.002)
