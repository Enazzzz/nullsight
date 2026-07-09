"""Health-driven visual hallucination intensity — gates noise vs fake scan geometry."""

from __future__ import annotations

# Noise / glyph chaos only begins below this health ratio.
_HEALTH_NOISE_GATE = 0.50
# Slow exponential ramp for static and drifting glyphs once below 50%.
_HEALTH_NOISE_EXP = 3.8
# Fake walls/boxes ramp as health falls from full toward the noise gate.
_HEALTH_ARCH_EXP = 2.4


def health_noise_intensity(health_ratio: float) -> float:
	"""0 above 50% health; slow-then-sharp ramp below (noise, glyphs, phantoms)."""
	ratio = max(0.0, min(1.0, health_ratio))
	if ratio >= _HEALTH_NOISE_GATE:
		return 0.0
	t = (_HEALTH_NOISE_GATE - ratio) / _HEALTH_NOISE_GATE
	return t ** _HEALTH_NOISE_EXP


def health_arch_intensity(health_ratio: float) -> float:
	"""Wrong-scan geometry — strongest between ~50% and ~90% health when damaged."""
	ratio = max(0.0, min(1.0, health_ratio))
	if ratio >= 1.0:
		return 0.0
	if ratio >= _HEALTH_NOISE_GATE:
		t = (1.0 - ratio) / (1.0 - _HEALTH_NOISE_GATE)
		return min(1.0, t ** _HEALTH_ARCH_EXP * 0.92)
	# Below 50%: fade fake architecture as noise takes over.
	t = ratio / _HEALTH_NOISE_GATE
	return min(0.55, t ** 1.7 * 0.55)
