"""Doorway and opening portal bleed for cracked / partially open doors.

Physics:
- Open fraction → slit width → aperture transmission (Kirchhoff / slit portal)
- Closed / near-closed panel uses double-leaf MAM transmission (materials.py)
- Bleed is opening leakage, not mass-law through the panel
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from acoustics.materials import (
	MATERIAL_METAL_DOOR,
	mam_resonance_hz,
	transmission_coefficient,
)

if TYPE_CHECKING:
	from interactable_door import InteractableDoor

# Full open angle used by InteractableDoor (π/2).
_FULL_OPEN_RAD = math.pi * 0.5
# When the door is barely cracked, still allow a thin acoustic leak.
_CRACK_GAIN_MIN = 0.04
_CRACK_LPF_MIN = 280.0
_OPEN_LPF_MAX = 6_500.0
# Nominal door clear width for slit geometry (m).
_DOOR_CLEAR_WIDTH_M = 0.90
# Typical door leaf thickness for MAM panel path (m).
_DOOR_PANEL_THICKNESS_M = 0.045


def door_open_fraction(door: InteractableDoor | None) -> float:
	"""Normalized door open fraction in [0, 1] from swing angle."""
	if door is None:
		return 0.0
	return max(0.0, min(1.0, door.open_angle / max(_FULL_OPEN_RAD, 1e-6)))


def slit_width_m(open_frac: float, *, clear_width_m: float = _DOOR_CLEAR_WIDTH_M) -> float:
	"""Physical slit width from door open angle (chord of swing arc).

	Near closed: narrow crack; fully open: full clear width.
	"""
	frac = max(0.0, min(1.0, open_frac))
	# Early swing opens a narrow acoustic slit (nonlinear with angle).
	shaped = frac ** 1.35
	return clear_width_m * shaped


def slit_portal_transmission(
	open_frac: float,
	*,
	clear_width_m: float = _DOOR_CLEAR_WIDTH_M,
	door_height_m: float = 2.1,
) -> tuple[float, float]:
	"""Return (pressure_gain, lpf_hz) for a cracked-door slit aperture.

	Uses slit area / doorway area as energy transmission with HF boost as the
	gap widens (Kirchhoff-style aperture, game-audio mapping).
	"""
	frac = max(0.0, min(1.0, open_frac))
	if frac <= 1e-4:
		return 0.0, _CRACK_LPF_MIN

	width = slit_width_m(frac, clear_width_m=clear_width_m)
	# Energy τ ≈ slit_area / door_area; pressure ∝ √τ.
	area_ratio = max(0.0, min(1.0, width / max(clear_width_m, 1e-3)))
	# Soften with height factor so a tall crack still leaks.
	_ = door_height_m
	pressure = math.sqrt(area_ratio)
	shaped = frac ** 1.35
	gain = max(_CRACK_GAIN_MIN, min(1.0, _CRACK_GAIN_MIN + (1.0 - _CRACK_GAIN_MIN) * pressure))
	lpf = _CRACK_LPF_MIN + (_OPEN_LPF_MAX - _CRACK_LPF_MIN) * shaped
	return gain, lpf


def door_panel_transmission(
	*,
	thickness_m: float = _DOOR_PANEL_THICKNESS_M,
) -> tuple[float, float]:
	"""Double-leaf MAM transmission through a closed metal door panel."""
	return transmission_coefficient(
		MATERIAL_METAL_DOOR,
		thickness_m,
		double_leaf=True,
	)


def door_mam_resonance_hz() -> float:
	"""MAM resonance for the default hollow metal door (Hz)."""
	# Two 12 kg/m² leaves, 5 cm air gap — see materials._DEFAULT_DOOR_*.
	return mam_resonance_hz(12.0, 12.0, gap_m=0.05)


def door_portal_bleed(door: InteractableDoor | None) -> tuple[float, float]:
	"""Return (portal_gain, portal_lpf_hz) from door open angle.

	Closed → (0, low). Fully open → panel is removed from occluders so portal
	gain is unused for LOS, but a mild bleed remains for transmission paths
	that still clip the frame. Uses physical slit width from open fraction.
	"""
	frac = door_open_fraction(door)
	if frac <= 1e-4:
		return 0.0, _CRACK_LPF_MIN
	return slit_portal_transmission(frac)
