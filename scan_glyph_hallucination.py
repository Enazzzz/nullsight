"""Drifting random glyph hallucinations — health-gated, entirely unpredictable motion."""

from __future__ import annotations

import math

import numpy as np

from health_visual_fx import health_noise_intensity
from ui_text import rasterize_glyph

# Characters eligible for hallucinated glyphs.
_GLYPH_ALPHABET = (
	"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
	"?!@#$%&*+=<>"
)
_MIN_NOISE = 0.008


def _hash01(*parts: float | int) -> float:
	"""Deterministic 0–1 hash."""
	value = 0
	for part in parts:
		value = (value * 1_316_021 + int(part * 10_007.0) + hash(part) % 997) & 0xFFFFFFFF
	return (value % 10_000) / 10_000.0


class _DriftingGlyph:
	"""One glyph drifting toward a random screen target."""

	__slots__ = ("char", "scale", "x", "y", "target_x", "target_y", "speed", "alpha", "seed")

	def __init__(
		self,
		char: str,
		scale: int,
		x: float,
		y: float,
		target_x: float,
		target_y: float,
		speed: float,
		alpha: int,
		seed: float,
	) -> None:
		self.char = char
		self.scale = scale
		self.x = x
		self.y = y
		self.target_x = target_x
		self.target_y = target_y
		self.speed = speed
		self.alpha = alpha
		self.seed = seed


class ScanGlyphHallucination:
	"""Maintains a random pool of glyphs that jump targets and wander the viewport."""

	def __init__(self) -> None:
		self._pool: list[_DriftingGlyph] = []
		self._last_time: float | None = None
		self._last_reshuffle_s: float = -1.0

	def _pick_char(self, rng: np.random.Generator) -> str:
		"""Return one random glyph character."""
		index = int(rng.integers(0, len(_GLYPH_ALPHABET)))
		return _GLYPH_ALPHABET[index]

	def _spawn_glyph(
		self,
		rng: np.random.Generator,
		width: int,
		height: int,
		chaos: float,
		index: int,
		time_s: float,
	) -> _DriftingGlyph:
		"""Create a glyph at a random place with a random destination."""
		seed = index * 1.73 + time_s * (5.0 + _hash01(index, time_s) * 13.0)
		x = float(rng.integers(0, max(1, width - 8)))
		y = float(rng.integers(0, max(1, height - 10)))
		target_x = float(rng.integers(0, max(1, width - 8)))
		target_y = float(rng.integers(0, max(1, height - 10)))
		scale = int(rng.integers(1, 3 if chaos < 0.35 else 4))
		speed = 0.4 + _hash01(seed) * 2.2 + chaos ** 2.2 * (4.0 + _hash01(seed, 0.3) * 10.0)
		alpha = int(55 + chaos ** 1.6 * 180 + _hash01(seed, 0.7) * 40)
		return _DriftingGlyph(
			self._pick_char(rng),
			scale,
			x,
			y,
			target_x,
			target_y,
			speed,
			min(255, alpha),
			seed,
		)

	def _maybe_reshuffle(
		self,
		rng: np.random.Generator,
		chaos: float,
		time_s: float,
		width: int,
		height: int,
	) -> None:
		"""Randomly change glyph count and teleport some targets."""
		interval = 0.08 + _hash01(time_s * 0.7) * (0.55 - chaos * 0.25)
		if time_s - self._last_reshuffle_s < interval:
			return
		self._last_reshuffle_s = time_s

		max_count = int(chaos ** 2.6 * (2.0 + _hash01(time_s, chaos) * 16.0))
		if max_count <= 0:
			self._pool.clear()
			return

		target_count = int(rng.integers(0, max_count + 1))
		while len(self._pool) < target_count:
			self._pool.append(
				self._spawn_glyph(rng, width, height, chaos, len(self._pool), time_s)
			)
		while len(self._pool) > target_count:
			self._pool.pop(int(rng.integers(0, len(self._pool))))

		for glyph in self._pool:
			if rng.random() < 0.15 + chaos * 0.45:
				glyph.target_x = float(rng.integers(0, max(1, width - 8)))
				glyph.target_y = float(rng.integers(0, max(1, height - 10)))
				if rng.random() < 0.35 + chaos * 0.4:
					glyph.char = self._pick_char(rng)
					glyph.scale = int(rng.integers(1, 3 if chaos < 0.4 else 4))

	def blit(
		self,
		engine,
		health_ratio: float,
		time_s: float,
		width: int,
		height: int,
	) -> None:
		"""Update and draw drifting glyphs when health is below 50%."""
		if width <= 0 or height <= 0:
			return

		chaos = health_noise_intensity(health_ratio)
		if chaos < _MIN_NOISE:
			self._pool.clear()
			self._last_time = time_s
			return

		dt = 0.016
		if self._last_time is not None:
			dt = max(0.001, min(0.05, time_s - self._last_time))
		self._last_time = time_s

		seed = int(time_s * 1_000_000.0) & 0xFFFFFFFF
		rng = np.random.default_rng(seed ^ int(chaos * 1_000_000.0))
		self._maybe_reshuffle(rng, chaos, time_s, width, height)

		for glyph in self._pool:
			if rng.random() < 0.012 + chaos * 0.08:
				glyph.x = float(rng.integers(0, max(1, width - 8)))
				glyph.y = float(rng.integers(0, max(1, height - 10)))

			blend = min(1.0, dt * glyph.speed * (1.2 + chaos * 3.5))
			glyph.x += (glyph.target_x - glyph.x) * blend
			glyph.y += (glyph.target_y - glyph.y) * blend

			if math.hypot(glyph.target_x - glyph.x, glyph.target_y - glyph.y) < 2.5:
				if rng.random() < 0.25 + chaos * 0.5:
					glyph.target_x = float(rng.integers(0, max(1, width - 8)))
					glyph.target_y = float(rng.integers(0, max(1, height - 10)))

			rgba, gw, gh = rasterize_glyph(
				glyph.char,
				scale=glyph.scale,
				alpha=glyph.alpha,
			)
			if gw <= 0 or gh <= 0:
				continue
			px = int(glyph.x) % max(1, width - gw)
			py = int(glyph.y) % max(1, height - gh)
			engine.blit_rgba(rgba, px, py, gw, gh)


_GLYPH_OVERLAY = ScanGlyphHallucination()


def blit_glyph_hallucinations(
	engine,
	health_ratio: float,
	time_s: float,
	width: int,
	height: int,
) -> None:
	"""Draw health-gated drifting glyph noise."""
	_GLYPH_OVERLAY.blit(engine, health_ratio, time_s, width, height)
