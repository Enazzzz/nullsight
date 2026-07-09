"""Stochastic / radiosity late energy after specular early orders.

After ISM + AABB specular (orders ≤ TRANSITION_ORDER), residual diffuse energy
is folded into ``reverb_send`` so early taps + late bus conserve reflected energy
(ODEON hybrid / Savioja–Svensson pattern).
"""

from __future__ import annotations

import math

from acoustics.types import ReflectionTap

# Assumed source unit energy before geometric spreading (pressure² domain).
_SOURCE_ENERGY = 1.0
# Cap on residual → send boost so wet never dominates dry.
_MAX_RESIDUAL_SEND = 0.28


def early_tap_energy(taps: tuple[ReflectionTap, ...] | list[ReflectionTap]) -> float:
	"""Sum of squared pressure gains for early taps (energy proxy)."""
	return sum(max(0.0, t.gain) ** 2 for t in taps)


def residual_diffuse_energy(
	taps: tuple[ReflectionTap, ...] | list[ReflectionTap],
	*,
	direct_gain: float = 0.0,
	source_energy: float = _SOURCE_ENERGY,
) -> float:
	"""Estimate energy not captured by early specular/diffuse taps.

	``source_energy`` is unit at the source; direct + early taps subtract.
	Remaining (clamped) feeds the statistical late bus.
	"""
	early = early_tap_energy(taps)
	direct_e = max(0.0, direct_gain) ** 2
	# Only a fraction of source energy is room-reflected (spreading + absorption).
	room_budget = source_energy * 0.55
	residual = max(0.0, room_budget - early - 0.35 * direct_e)
	return residual


def radiosity_reverb_send_boost(
	taps: tuple[ReflectionTap, ...] | list[ReflectionTap],
	*,
	direct_gain: float = 0.0,
	base_send: float = 0.0,
) -> float:
	"""Increase reverb_send from residual diffuse energy (energy conservation).

	``late = remaining after early taps`` — returns the boosted send level.
	"""
	residual = residual_diffuse_energy(taps, direct_gain=direct_gain)
	# Pressure-ish mapping: √E → send contribution.
	boost = min(_MAX_RESIDUAL_SEND, math.sqrt(residual) * 0.45)
	return min(0.65, max(0.0, base_send) + boost)


def transition_order_fold(
	taps: tuple[ReflectionTap, ...] | list[ReflectionTap],
	*,
	transition_order: int,
	direct_gain: float,
	base_send: float,
) -> tuple[tuple[ReflectionTap, ...], float]:
	"""Keep taps with bounce_order ≤ TO; fold higher-order energy into send.

	ODEON-style: below TO use ISM+scatter taps; above TO → reverb_send.
	"""
	kept: list[ReflectionTap] = []
	folded_energy = 0.0
	for tap in taps:
		if tap.bounce_order <= transition_order:
			kept.append(tap)
		else:
			folded_energy += max(0.0, tap.gain) ** 2
	boost = min(_MAX_RESIDUAL_SEND, math.sqrt(folded_energy) * 0.5)
	# Also add residual from kept early field.
	send = radiosity_reverb_send_boost(
		tuple(kept),
		direct_gain=direct_gain,
		base_send=base_send + boost,
	)
	return tuple(kept), send
