"""Source directivity — facing, pattern, and finite speaker size (baffled piston).

Physics:
- Pattern G_pattern(θ): cardioid / horn / omni (Kuttruff)
- Size: baffled circular piston H(θ,f) = 2 J₁(ka sinθ) / (ka sinθ)
  (Kinsler & Frey; Russell PSU demo) where k=2πf/c, a = piston radius
- Combined launch pressure per band: G = G_pattern(θ) · |H(θ,f)|
- Applied to the DIRECT path and to every specular/diffuse LAUNCH direction
  (first leg source → first bounce / listener), not only LOS

When ka ≪ 1 the piston is nearly omnidirectional; larger cones beam HF on-axis.
"""

from __future__ import annotations

import math

from acoustics.materials import BAND_COUNT, OCTAVE_HZ, SPEED_OF_SOUND_MPS
from math3d import Vec3, length, sub

# Cardioid / horn null floor (pressure).
_MIN_DIRECTIVITY_GAIN = 0.02
# Series terms for J1 (sufficient for |x| ≲ 20 used by game ka·sinθ).
_J1_SERIES_TERMS = 12


def _normalize(vec: Vec3) -> Vec3:
	"""Return a unit vector; fallback to +Z when degenerate."""
	mag = length(vec)
	if mag < 1e-9:
		return (0.0, 0.0, 1.0)
	inv = 1.0 / mag
	return (vec[0] * inv, vec[1] * inv, vec[2] * inv)


def _dot(a: Vec3, b: Vec3) -> float:
	"""Euclidean dot product."""
	return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def angle_between(a: Vec3, b: Vec3) -> float:
	"""Return the angle in radians between two vectors."""
	na = _normalize(a)
	nb = _normalize(b)
	return math.acos(max(-1.0, min(1.0, _dot(na, nb))))


def listener_to_source_dir(listener: Vec3, source: Vec3) -> Vec3:
	"""Unit vector from listener toward source (incoming direct path at listener)."""
	return _normalize(sub(source, listener))


def source_to_listener_dir(source: Vec3, listener: Vec3) -> Vec3:
	"""Unit vector from source toward listener (emission axis reference)."""
	return _normalize(sub(listener, source))


def launch_dir_from_path(path_points: list[Vec3]) -> Vec3:
	"""Unit launch direction at the source (first leg of the acoustic path).

	``path_points`` is ``[listener, bounce..., source]``. Sound leaves the
	source toward the previous point (last bounce, or listener if direct).
	"""
	if len(path_points) < 2:
		return (0.0, 0.0, 1.0)
	source = path_points[-1]
	prev = path_points[-2]
	return _normalize(sub(prev, source))


def arrival_dir_from_path(path_points: list[Vec3]) -> Vec3:
	"""Unit direction of the incoming wave at the listener (last leg of path).

	``path_points`` is ``[listener, bounce..., source]``. The wave travels
	source → bounces → listener, so arrival at the ear is along the last segment.
	"""
	if len(path_points) < 2:
		return (0.0, 0.0, 1.0)
	listener = path_points[0]
	last_bounce = path_points[-2] if len(path_points) > 2 else path_points[1]
	return _normalize(sub(listener, last_bounce))


def world_dir_to_listener_angles(
	direction: Vec3,
	yaw_rad: float,
	pitch_rad: float = 0.0,
) -> tuple[float, float]:
	"""Transform a world-space unit direction into listener-local azimuth/elevation.

	Azimuth 0 = ahead (+Z in NULLSIGHT listener frame), + = right (+X).
	Matches Spherion ``compute_listener_relative_angles`` convention.
	"""
	cy = math.cos(-yaw_rad)
	sy = math.sin(-yaw_rad)
	x1 = direction[0] * cy - direction[2] * sy
	y1 = direction[1]
	z1 = direction[0] * sy + direction[2] * cy

	cp = math.cos(-pitch_rad)
	sp = math.sin(-pitch_rad)
	y2 = y1 * cp - z1 * sp
	z2 = y1 * sp + z1 * cp

	lx, ly, lz = x1, y2, z2
	horiz = math.sqrt(lx * lx + lz * lz)
	azimuth = math.atan2(lx, lz)
	elevation = math.atan2(ly, max(horiz, 1e-9))
	return azimuth, elevation


def bessel_j1(x: float) -> float:
	"""Bessel function of the first kind, order 1 (series + asymptotic)."""
	ax = abs(x)
	if ax < 1e-12:
		return 0.0
	# Power series: J1(x) = (x/2) Σ (-1)^k (x/2)^{2k} / (k! (k+1)!)
	if ax < 12.0:
		half = x * 0.5
		term = half
		total = term
		for k in range(1, _J1_SERIES_TERMS):
			term *= -(half * half) / (k * (k + 1))
			total += term
		return total
	# Asymptotic for large |x| (Abramowitz & Stegun 9.2.1).
	z = abs(x)
	mu = 4.0 * 1.0 * 1.0
	theta = z - (0.75 * math.pi)
	p = 1.0 - (mu - 1.0) / (8.0 * z * z)
	q = (mu - 1.0) / (8.0 * z)
	amp = math.sqrt(2.0 / (math.pi * z))
	val = amp * (p * math.cos(theta) - q * math.sin(theta))
	return math.copysign(val, x) if x < 0 else val


def piston_directivity_h(theta_rad: float, ka: float) -> float:
	"""Baffled circular piston directional factor H(θ) = 2 J₁(u)/u, u=ka sinθ.

	On-axis (θ→0 or ka→0): H→1. Side lobes may go negative (phase inversion);
	we return the absolute value for energy-based game audio.
	"""
	ka = max(0.0, ka)
	if ka < 1e-6:
		return 1.0
	s = abs(math.sin(theta_rad))
	u = ka * s
	if u < 1e-6:
		return 1.0
	h = 2.0 * bessel_j1(u) / u
	return abs(h)


def omni_gain() -> float:
	"""Omnidirectional pattern — unity gain."""
	return 1.0


def cardioid_gain(source_axis: Vec3, to_listener: Vec3) -> float:
	"""Cardioid pressure gain: G = 0.5 + 0.5·cos θ (Kuttruff)."""
	axis = _normalize(source_axis)
	theta = angle_between(axis, to_listener)
	return max(_MIN_DIRECTIVITY_GAIN, 0.5 + 0.5 * math.cos(theta))


def horn_gain(source_axis: Vec3, to_listener: Vec3, half_angle_rad: float = 0.55) -> float:
	"""Narrow horn / beacon pattern — cos^n falloff outside beam."""
	axis = _normalize(source_axis)
	theta = angle_between(axis, to_listener)
	if theta <= half_angle_rad:
		t = theta / max(half_angle_rad, 1e-6)
		return max(_MIN_DIRECTIVITY_GAIN, 1.0 - 0.15 * t * t)
	off = theta - half_angle_rad
	return max(_MIN_DIRECTIVITY_GAIN, math.cos(off) ** 4 * 0.35)


def pattern_gain(pattern: str, source_axis: Vec3, launch_dir: Vec3) -> float:
	"""Broadband pattern factor G_pattern(θ) for a launch direction."""
	if pattern == "cardioid":
		return cardioid_gain(source_axis, launch_dir)
	if pattern == "horn":
		return horn_gain(source_axis, launch_dir)
	return omni_gain()


def launch_band_energy_gains(
	pattern: str,
	source_axis: Vec3,
	launch_dir: Vec3,
	speaker_radius_m: float,
	*,
	speed_of_sound: float = SPEED_OF_SOUND_MPS,
) -> list[float]:
	"""Per-octave-band pressure² energy multipliers for a launch direction.

	Energy gain = [G_pattern(θ) · H(θ, ka_f)]² so intensity follows piston physics.
	Returned list length = BAND_COUNT (125…4000 Hz).
	"""
	axis = _normalize(source_axis)
	launch = _normalize(launch_dir)
	theta = angle_between(axis, launch)
	g_pat = pattern_gain(pattern, axis, launch)
	a = max(0.0, speaker_radius_m)
	gains: list[float] = []
	for f_hz in OCTAVE_HZ:
		k = 2.0 * math.pi * float(f_hz) / max(speed_of_sound, 1.0)
		ka = k * a
		h = piston_directivity_h(theta, ka)
		pressure = max(_MIN_DIRECTIVITY_GAIN, g_pat * h)
		gains.append(pressure * pressure)
	return gains


def launch_pressure_gain_broadband(
	pattern: str,
	source_axis: Vec3,
	launch_dir: Vec3,
	speaker_radius_m: float,
) -> float:
	"""Broadband pressure gain = √(mean band energy) for dry-path scaling."""
	energies = launch_band_energy_gains(pattern, source_axis, launch_dir, speaker_radius_m)
	mean_e = sum(energies) / max(1, BAND_COUNT)
	return max(_MIN_DIRECTIVITY_GAIN, math.sqrt(mean_e))


def directivity_pressure_gain(
	pattern: str,
	source_axis: Vec3,
	source_pos: Vec3,
	listener_pos: Vec3,
	speaker_radius_m: float = 0.0,
) -> float:
	"""Evaluate source directivity G(θ,a) for one listener position (dry path)."""
	to_listener = source_to_listener_dir(source_pos, listener_pos)
	return launch_pressure_gain_broadband(pattern, source_axis, to_listener, speaker_radius_m)


# Default speaker radii (m) — cone / horn mouth size for typical game assets.
DEFAULT_SPEAKER_RADIUS_M: dict[str, float] = {
	"old_radio": 0.06,  # ~12 cm cone
	"beacon": 0.12,  # horn mouth
	"hum": 0.08,
}

# Default facing axes when not authored on LevelSound.
DEFAULT_FACING: dict[str, Vec3] = {
	"old_radio": (0.0, 0.0, -1.0),  # faces into the room (−Z)
	"beacon": (0.0, -1.0, 0.0),  # ceiling horn aims down
	"hum": (0.0, 0.0, 1.0),
}
