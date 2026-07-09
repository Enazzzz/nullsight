"""Resolve per-frame render and scan knobs from individual perf settings."""

from __future__ import annotations

from dataclasses import dataclass

from settings import GameSettings

# Squared world-space speed (m/s) above which motion stress kicks in.
_MOTION_STRESS_SPEED_SQ = 0.64
# Squared speed treated as "fast strafe" for occlusion stride.
_FAST_MOVE_SPEED_SQ = 1.44


@dataclass(frozen=True)
class RenderPerf:
	"""Resolved per-frame render knobs."""

	fade_buckets: int
	fade_buckets_dense: int
	dense_fade_segment_threshold: int
	motion_stress: bool


@dataclass(frozen=True)
class ScanPerf:
	"""Resolved per-frame scan/occlusion knobs."""

	live_occlusion_stride: int
	live_cache_dist_sq: float
	skip_screen_clip_while_moving: bool


def resolve_render_perf(
	settings: GameSettings,
	*,
	segment_count: int,
	camera_speed_sq: float,
) -> RenderPerf:
	"""Pick fade buckets from graphics perf toggles."""
	perf = settings.perf
	moving = camera_speed_sq >= _MOTION_STRESS_SPEED_SQ

	fade_buckets = 4 if perf.coarse_fade else 16
	fade_dense = 4 if (perf.coarse_fade or perf.dense_coarse_fade) else 8
	dense_threshold = 48 if perf.coarse_fade else 80
	motion_stress = moving and perf.simple_fade_moving

	return RenderPerf(
		fade_buckets=fade_buckets,
		fade_buckets_dense=fade_dense,
		dense_fade_segment_threshold=dense_threshold,
		motion_stress=motion_stress,
	)


def resolve_scan_perf(settings: GameSettings, *, camera_speed_sq: float) -> ScanPerf:
	"""Pick live-occlusion stride and cache reuse from graphics perf toggles."""
	perf = settings.perf
	fast = camera_speed_sq >= _FAST_MOVE_SPEED_SQ
	moving = camera_speed_sq >= _MOTION_STRESS_SPEED_SQ

	if perf.throttle_live_occlusion:
		stride = 3 if fast else (2 if moving else 1)
	else:
		stride = 1

	if perf.wide_occlusion_cache and moving:
		cache_dist_sq = 0.0144
	elif perf.wide_occlusion_cache:
		cache_dist_sq = 0.01
	else:
		cache_dist_sq = 0.0036

	skip_screen = perf.skip_screen_clip_moving and moving

	return ScanPerf(
		live_occlusion_stride=stride,
		live_cache_dist_sq=cache_dist_sq,
		skip_screen_clip_while_moving=skip_screen,
	)
