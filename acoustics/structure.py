"""Structure-borne coupling hook for future footstep / impact SFX.

Airborne GA does not model floor/structure paths. This module exposes a stub
API so gameplay can query a structure-borne gain/LPF along a floor material
path without committing to a full SEA / FEA model yet.
"""

from __future__ import annotations

import math

from acoustics.materials import material_by_name
from math3d import Vec3, length, sub

# Structure-borne waves are slower / more damped than airborne — stub constants.
_STRUCTURE_SPEED_MPS = 1200.0
_STRUCTURE_BASE_GAIN = 0.12
_STRUCTURE_LPF_HZ = 450.0


def structure_borne_gain(
	source_pos: Vec3,
	listener_pos: Vec3,
	floor_material: str = "concrete",
) -> tuple[float, float]:
	"""Return (pressure_gain, lpf_hz) for a stub floor-borne path.

	Gain falls with horizontal floor distance and rises slightly for denser
	floors (concrete > wood > carpet). LPF stays low — structure paths are dull.
	Not a full SEA model; interface for future footstep SFX wiring.
	"""
	# Horizontal path on the floor plane (ignore height for coupling stub).
	flat_src: Vec3 = (source_pos[0], 0.0, source_pos[2])
	flat_lst: Vec3 = (listener_pos[0], 0.0, listener_pos[2])
	dist = length(sub(flat_lst, flat_src))
	mat = material_by_name(floor_material)
	# Denser floors couple better; soft finishes damp structure energy.
	density_factor = min(1.5, max(0.25, mat.density_kg_m3 / 2300.0))
	alpha_mid = 0.5 * (mat.alpha[2] + mat.alpha[3])
	absorb = 1.0 - 0.7 * alpha_mid
	# 1/r-ish with soft floor.
	spread = 1.0 / max(1.0, dist)
	gain = min(0.35, _STRUCTURE_BASE_GAIN * density_factor * absorb * spread)
	# Extra HF loss with distance (structure damping stub).
	lpf = max(180.0, _STRUCTURE_LPF_HZ * math.exp(-0.04 * dist) * (0.7 + 0.3 * density_factor))
	return gain, lpf


def structure_borne_delay_s(source_pos: Vec3, listener_pos: Vec3) -> float:
	"""Stub delay for structure path using a fast plate-wave speed."""
	flat_src: Vec3 = (source_pos[0], 0.0, source_pos[2])
	flat_lst: Vec3 = (listener_pos[0], 0.0, listener_pos[2])
	dist = length(sub(flat_lst, flat_src))
	return dist / _STRUCTURE_SPEED_MPS


def structure_borne_for_source(
	source_pos: Vec3,
	listener_pos: Vec3,
	*,
	floor_material: str = "concrete",
	source_height_m: float | None = None,
	coupled: bool = True,
) -> tuple[float, float, float]:
	"""Return (gain, lpf_hz, delay_s) for a structure-coupled source.

	``coupled`` True for table radios / impacts resting on furniture or floor.
	Sources high above the floor (ceiling beacons) get near-zero coupling.
	"""
	if not coupled:
		return 0.0, _STRUCTURE_LPF_HZ, 0.0
	height = source_pos[1] if source_height_m is None else source_height_m
	# Coupling falls off above ~1.2 m (airborne dominates for elevated sources).
	height_factor = max(0.0, 1.0 - height / 1.4)
	if height_factor < 0.05:
		return 0.0, _STRUCTURE_LPF_HZ, 0.0
	gain, lpf = structure_borne_gain(source_pos, listener_pos, floor_material)
	gain *= height_factor
	delay = structure_borne_delay_s(source_pos, listener_pos)
	return gain, lpf, delay


def airborne_vs_structure_hint(
	source_pos: Vec3,
	listener_pos: Vec3,
	*,
	airborne_gain: float,
	floor_material: str = "concrete",
) -> str:
	"""Diagnostic: which path dominates for a footstep-like event."""
	struct_g, _ = structure_borne_gain(source_pos, listener_pos, floor_material)
	if struct_g > airborne_gain * 1.2 and airborne_gain < 0.05:
		return "structure"
	if airborne_gain >= struct_g:
		return "airborne"
	return "mixed"
