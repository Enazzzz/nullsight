"""Procedural early-field IR from a specular/diffuse tap cluster.

Builds a short stereo impulse (256–512 samples) that bridges discrete early
taps into the statistical late Schroeder bus — Steam Audio / Wayverb hybrid
pattern without a full zone bake.
"""

from __future__ import annotations

import math

from acoustics.types import ReflectionTap
from math3d import Vec3

# Default IR length (~10.7 ms at 48 kHz) — early coloration only.
_DEFAULT_IR_SAMPLES = 512
# Soft Gaussian half-width around each tap sample (samples).
_TAP_HALF_WIDTH = 3.0


def _arrival_pan(arrival_dir: Vec3) -> tuple[float, float]:
	"""Constant-power L/R gains from world arrival direction (X = right)."""
	x = arrival_dir[0]
	# Map −1…+1 → left/right with mild center bias.
	pan = max(-1.0, min(1.0, x))
	left = math.cos((pan + 1.0) * 0.25 * math.pi)
	right = math.sin((pan + 1.0) * 0.25 * math.pi)
	return left, right


def early_ir_from_taps(
	taps: tuple[ReflectionTap, ...] | list[ReflectionTap],
	*,
	sample_rate_hz: int = 48_000,
	num_samples: int = _DEFAULT_IR_SAMPLES,
	listener_yaw_rad: float = 0.0,
) -> tuple[list[float], list[float]]:
	"""Synthesize a short stereo IR from early-reflection taps.

	Each tap deposits a short Gaussian pulse at ``delay_s * sr``, panned by
	arrival direction, scaled by gain, and low-passed by a simple 1-pole
	approximation (wider pulse for duller taps).
	``listener_yaw_rad`` rotates arrival XZ into listener frame for pan.
	"""
	n = max(64, min(2048, num_samples))
	sr = max(8_000, sample_rate_hz)
	left = [0.0] * n
	right = [0.0] * n
	if not taps:
		return left, right

	cy = math.cos(-listener_yaw_rad)
	sy = math.sin(-listener_yaw_rad)

	for tap in taps:
		# Rotate arrival into listener yaw frame (XZ).
		ax, ay, az = tap.arrival_dir
		lx = ax * cy - az * sy
		lz = ax * sy + az * cy
		pan_l, pan_r = _arrival_pan((lx, ay, lz))
		center = tap.delay_s * float(sr)
		if center < 0.0 or center >= n - 1:
			continue
		# Dull taps → slightly wider pulse (less HF).
		width = _TAP_HALF_WIDTH * (1.0 + max(0.0, 1.0 - tap.lpf_hz / 8_000.0))
		amp = max(0.0, min(1.2, tap.gain))
		i0 = max(0, int(center - width * 3.0))
		i1 = min(n, int(center + width * 3.0) + 1)
		for i in range(i0, i1):
			t = (float(i) - center) / max(0.5, width)
			env = math.exp(-0.5 * t * t)
			left[i] += amp * pan_l * env
			right[i] += amp * pan_r * env

	# Normalize so peak ≤ 0.85 (leave headroom for dry + late).
	peak = max(1e-6, max(abs(x) for x in left + right))
	scale = 0.85 / peak
	left = [x * scale for x in left]
	right = [x * scale for x in right]
	return left, right


def early_ir_energy(left: list[float], right: list[float]) -> float:
	"""Return RMS energy of a stereo IR (diagnostic)."""
	if not left:
		return 0.0
	acc = sum(l * l + r * r for l, r in zip(left, right))
	return math.sqrt(acc / max(1, len(left)))
