"""Late-reverb zones driven by Sabine / Eyring RT60 from room geometry.

Physics (Kuttruff / ISO 3382):
- Sabine (low ᾱ):  T60 = 0.161 V / (A + 4mV),  A = Σ α_i S_i
- Eyring (ᾱ ≳ 0.2): T60 = 0.161 V / (−S ln(1−ᾱ) + 4mV)
- V, S from room BoundaryPlane extents (AABB shell)
- Door open fraction couples rooms → longer effective RT60 / more wet
- Occluder furniture adds local absorption A = α · S_AABB
- Indoor wet bus uses geometry RT60; outdoor / huge volumes disable wet
- Procedural zone IR: exponential noise decay from RT60 (Phase 4)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from acoustics.materials import MATERIAL_CONCRETE, AcousticMaterial, material_by_name
from occlusion import Aabb, BoundaryPlane

# Sabine constant 24·ln(10)/c ≈ 0.161 s/m at 20 °C.
_SABINE_K = 0.161
# Switch Sabine → Eyring when mean absorption is no longer tiny.
_EYRING_ALPHA_THRESHOLD = 0.20
# Midband air absorption m (Np/m) ≈ 0.0015 at 1 kHz, 20 °C / 50 %RH.
_AIR_M_NP_PER_M = 0.0015
# Gameplay clamp — bare concrete Sabine can be 10–20 s; keep playable.
_RT60_MIN_S = 0.25
_RT60_MAX_S = 2.80
# Volumes above this are treated as outdoor / open (disable wet).
_OUTDOOR_VOLUME_M3 = 2_500.0
# Minimum extent per axis when inferring from planes (m).
_MIN_EXTENT_M = 0.5
# Open door couples adjacent volume — fraction of shell volume added at full open.
_DOOR_COUPLED_VOLUME_FRAC = 0.55
# Open door reduces effective absorption (less sealed room) — scale on A.
_DOOR_ABSORPTION_REDUCTION = 0.35


@dataclass(frozen=True)
class ReverbZone:
	"""Statistical late-reverb parameters for one room / biome."""

	name: str
	# Approximate RT60 in seconds (energy decay).
	rt60_s: float
	# Wet send level into the reverb bus (0–1).
	send_level: float
	# Early reflection density hint (unused by Spherion; for future tuning).
	density: float
	# Disable wet bus outdoors / large open spaces.
	enabled: bool = True
	# First-reflection → late bus predelay (seconds).
	predelay_s: float = 0.0


# Fallback presets when geometry is unavailable (outdoor / unknown).
_ZONE_SMALL = ReverbZone("small", rt60_s=1.20, send_level=0.48, density=0.85, enabled=True)
_ZONE_LARGE = ReverbZone("large", rt60_s=0.40, send_level=0.08, density=0.35, enabled=False)
_ZONE_DEFAULT = ReverbZone("default", rt60_s=0.90, send_level=0.38, density=0.7, enabled=True)

_ZONES: dict[str, ReverbZone] = {
	"small": _ZONE_SMALL,
	"large": _ZONE_LARGE,
	"test": _ZONE_SMALL,
	"default": _ZONE_DEFAULT,
}


def reverb_zone_for_room(room_name: str) -> ReverbZone:
	"""Look up a reverb zone by ``RoomData.name`` (fallback when no geometry)."""
	return _ZONES.get(room_name, _ZONE_DEFAULT)


def room_extents_from_boundaries(
	boundaries: list[BoundaryPlane],
) -> tuple[float, float, float] | None:
	"""Infer (dx, dy, dz) from opposing axis-aligned boundary planes."""
	mins = [math.inf, math.inf, math.inf]
	maxs = [-math.inf, -math.inf, -math.inf]
	for plane in boundaries:
		axis = plane.axis
		if axis < 0 or axis > 2:
			continue
		mins[axis] = min(mins[axis], plane.coord)
		maxs[axis] = max(maxs[axis], plane.coord)
	extents: list[float] = []
	for axis in range(3):
		if not math.isfinite(mins[axis]) or not math.isfinite(maxs[axis]):
			return None
		extent = maxs[axis] - mins[axis]
		if extent < _MIN_EXTENT_M:
			return None
		extents.append(extent)
	return extents[0], extents[1], extents[2]


def room_volume_and_area(extents: tuple[float, float, float]) -> tuple[float, float]:
	"""Return (V, S) for a rectangular room shell."""
	dx, dy, dz = extents
	volume = dx * dy * dz
	area = 2.0 * (dx * dy + dy * dz + dz * dx)
	return volume, area


def midband_alpha(material: AcousticMaterial) -> float:
	"""Absorption used for single-number RT60: mean of 500 Hz + 1 kHz bands."""
	# OCTAVE_HZ = (125, 250, 500, 1000, 2000, 4000) → indices 2, 3.
	return 0.5 * (material.alpha[2] + material.alpha[3])


def aabb_surface_area_m2(bounds: Aabb) -> float:
	"""Surface area of an AABB shell (furniture absorption proxy)."""
	box_min, box_max = bounds
	dx = max(0.0, box_max[0] - box_min[0])
	dy = max(0.0, box_max[1] - box_min[1])
	dz = max(0.0, box_max[2] - box_min[2])
	return 2.0 * (dx * dy + dy * dz + dz * dx)


def occluder_absorption_area(
	occluders: list[Aabb] | None,
	occluder_materials: list[str] | None = None,
) -> float:
	"""Sum A = α_mid · S over furniture / interior occluder AABBs."""
	if not occluders:
		return 0.0
	total = 0.0
	for index, bounds in enumerate(occluders):
		name = None
		if occluder_materials and index < len(occluder_materials):
			name = occluder_materials[index]
		mat = material_by_name(name) if name else MATERIAL_CONCRETE
		# Skip door-sized thin panels from furniture A (handled via door coupling).
		box_min, box_max = bounds
		thickness = min(
			box_max[0] - box_min[0],
			box_max[1] - box_min[1],
			box_max[2] - box_min[2],
		)
		if name == "metal_door" or thickness < 0.08:
			continue
		alpha = midband_alpha(mat)
		total += alpha * aabb_surface_area_m2(bounds)
	return total


def sabine_rt60_s(volume_m3: float, absorption_area_m2: float, *, air_m: float = _AIR_M_NP_PER_M) -> float:
	"""Sabine T60 = 0.161 V / (A + 4mV)."""
	denom = absorption_area_m2 + 4.0 * air_m * volume_m3
	if denom <= 1e-9 or volume_m3 <= 0.0:
		return _RT60_MAX_S
	return _SABINE_K * volume_m3 / denom


def eyring_rt60_s(
	volume_m3: float,
	surface_area_m2: float,
	alpha_bar: float,
	*,
	air_m: float = _AIR_M_NP_PER_M,
) -> float:
	"""Eyring T60 = 0.161 V / (−S ln(1−ᾱ) + 4mV)."""
	a = min(0.99, max(0.0, alpha_bar))
	if a >= 0.999 or surface_area_m2 <= 0.0 or volume_m3 <= 0.0:
		return _RT60_MIN_S
	denom = -surface_area_m2 * math.log(1.0 - a) + 4.0 * air_m * volume_m3
	if denom <= 1e-9:
		return _RT60_MAX_S
	return _SABINE_K * volume_m3 / denom


def compute_rt60_from_geometry(
	boundaries: list[BoundaryPlane],
	*,
	material: AcousticMaterial | None = None,
	material_name: str | None = None,
	include_air: bool = True,
	door_open_frac: float = 0.0,
	occluders: list[Aabb] | None = None,
	occluder_materials: list[str] | None = None,
) -> tuple[float, float, float, float]:
	"""Compute (rt60_s, volume, surface_area, alpha_bar) from room planes + material.

	Uses Sabine when ᾱ < 0.2, else Eyring. Door open fraction couples rooms
	(larger V, less sealed A → longer RT60). Occluder AABBs add furniture A.
	Returns clamped gameplay RT60.
	"""
	mat = material or material_by_name(material_name) or MATERIAL_CONCRETE
	extents = room_extents_from_boundaries(boundaries)
	if extents is None:
		return 0.0, 0.0, 0.0, 0.0

	volume, surface = room_volume_and_area(extents)
	alpha = midband_alpha(mat)
	# Uniform shell: A = ᾱ · S.
	a_sabine = alpha * surface
	# Furniture / pillars: local absorption reduces RT60.
	a_furniture = occluder_absorption_area(occluders, occluder_materials)
	a_sabine += a_furniture

	# Bare concrete → very long Sabine; add equivalent absorption so bunker
	# rooms land in the playable range. Furniture A stacks on top so tables /
	# pillars always shorten RT60 relative to the empty shell.
	if alpha < 0.08:
		a_sabine = a_sabine + 0.12 * surface

	# Open door couples adjacent cell — increase effective volume, reduce sealed A.
	frac = max(0.0, min(1.0, door_open_frac))
	if frac > 1e-4:
		volume = volume * (1.0 + _DOOR_COUPLED_VOLUME_FRAC * frac)
		a_sabine = a_sabine * (1.0 - _DOOR_ABSORPTION_REDUCTION * frac)

	air = _AIR_M_NP_PER_M if include_air else 0.0
	# Effective ᾱ for Eyring includes furniture.
	alpha_eff = min(0.99, a_sabine / max(surface, 1e-6))
	if alpha_eff >= _EYRING_ALPHA_THRESHOLD:
		raw = eyring_rt60_s(volume, surface, alpha_eff, air_m=air)
	else:
		raw = sabine_rt60_s(volume, a_sabine, air_m=air)

	rt60 = max(_RT60_MIN_S, min(_RT60_MAX_S, raw))
	return rt60, volume, surface, alpha


def wet_level_from_rt60(rt60_s: float) -> float:
	"""Map RT60 to Schroeder bus wet level (keep dry near 1)."""
	if rt60_s <= 0.0:
		return 0.0
	# Mild log curve: short rooms quieter wet, long rooms up to ~0.65.
	return max(0.0, min(0.65, 0.22 * math.log1p(rt60_s * 3.5)))


def reverb_zone_from_geometry(
	room_name: str,
	boundaries: list[BoundaryPlane],
	*,
	material_name: str | None = "concrete",
	door_open_frac: float = 0.0,
	occluders: list[Aabb] | None = None,
	occluder_materials: list[str] | None = None,
	predelay_s: float = 0.0,
) -> ReverbZone:
	"""Build a ReverbZone from room shell geometry + materials + door coupling.

	Outdoor / huge volumes stay disabled. Named ``large`` rooms force outdoor.
	"""
	fallback = reverb_zone_for_room(room_name)
	if room_name == "large" or not fallback.enabled:
		return fallback

	rt60, volume, _surface, _alpha = compute_rt60_from_geometry(
		boundaries,
		material_name=material_name,
		door_open_frac=door_open_frac,
		occluders=occluders,
		occluder_materials=occluder_materials,
	)
	if volume <= 0.0:
		return fallback
	if volume >= _OUTDOOR_VOLUME_M3:
		return ReverbZone(
			name=room_name or "outdoor",
			rt60_s=0.35,
			send_level=0.05,
			density=0.2,
			enabled=False,
			predelay_s=0.0,
		)

	# Open door → wetter send (coupled rooms).
	frac = max(0.0, min(1.0, door_open_frac))
	send = min(0.65, fallback.send_level * (0.75 + 0.35 * (rt60 / 1.2)) * (1.0 + 0.25 * frac))
	return ReverbZone(
		name=room_name or "geo",
		rt60_s=rt60,
		send_level=send,
		density=fallback.density,
		enabled=True,
		predelay_s=max(0.0, min(0.25, predelay_s)),
	)


def reverb_send_for_path(
	room_name: str,
	*,
	direct_gain: float,
	reflection_gain: float,
	path_mode: str,
	zone: ReverbZone | None = None,
) -> float:
	"""Compute a wet-send level from zone preset and path energy."""
	active = zone if zone is not None else reverb_zone_for_room(room_name)
	if not active.enabled:
		return 0.0
	# Stronger early energy → more late send; fully blocked paths stay mostly dry.
	path_energy = max(direct_gain, reflection_gain * 0.65, 0.15 if path_mode != "blocked" else 0.0)
	if path_mode == "blocked" and path_energy < 0.05:
		return 0.0
	return min(0.65, active.send_level * (0.65 + 0.45 * path_energy))


def generate_zone_ir(
	rt60_s: float,
	*,
	sample_rate_hz: int = 48_000,
	duration_s: float | None = None,
	seed: int = 42,
) -> tuple[list[float], list[float]]:
	"""Procedural short stereo IR: exponential noise decay from RT60.

	Energy envelope e^(−t·ln(1000)/T60) so amplitude is −60 dB at T60.
	Use with ``StereoObject.set_ir_arrays`` if a zone bus needs convolution;
	otherwise LateReverb RT60 + predelay already covers the statistical late field.
	"""
	t60 = max(0.05, min(4.0, rt60_s))
	dur = duration_s if duration_s is not None else min(2.5, t60 * 1.15)
	n = max(64, int(sample_rate_hz * dur))
	rng = random.Random(seed)
	decay = math.log(1000.0) / t60  # amplitude to 0.001
	left: list[float] = []
	right: list[float] = []
	for i in range(n):
		t = i / float(sample_rate_hz)
		env = math.exp(-decay * t)
		left.append((rng.random() * 2.0 - 1.0) * env)
		right.append((rng.random() * 2.0 - 1.0) * env)
	# Normalize peak.
	peak = max(1e-6, max(abs(x) for x in left + right))
	left = [x / peak for x in left]
	right = [x / peak for x in right]
	return left, right
