"""Handheld scan device — frozen snapshot with per-segment fade."""

from dataclasses import dataclass

from camera import DEFAULT_FOV_Y
from math3d import Vec3, clip_segment_to_sphere_interval, lerp, length, merge_intervals, sub
from occlusion import BoundaryPlane
from performance_config import ScanPerf, resolve_scan_perf
from render_cull import ViewCullSource, segment_behind_near_plane, segment_fully_off_screen, segment_wholly_in_view_frustum
from settings import GameSettings
from scan_memory import ScanMemory, ScannedSegment, segment_key
from scan_occluders import ScannedFaceIndex, build_scanned_occluders
from scan_snapshot import (
	ScanSnapshot,
	SnapshotSegment,
	capture_scan_snapshot,
	forward_view_intervals,
	intersect_interval_lists,
	live_occlusion_intervals,
	screen_view_intervals,
)

MIN_FORWARD_Z = 0.05
_INTERVAL_UNIT = 1.0 - 1e-6
# Seconds for the whole scan to dim after reveal + hold (see ``ScanDevice`` timeline).
DEFAULT_FADE_OUT_SECONDS = 3


def _interval_covers_segment(interval: tuple[float, float]) -> bool:
	"""Return True when a parametric interval spans the full edge."""
	return interval[0] <= 1e-6 and interval[1] >= _INTERVAL_UNIT


@dataclass(frozen=True)
class SegmentRenderState:
	"""Per-frame draw timing cached for one scanned segment."""

	uniform_bright: bool
	wave_radius: float
	wave_complete: bool
	reveal_origin: Vec3
	wave_interval: tuple[float, float] | None
	outside_wave: bool
	wave_covers_full: bool


@dataclass(frozen=True)
class ScanPulse:
	"""One scan snapshot anchored at the moment the player triggered it."""

	origin: Vec3
	yaw: float
	pitch: float
	start_time: float
	wave_speed: float
	max_radius: float
	fade_duration: float
	view_width: int
	view_height: int
	fov_y: float


class ScanDevice:
	"""Expanding scan pulses with deduplicated persistent scanned geometry."""

	def __init__(
		self,
		wave_speed: float = 10.0,
		max_radius: float = 14.0,
		fade_out_duration: float = DEFAULT_FADE_OUT_SECONDS,
		cooldown: float = 5.6,
		hold_duration: float = 0.7,
		cascade_exponent: float = 2.8,
		cascade_span_scale: float = 0.0,
	) -> None:
		self.wave_speed = wave_speed
		self.max_radius = max_radius
		# Unified fade length — entire scan dims together when ``cascade_span_scale`` is 0.
		self.fade_duration = fade_out_duration
		self.fade_out_duration = fade_out_duration
		self.cooldown = cooldown
		self.hold_duration = hold_duration
		self.cascade_exponent = cascade_exponent
		self.cascade_span_scale = cascade_span_scale
		self._unified_fade = cascade_span_scale <= 0.0

		self._memory = ScanMemory()
		self._time = 0.0
		self._cooldown_left = 0.0
		self._boundaries: list[BoundaryPlane] | None = None
		self._occluders: list | None = None
		self._live_draw_cache: dict[int, tuple[list, Vec3]] = {}
		self._prune_counter = 0
		self._frame_wave_complete: float | None = None
		self._render_states: dict[int, SegmentRenderState] = {}
		self._fade_cache: dict[tuple[int, int], float | None] = {}
		self._camera_speed_sq = 0.0
		self._scan_perf = resolve_scan_perf(GameSettings(), camera_speed_sq=0.0)
		self._live_occlusion_frame = 0

	@property
	def memory(self) -> ScanMemory:
		"""Active scanned geometry and merged occlusion faces."""
		return self._memory

	@property
	def active_segments(self) -> list[ScannedSegment]:
		"""All scanned segments currently visible or fading."""
		return self._memory.segments

	@property
	def scanned_faces(self) -> list:
		"""Merged scan-discovered occluder faces for live occlusion."""
		return self._memory.scanned_faces

	@property
	def is_active(self) -> bool:
		"""True while any scanned geometry may still be drawing."""
		return bool(self._memory.segments)

	@property
	def scan_ready(self) -> bool:
		"""True when a new pulse may be triggered (cooldown elapsed)."""
		return self._cooldown_left <= 0.0

	@property
	def sim_time(self) -> float:
		"""Simulation clock used for scan fade and distortion animation."""
		return self._time

	def elapsed(self, start_time: float) -> float:
		"""Seconds since a segment or pulse began."""
		return self._time - start_time

	def wave_complete_elapsed(self) -> float:
		"""Seconds after trigger when the wave reaches max radius."""
		cached = self._frame_wave_complete
		if cached is not None:
			return cached
		return self.max_radius / self.wave_speed

	def fade_start_elapsed(self) -> float:
		"""Seconds after trigger when the unified fade-out begins."""
		return self.wave_complete_elapsed() + self.hold_duration

	def total_visible_elapsed(self) -> float:
		"""Seconds after trigger when the last segment is fully pruned."""
		return self.fade_start_elapsed() + self.fade_out_duration

	def set_frame_hints(self, settings: GameSettings, camera_speed_sq: float) -> None:
		"""Refresh per-frame occlusion tuning from graphics perf settings and motion."""
		self._camera_speed_sq = max(0.0, camera_speed_sq)
		self._scan_perf = resolve_scan_perf(settings, camera_speed_sq=self._camera_speed_sq)

	def begin_render_frame(self) -> None:
		"""Cache per-frame timing values used heavily during draw."""
		self._live_occlusion_frame += 1
		self._frame_wave_complete = self.max_radius / self.wave_speed
		self._fade_cache.clear()
		wave_done = self._frame_wave_complete
		states: dict[int, SegmentRenderState] = {}
		for segment in self._memory.segments:
			elapsed = self.elapsed(segment.start_time)
			reveal_origin = segment.reveal_origin
			if not segment.wave_complete:
				uniform = elapsed < wave_done + self.hold_duration
				wave_radius = elapsed * self.wave_speed
				wave_interval = clip_segment_to_sphere_interval(
					segment.start,
					segment.end,
					reveal_origin,
					wave_radius,
				)
				outside_wave = wave_interval is None
				wave_covers_full = wave_interval is not None and _interval_covers_segment(wave_interval)
			else:
				uniform = elapsed < self.hold_duration
				wave_radius = 0.0
				wave_interval = (0.0, 1.0)
				outside_wave = False
				wave_covers_full = True
			states[id(segment)] = SegmentRenderState(
				uniform_bright=uniform,
				wave_radius=wave_radius,
				wave_complete=segment.wave_complete,
				reveal_origin=reveal_origin,
				wave_interval=wave_interval,
				outside_wave=outside_wave,
				wave_covers_full=wave_covers_full,
			)
			if not segment.wave_complete and wave_covers_full:
				segment.wave_complete = True
				self._refresh_segment_expire_time(segment)
		self._render_states = states

	def segment_render_state(self, segment: ScannedSegment) -> SegmentRenderState:
		"""Return cached draw timing for a segment during the current render frame."""
		return self._render_states[id(segment)]

	def _live_segment_cache_valid(self, eye: Vec3, cached_eye: Vec3) -> bool:
		"""Return True when a per-segment occlusion result is still fresh enough."""
		dx = eye[0] - cached_eye[0]
		dy = eye[1] - cached_eye[1]
		dz = eye[2] - cached_eye[2]
		return dx * dx + dy * dy + dz * dz <= self._scan_perf.live_cache_dist_sq

	def prepare_segment_draws(
		self,
		eye: Vec3,
		scanned_faces: list,
		visibility_cache,
		view_cache: ViewCullSource | None = None,
		fov_y: float = DEFAULT_FOV_Y,
	):
		"""Yield per-segment draw intervals; occlusion is cached per segment and eye step."""
		visibility_gate = getattr(view_cache, "segment_visibility", None)
		scan_perf = self._scan_perf

		for segment in self._memory.segments:
			vis = "inside"
			if view_cache is not None:
				if visibility_gate is not None:
					vis = visibility_gate(segment.start, segment.end)
					if vis == "culled":
						continue
				elif self._culled_segment(view_cache, segment):
					continue

			intervals = segment.intervals
			if not intervals:
				continue

			if scanned_faces:
				cache_key = id(segment)
				cached_entry = self._live_draw_cache.get(cache_key)
				reuse_live = False
				if cached_entry is not None:
					cached_intervals, cached_eye = cached_entry
					if self._live_segment_cache_valid(eye, cached_eye):
						intervals = cached_intervals
						reuse_live = True
				if not reuse_live:
					occluded = live_occlusion_intervals(
						segment.start,
						segment.end,
						intervals,
						segment.hull_bounds,
						eye,
						scanned_faces,
						visibility_cache,
					)
					self._live_draw_cache[cache_key] = (occluded, eye)
					intervals = occluded

			if view_cache is not None and view_cache.width > 0 and view_cache.height > 0:
				if scan_perf.skip_screen_clip_while_moving:
					pass
				elif visibility_gate is not None:
					in_view = vis == "inside"
					if not in_view:
						on_screen = screen_view_intervals(
							segment.start,
							segment.end,
							view_cache,
							view_cache.width,
							view_cache.height,
							fov_y,
						)
						intervals = intersect_interval_lists(intervals, on_screen)
				else:
					in_view = segment_wholly_in_view_frustum(
						view_cache,
						segment.start,
						segment.end,
						fov_y,
					)
					if not in_view:
						on_screen = screen_view_intervals(
							segment.start,
							segment.end,
							view_cache,
							view_cache.width,
							view_cache.height,
							fov_y,
						)
						intervals = intersect_interval_lists(intervals, on_screen)

			if intervals:
				yield segment, intervals

	def _culled_segment(self, view_cache: ViewCullSource, segment: ScannedSegment) -> bool:
		"""Return True when a segment cannot contribute pixels this frame."""
		if segment_behind_near_plane(view_cache, segment.start, segment.end):
			return True
		return segment_fully_off_screen(view_cache, segment.start, segment.end)

	def _fade_cascade_start(self, start_time: float, distance: float, *, skip_wave: bool = False) -> float:
		"""Elapsed time when fade begins for geometry at ``distance``."""
		if self._unified_fade and not skip_wave:
			return start_time + self.fade_start_elapsed()
		wave_complete = 0.0 if skip_wave else self.wave_complete_elapsed()
		normalized = distance / self.max_radius
		cascade_offset = (normalized ** self.cascade_exponent) * wave_complete * self.cascade_span_scale
		if skip_wave:
			cascade_offset = (normalized ** self.cascade_exponent) * self.hold_duration * self.cascade_span_scale
			return start_time + self.hold_duration + cascade_offset
		return start_time + wave_complete + self.hold_duration + cascade_offset

	def _fade_curve(self, fade_t: float, normalized_distance: float) -> float:
		"""Map fade progress to brightness; closer geometry dims faster early on."""
		fade_t = max(0.0, min(1.0, fade_t))
		if self._unified_fade:
			return 1.0 - fade_t
		near_weight = 1.0 - normalized_distance
		fade_power = 0.4 + 0.6 * normalized_distance - near_weight * 0.25
		fade_power = max(0.25, min(1.0, fade_power))
		return 1.0 - fade_t ** fade_power

	def _max_reveal_distance(self, segment: ScannedSegment) -> float:
		"""Farthest scan-origin distance along a segment's visible intervals."""
		origin = segment.reveal_origin
		farthest = 0.0
		for t0, t1 in segment.intervals:
			for t in (t0, t1):
				point = lerp(segment.start, segment.end, t)
				farthest = max(farthest, length(sub(point, origin)))
		return farthest

	def _segment_expire_time(self, segment: ScannedSegment) -> float:
		"""World time when a segment's fade has fully finished."""
		max_dist = min(self._max_reveal_distance(segment), self.max_radius)
		return (
			self._fade_cascade_start(segment.start_time, max_dist, skip_wave=segment.wave_complete)
			+ self.fade_duration
		)

	def _segment_expired(self, segment: ScannedSegment) -> bool:
		"""Return True only after a segment's full reveal, hold, and fade cycle."""
		expire = segment.expire_time
		if expire > 0.0:
			return self._time >= expire
		return self._time >= self._segment_expire_time(segment)

	def _refresh_segment_expire_time(self, segment: ScannedSegment) -> None:
		"""Store absolute fade-end time so pruning avoids recomputing distance each frame."""
		segment.expire_time = self._segment_expire_time(segment)

	def uses_uniform_brightness(self, segment: ScannedSegment) -> bool:
		"""Return True when an entire segment draws at full brightness without cascade fade."""
		elapsed = self.elapsed(segment.start_time)
		wave_done = self.wave_complete_elapsed()
		if not segment.wave_complete:
			return elapsed < wave_done + self.hold_duration
		return elapsed < self.hold_duration

	def live_draw_intervals(
		self,
		segment: ScannedSegment,
		eye: Vec3,
		scanned_faces: list,
		visibility_cache,
	) -> list:
		"""Return draw intervals with per-segment caching for live occlusion."""
		cache_key = id(segment)
		cached_entry = self._live_draw_cache.get(cache_key)
		if cached_entry is not None:
			cached_intervals, cached_eye = cached_entry
			if self._live_segment_cache_valid(eye, cached_eye):
				return cached_intervals

		draw_intervals = live_occlusion_intervals(
			segment.start,
			segment.end,
			segment.intervals,
			segment.hull_bounds,
			eye,
			scanned_faces,
			visibility_cache,
		)
		self._live_draw_cache[cache_key] = (draw_intervals, eye)
		return draw_intervals

	def _clear_live_draw_cache(self) -> None:
		"""Drop cached live-occlusion intervals after geometry changes."""
		self._live_draw_cache.clear()

	def invalidate_live_occlusion_cache(self) -> None:
		"""Public hook for dynamic geometry that moves scanned segments."""
		self._clear_live_draw_cache()

	def rebuild_scanned_faces(self) -> None:
		"""Refresh scan-discovered occluder faces after dynamic geometry moves."""
		if self._boundaries is not None:
			self._rebuild_scanned_faces(self._boundaries)
			self._clear_live_draw_cache()

	def fade_factor(self, segment: ScannedSegment, distance: float) -> float | None:
		"""Return 1→0 fade strength for a point, or None when fully gone or unrevealed."""
		if distance > self.max_radius:
			return None

		elapsed = self.elapsed(segment.start_time)

		if not segment.wave_complete:
			reveal_elapsed = distance / self.wave_speed
			if elapsed < reveal_elapsed:
				return None

		wave_complete = self.wave_complete_elapsed()
		if not segment.wave_complete and elapsed < wave_complete + self.hold_duration:
			return 1.0
		if segment.wave_complete and elapsed < self.hold_duration:
			return 1.0

		fade_start = self._fade_cascade_start(
			segment.start_time,
			distance,
			skip_wave=segment.wave_complete,
		)
		fade_age = self._time - fade_start
		if fade_age < 0.0:
			return 1.0
		if fade_age >= self.fade_duration:
			return None

		normalized = distance / self.max_radius
		fade_t = fade_age / self.fade_duration
		return self._fade_curve(fade_t, normalized)

	def fade_factor_cached(self, segment: ScannedSegment, distance: float) -> float | None:
		"""Per-frame memoized fade lookup keyed by segment and distance bucket."""
		key = (id(segment), int(distance * 8.0))
		if key in self._fade_cache:
			return self._fade_cache[key]
		value = self.fade_factor(segment, distance)
		self._fade_cache[key] = value
		return value

	def update(
		self,
		dt: float,
		trigger: bool,
		eye: Vec3,
		yaw: float,
		pitch: float,
		environment_edges: list | None = None,
		occluder_hulls: list | None = None,
		occluders: list | None = None,
		boundaries: list[BoundaryPlane] | None = None,
		view_width: int = 0,
		view_height: int = 0,
		fov_y: float = DEFAULT_FOV_Y,
	) -> None:
		"""Advance timing, merge new scans, and prune fully faded geometry."""
		if self._cooldown_left > 0.0:
			self._cooldown_left = max(0.0, self._cooldown_left - dt)

		if trigger and environment_edges is not None and boundaries is not None:
			self._try_trigger(
				eye,
				yaw,
				pitch,
				environment_edges,
				occluder_hulls or [],
				occluders or [],
				boundaries,
				view_width,
				view_height,
				fov_y,
			)

		self._time += dt

		self._prune_counter += 1
		if self._prune_counter >= 4:
			self._prune_counter = 0
			self._prune_expired_segments()

	def _try_trigger(
		self,
		eye: Vec3,
		yaw: float,
		pitch: float,
		environment_edges: list,
		occluder_hulls: list,
		occluders: list,
		boundaries: list[BoundaryPlane],
		view_width: int,
		view_height: int,
		fov_y: float,
	) -> bool:
		"""Start a scan if off cooldown; merge into persistent memory."""
		if self._cooldown_left > 0.0:
			return False
		if view_width <= 0 or view_height <= 0:
			return False

		pulse = ScanPulse(
			origin=eye,
			yaw=yaw,
			pitch=pitch,
			start_time=self._time,
			wave_speed=self.wave_speed,
			max_radius=self.max_radius,
			fade_duration=self.fade_duration,
			view_width=view_width,
			view_height=view_height,
			fov_y=fov_y,
		)
		snapshot = capture_scan_snapshot(
			pulse,
			environment_edges,
			occluder_hulls,
			occluders,
			boundaries,
		)
		self._merge_snapshot(pulse, snapshot, boundaries)
		self._boundaries = boundaries
		self._occluders = list(occluders)
		self._cooldown_left = self.cooldown
		return True

	def _rearm_segment_for_pulse(
		self,
		stored: ScannedSegment,
		pulse: ScanPulse,
		incoming_intervals: list,
	) -> None:
		"""Reset a known edge so it re-reveals via wave from the current scan pose."""
		stored.start_time = pulse.start_time
		stored.reveal_origin = pulse.origin
		stored.wave_complete = False
		stored.intervals = merge_intervals(stored.intervals + list(incoming_intervals))
		stored.scanned = True
		self._refresh_segment_expire_time(stored)

	def _merge_snapshot(
		self,
		pulse: ScanPulse,
		snapshot: ScanSnapshot,
		boundaries: list[BoundaryPlane],
	) -> None:
		"""Add new scan geometry or re-arm overlapping edges with a fresh wave."""
		incoming_by_key: dict = {}
		incoming_keys: set = set()
		for segment in snapshot.segments:
			key = segment_key(segment.start, segment.end, segment.hull_bounds)
			incoming_keys.add(key)
			if key in incoming_by_key:
				prev = incoming_by_key[key]
				incoming_by_key[key] = SnapshotSegment(
					prev.start,
					prev.end,
					merge_intervals(prev.intervals + list(segment.intervals)),
					prev.hull_bounds,
				)
			else:
				incoming_by_key[key] = SnapshotSegment(
					segment.start,
					segment.end,
					list(segment.intervals),
					segment.hull_bounds,
				)

		for stored in self._memory.segments:
			key = segment_key(stored.start, stored.end, stored.hull_bounds)
			if key not in incoming_keys:
				stored.wave_complete = True
				self._refresh_segment_expire_time(stored)

		for key, segment in incoming_by_key.items():
			existing = self._memory.segment_index.get(key)
			if existing is not None:
				self._rearm_segment_for_pulse(
					self._memory.segments[existing],
					pulse,
					segment.intervals,
				)
				continue

			new_segment = ScannedSegment(
				start=segment.start,
				end=segment.end,
				intervals=list(segment.intervals),
				hull_bounds=segment.hull_bounds,
				reveal_origin=pulse.origin,
				start_time=pulse.start_time,
				scanned=True,
				wave_complete=False,
			)
			self._refresh_segment_expire_time(new_segment)
			self._memory.segments.append(new_segment)
			self._memory.segment_index[key] = len(self._memory.segments) - 1

		self._rebuild_scanned_faces(boundaries)
		self._clear_live_draw_cache()

	def _rebuild_scanned_faces(self, boundaries: list[BoundaryPlane]) -> None:
		"""Merge occluder faces from every active scanned segment."""
		snapshot_segments = [
			SnapshotSegment(seg.start, seg.end, seg.intervals, seg.hull_bounds)
			for seg in self._memory.segments
		]
		scan_eye = self._memory.segments[0].reveal_origin if self._memory.segments else (0.0, 0.0, 0.0)
		self._memory.scanned_faces = build_scanned_occluders(
			snapshot_segments,
			scan_eye,
			boundaries,
			self._occluders or [],
		)
		if self._memory.scanned_faces:
			self._memory.face_index = ScannedFaceIndex(self._memory.scanned_faces)
		else:
			self._memory.face_index = None

	def _prune_expired_segments(self) -> None:
		"""Drop segments whose fade has fully completed."""
		before = len(self._memory.segments)
		kept: list[ScannedSegment] = []
		index: dict = {}
		for segment in self._memory.segments:
			if self._segment_expired(segment):
				continue
			key = segment_key(segment.start, segment.end, segment.hull_bounds)
			index[key] = len(kept)
			kept.append(segment)
		self._memory.segments = kept
		self._memory.segment_index = index
		if before != len(kept):
			self._clear_live_draw_cache()
		if before != len(kept) and self._boundaries is not None:
			if kept:
				self._rebuild_scanned_faces(self._boundaries)
			else:
				self._memory.scanned_faces = []
				self._memory.face_index = None


__all__ = [
	"DEFAULT_FADE_OUT_SECONDS",
	"MIN_FORWARD_Z",
	"ScanDevice",
	"ScanPulse",
	"ScannedSegment",
	"ScanMemory",
	"SegmentRenderState",
	"SnapshotSegment",
	"capture_scan_snapshot",
	"forward_view_intervals",
	"screen_view_intervals",
	"segment_key",
]
