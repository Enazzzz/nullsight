"""Sparse scan static overlay — health-gated, low pixel budget for performance."""

from __future__ import annotations

import numpy as np

from health_visual_fx import health_noise_intensity
from scan_distortion import scan_distortion_state

# Phosphor green bias (matches scan_palette).
_PHOSPHOR_R = 48
_PHOSPHOR_G = 255
_PHOSPHOR_B = 120
# Max lit pixels per frame as a fraction of the framebuffer.
_MAX_PIXEL_FRACTION = 0.006


def _frame_seed(time_s: float) -> int:
	"""Per-frame RNG seed from simulation time."""
	return int(time_s * 1_000_000.0) & 0xFFFFFFFF


def _build_noise_rgba(
	width: int,
	height: int,
	chaos: float,
	pulse: bool,
	time_s: float,
) -> bytes:
	"""Build a sparse RGBA static buffer — only random pixel specks, not full-frame fill."""
	rng = np.random.default_rng(_frame_seed(time_s))

	intensity = chaos ** 2.6 * (1.25 if pulse else 1.0)
	pixel_budget = int(width * height * intensity ** 3.0 * _MAX_PIXEL_FRACTION)
	pixel_budget = min(pixel_budget, width * height // 12)

	rgba = np.zeros((height, width, 4), dtype=np.uint8)
	if pixel_budget <= 0:
		return rgba.tobytes()

	ys = rng.integers(0, height, size=pixel_budget)
	xs = rng.integers(0, width, size=pixel_budget)
	lum = rng.integers(50, 255, size=pixel_budget, dtype=np.uint8)
	alph = rng.integers(35, min(180, int(60 + chaos * 120)), size=pixel_budget, dtype=np.uint8)

	rgba[ys, xs, 0] = (lum * _PHOSPHOR_R // 255).astype(np.uint8)
	rgba[ys, xs, 1] = lum
	rgba[ys, xs, 2] = (lum * _PHOSPHOR_B // 255).astype(np.uint8)
	rgba[ys, xs, 3] = alph

	# Rare horizontal tear — at most one or two bands when chaos is high.
	if chaos > 0.42 and pulse:
		band_count = int(1 + chaos * chaos * 2.0)
		for _ in range(band_count):
			y0 = int(rng.integers(0, max(1, height - 1)))
			band_h = int(rng.integers(1, 2 + int(chaos * 4)))
			y1 = min(height, y0 + band_h)
			band_alpha = int(70 + chaos * 90)
			rgba[y0:y1, :, 3] = np.maximum(rgba[y0:y1, :, 3], band_alpha)

	return rgba.tobytes()


def blit_scan_noise_overlay(
	engine,
	scanner_ratio: float,
	health_ratio: float,
	time_s: float,
	width: int,
	height: int,
) -> None:
	"""Composite sparse static when health is below 50% and scanner chaos is active."""
	if width <= 0 or height <= 0:
		return

	health_noise = health_noise_intensity(health_ratio)
	if health_noise <= 0.0:
		return

	scanner_chaos, pulse = scan_distortion_state(scanner_ratio, time_s)
	effective = scanner_chaos * health_noise
	if effective < 0.015 and health_noise < 0.02:
		return

	chaos = max(effective, health_noise * 0.35)
	rgba = _build_noise_rgba(width, height, chaos, pulse, time_s)
	engine.blit_rgba(rgba, 0, 0, width, height)
