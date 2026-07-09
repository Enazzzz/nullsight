"""Rectangular room eigenmodes below the Schroeder frequency.

Physics:
- Mode frequency: f_{m,n,l} = (c/2)·√((m/Lx)²+(n/Ly)²+(l/Lz)²)
- Schroeder frequency: f_s ≈ 2000·√(T60/V) — modal overlap above this
- Below f_s: sparse modes; mild LF boost when modes cluster near a band

Wiring (documented): when several modes fall in the same low band, raise
``reverb_send`` slightly and/or lower dry LPF so the late bus carries more
modal energy (statistical Schroeder already covers the diffuse field above f_s).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from acoustics.materials import SPEED_OF_SOUND_MPS
from acoustics.reverb_zones import room_extents_from_boundaries, room_volume_and_area
from occlusion import BoundaryPlane

# Max mode indices searched per axis.
_MAX_INDEX = 4
# Relative amplitude floor for returned modes.
_MIN_REL_AMP = 0.05


@dataclass(frozen=True)
class RoomMode:
	"""One rectangular-room eigenmode."""

	m: int
	n: int
	l: int
	freq_hz: float
	# Relative pressure amplitude (axial > tangential > oblique heuristic).
	relative_amplitude: float


def schroeder_frequency_hz(rt60_s: float, volume_m3: float) -> float:
	"""Schroeder frequency f_s ≈ 2000·√(T60/V) Hz (modal overlap threshold)."""
	if volume_m3 <= 1e-6 or rt60_s <= 0.0:
		return 0.0
	return 2000.0 * math.sqrt(rt60_s / volume_m3)


def mode_frequency_hz(
	m: int,
	n: int,
	l: int,
	extents: tuple[float, float, float],
	*,
	speed_mps: float = SPEED_OF_SOUND_MPS,
) -> float:
	"""Rectangular room mode f_{m,n,l} = (c/2)·√((m/Lx)²+(n/Ly)²+(l/Lz)²)."""
	lx, ly, lz = extents
	if lx <= 0.0 or ly <= 0.0 or lz <= 0.0:
		return 0.0
	inside = (m / lx) ** 2 + (n / ly) ** 2 + (l / lz) ** 2
	return 0.5 * speed_mps * math.sqrt(inside)


def _mode_amplitude(m: int, n: int, l: int) -> float:
	"""Heuristic relative amplitude: axial > tangential > oblique."""
	nonzero = sum(1 for k in (m, n, l) if k > 0)
	if nonzero == 1:
		return 1.0
	if nonzero == 2:
		return 0.65
	return 0.40


def rectangular_room_modes(
	extents: tuple[float, float, float],
	*,
	rt60_s: float,
	max_index: int = _MAX_INDEX,
	speed_mps: float = SPEED_OF_SOUND_MPS,
	max_freq_hz: float | None = None,
) -> tuple[list[RoomMode], float]:
	"""Return (modes below cutoff, f_schroeder_hz).

	Default cutoff is Schroeder frequency. Pass ``max_freq_hz`` to include
	modes above f_s (needed for audible LF resonators in large rooms).
	Skips the (0,0,0) DC mode. Amplitudes are relative heuristics for LF boost.
	"""
	volume, _ = room_volume_and_area(extents)
	f_s = schroeder_frequency_hz(rt60_s, volume)
	cutoff = f_s if max_freq_hz is None else max(f_s, max_freq_hz)
	modes: list[RoomMode] = []
	for m in range(0, max_index + 1):
		for n in range(0, max_index + 1):
			for l in range(0, max_index + 1):
				if m == 0 and n == 0 and l == 0:
					continue
				f = mode_frequency_hz(m, n, l, extents, speed_mps=speed_mps)
				if f <= 0.0 or f > cutoff:
					continue
				amp = _mode_amplitude(m, n, l)
				if amp < _MIN_REL_AMP:
					continue
				modes.append(RoomMode(m=m, n=n, l=l, freq_hz=f, relative_amplitude=amp))
	modes.sort(key=lambda mode: mode.freq_hz)
	return modes, f_s


def modes_from_boundaries(
	boundaries: list[BoundaryPlane],
	*,
	rt60_s: float,
	max_freq_hz: float | None = None,
) -> tuple[list[RoomMode], float]:
	"""Compute modes from room BoundaryPlane shell + RT60."""
	extents = room_extents_from_boundaries(boundaries)
	if extents is None:
		return [], 0.0
	return rectangular_room_modes(extents, rt60_s=rt60_s, max_freq_hz=max_freq_hz)


def modal_lf_boost(
	modes: list[RoomMode],
	*,
	band_low_hz: float = 80.0,
	band_high_hz: float = 250.0,
) -> float:
	"""Return a mild LF boost factor (1.0 = none) when modes cluster in-band.

	Wiring: multiply into ``reverb_send`` (cap +0.12) or lower dry LPF toward
	``band_high_hz`` when boost > 1.05 — documents modal energy below f_s.
	"""
	if not modes:
		return 1.0
	cluster = [m for m in modes if band_low_hz <= m.freq_hz <= band_high_hz]
	if len(cluster) < 2:
		return 1.0
	energy = sum(m.relative_amplitude for m in cluster)
	# Mild: 2–4 clustered modes → ~1.05–1.15.
	return min(1.18, 1.0 + 0.04 * energy)


def modal_resonator_params(
	modes: list[RoomMode],
	*,
	max_count: int = 8,
	band_low_hz: float = 40.0,
	band_high_hz: float = 280.0,
	schroeder_hz: float = 0.0,
) -> tuple[list[float], list[float], list[float]]:
	"""Pick loudest in-band modes for the late-bus biquad bank.

	Returns (freqs_hz, gains, qs) ready for ``set_late_reverb_modal_resonators``.
	When ``schroeder_hz`` is set, the upper bound is at least ``1.2·f_s`` so large
	rooms (low f_s) still get audible LF resonators from sparse modes.
	"""
	if not modes:
		return [], [], []
	# Large rooms: f_s can be < 60 Hz — still ring the strongest modes below ~200 Hz.
	hi = max(band_high_hz, schroeder_hz * 1.2 if schroeder_hz > 0.0 else 0.0, 120.0)
	lo = band_low_hz
	candidates = [m for m in modes if lo <= m.freq_hz <= hi]
	if not candidates:
		# Fallback: loudest modes overall under 250 Hz.
		candidates = [m for m in modes if m.freq_hz <= 250.0]
	candidates.sort(key=lambda m: (-m.relative_amplitude, m.freq_hz))
	freqs: list[float] = []
	gains: list[float] = []
	qs: list[float] = []
	for mode in candidates:
		if len(freqs) >= max_count:
			break
		# Skip modes within 8 Hz of an already-selected peak.
		if any(abs(mode.freq_hz - f) < 8.0 for f in freqs):
			continue
		freqs.append(mode.freq_hz)
		# Axial modes get higher Q (sharper boom); oblique softer.
		q = 18.0 if mode.relative_amplitude >= 0.95 else (12.0 if mode.relative_amplitude >= 0.6 else 8.0)
		gains.append(min(0.28, 0.08 + 0.18 * mode.relative_amplitude))
		qs.append(q)
	return freqs, gains, qs
