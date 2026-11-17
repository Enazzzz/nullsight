"""Wireframe rendering with scan clipping, occlusion, and partial visibility."""

import math

import hyperlite

from camera import Camera, DEFAULT_FOV_Y
from screen_batch import ScreenLineCollector
from math3d import (
	Vec3,
	clip_segment_to_min_z,
	intersect_intervals,
	lerp,
	length,
	sub,
	vertex_key,
)
from occlusion import Aabb, BoundaryPlane
from render_cull import _camera_point_in_frustum
from room import Edge, OccluderHull
from scan import ScanDevice, ScannedSegment
from scan_distortion import apply_scan_distortion, segment_distortion_skipped
from scan_noise_overlay import blit_scan_noise_overlay
from scan_glyph_hallucination import blit_glyph_hallucinations
from scan_occluders import ScannedOccluderCache
from scan_palette import SCAN_PACKED, SCAN_RGBA, faded_packed
from performance_config import resolve_render_perf
from player_vitals import SCANNER_MIN_VISUAL_RATIO
from settings import GameSettings

SCAN_COLOR = SCAN_RGBA
BLIND_CLEAR = (0, 0, 0, 255)
NEAR_PLANE_Z = 0.05
# Quantize fade to this many brightness buckets — one draw per bucket per interval.
_FADE_BUCKETS = 16
_FADE_BUCKETS_DENSE = 8
_DENSE_FADE_SEGMENT_THRESHOLD = 80

# Reused each frame to avoid allocating draw buffers.
_FRAME_COLLECTOR = ScreenLineCollector()


class ProjectedVertexCache:
	"""Per-frame screen projection cache keyed by quantized world vertices."""

	def __init__(
		self,
		camera: Camera,
		width: int,
		height: int,
		fov_y: float,
	) -> None:
		self._width = width
		self._height = height
		self._focal = (height * 0.5) / math.tan(fov_y * 0.5)
		self._tan_v = math.tan(fov_y * 0.5)
		self._tan_h = self._tan_v * (width / height) if height > 0 else self._tan_v
		self._screen: dict[tuple[int, int, int], tuple[int, int] | None] = {}
		self._camera: dict[tuple[int, int, int], Vec3] = {}
		self._eye = camera.eye_position()
		self._cos_yaw = math.cos(camera.yaw)
		self._sin_yaw = math.sin(camera.yaw)
		self._cos_pitch = math.cos(camera.pitch)
		self._sin_pitch = math.sin(camera.pitch)

	@property
	def width(self) -> int:
		"""Framebuffer width for view culling."""
		return self._width

	@property
	def height(self) -> int:
		"""Framebuffer height for view culling."""
		return self._height

	def world_to_camera(self, point: Vec3) -> Vec3:
		"""Transform a world point into camera space without repeated trig."""
		key = vertex_key(point)
		cached = self._camera.get(key)
		if cached is not None:
			return cached

		x = point[0] - self._eye[0]
		y = point[1] - self._eye[1]
		z = point[2] - self._eye[2]
		rx = x * self._cos_yaw - z * self._sin_yaw
		rz = x * self._sin_yaw + z * self._cos_yaw
		cy = y * self._cos_pitch - rz * self._sin_pitch
		fz = y * self._sin_pitch + rz * self._cos_pitch
		camera_point = (rx, cy, fz)
		self._camera[key] = camera_point
		return camera_point

	def project_world(self, world: Vec3) -> tuple[int, int] | None:
		"""Project a world point once and reuse the result for shared endpoints."""
		key = vertex_key(world)
		if key in self._screen:
			return self._screen[key]

		camera_point = self.world_to_camera(world)
		if camera_point[2] < NEAR_PLANE_Z:
			self._screen[key] = None
			return None

		forward_z = camera_point[2]
		screen_x = round(self._width * 0.5 + camera_point[0] * self._focal / forward_z)
		screen_y = round(self._height * 0.5 - camera_point[1] * self._focal / forward_z)
		screen = (int(screen_x), int(screen_y))
		self._screen[key] = screen
		return screen

	def segment_wholly_in_frustum(self, start: Vec3, end: Vec3) -> bool:
		"""Return True when both endpoints lie inside the view frustum (cached transforms)."""
		cam_start = self.world_to_camera(start)
		cam_end = self.world_to_camera(end)
		if cam_start[2] < NEAR_PLANE_Z or cam_end[2] < NEAR_PLANE_Z:
			return False
		return _camera_point_in_frustum(cam_start, self._tan_h, self._tan_v, NEAR_PLANE_Z) and _camera_point_in_frustum(
			cam_end, self._tan_h, self._tan_v, NEAR_PLANE_Z
		)

	def segment_visibility(self, start: Vec3, end: Vec3, margin: int = 48) -> str:
		"""Classify a segment for culling using one cached transform pass."""
		cam_start = self.world_to_camera(start)
		cam_end = self.world_to_camera(end)
		if cam_start[2] < NEAR_PLANE_Z and cam_end[2] < NEAR_PLANE_Z:
			return "culled"

		screen_start = self.project_world(start)
		screen_end = self.project_world(end)
		if screen_start is not None and screen_end is not None:
			width = self._width + margin * 2
			height = self._height + margin * 2
			min_x = -margin
			min_y = -margin
			on_screen = False
			for x, y in (screen_start, screen_end):
				if min_x <= x < width and min_y <= y < height:
					on_screen = True
					break
			if not on_screen:
				return "culled"

		if cam_start[2] < NEAR_PLANE_Z or cam_end[2] < NEAR_PLANE_Z:
			return "clip"
		if _camera_point_in_frustum(cam_start, self._tan_h, self._tan_v, NEAR_PLANE_Z) and _camera_point_in_frustum(
			cam_end, self._tan_h, self._tan_v, NEAR_PLANE_Z
		):
			return "inside"
		return "clip"


def _project_camera_point(
	cache: ProjectedVertexCache,
	camera_point: Vec3,
) -> tuple[int, int]:
	"""Project an already-clipped camera-space point."""
	forward_z = camera_point[2]
	screen_x = round(cache._width * 0.5 + camera_point[0] * cache._focal / forward_z)
	screen_y = round(cache._height * 0.5 - camera_point[1] * cache._focal / forward_z)
	return int(screen_x), int(screen_y)


def _queue_world_segment_packed(
	collector: ScreenLineCollector,
	cache: ProjectedVertexCache,
	world_start: Vec3,
	world_end: Vec3,
	packed_color: int,
) -> None:
	"""Project a world segment and record it for batched FX expansion."""
	cam_start = cache.world_to_camera(world_start)
	cam_end = cache.world_to_camera(world_end)

	clipped = clip_segment_to_min_z(cam_start, cam_end, NEAR_PLANE_Z)
	if clipped is None:
		return

	clip_start, clip_end = clipped

	if clip_start is cam_start:
		screen_start = cache.project_world(world_start)
	else:
		screen_start = _project_camera_point(cache, clip_start)

	if clip_end is cam_end:
		screen_end = cache.project_world(world_end)
	else:
		screen_end = _project_camera_point(cache, clip_end)

	if screen_start is None or screen_end is None:
		return

	x0, y0 = screen_start
	x1, y1 = screen_end
	if x0 == x1 and y0 == y1:
		return
	collector.add_packed(x0, y0, x1, y1, packed_color)


def draw_live_wireframe_edges(
	collector: ScreenLineCollector,
	cache: ProjectedVertexCache,
	edges: list[Edge],
	*,
	packed_color: int = SCAN_PACKED,
) -> None:
	"""Draw dynamic world edges at full brightness (doors, props)."""
	for start, end in edges:
		_queue_world_segment_packed(collector, cache, start, end, packed_color)


def _fade_bucket(fade: float | None, *, buckets: int = _FADE_BUCKETS) -> int:
	"""Map fade strength to a small set of draw buckets (-1 = invisible)."""
	if fade is None:
		return -1
	index = int(max(0.0, min(1.0, fade)) * (buckets - 1) + 0.5)
	return index


def _queue_faded_span(
	collector: ScreenLineCollector,
	cache: ProjectedVertexCache,
	start: Vec3,
	end: Vec3,
	t0: float,
	t1: float,
	packed_color: int,
) -> None:
	"""Project and queue one faded parametric span."""
	if t1 - t0 < 1e-5:
		return
	_queue_world_segment_packed(
		collector,
		cache,
		lerp(start, end, t0),
		lerp(start, end, t1),
		packed_color,
	)


def _draw_faded_interval(
	collector: ScreenLineCollector,
	cache: ProjectedVertexCache,
	scan: ScanDevice,
	segment: ScannedSegment,
	start: Vec3,
	end: Vec3,
	reveal_origin: Vec3,
	t0: float,
	t1: float,
	*,
	fade_buckets: int = _FADE_BUCKETS,
	motion_stress: bool = False,
) -> None:
	"""Draw one interval using at most two fade buckets (endpoint quantization)."""
	p0 = lerp(start, end, t0)
	p1 = lerp(start, end, t1)
	span_len = length(sub(p1, p0))
	d0 = length(sub(p0, reveal_origin))
	d1 = length(sub(p1, reveal_origin))

	# While moving, one midpoint bucket avoids doubling draw count on long edges.
	if motion_stress or span_len < 2.25:
		mid_fade = scan.fade_factor_cached(segment, (d0 + d1) * 0.5)
		bucket = _fade_bucket(mid_fade, buckets=fade_buckets)
		if bucket < 0:
			return
		_queue_faded_span(collector, cache, start, end, t0, t1, faded_packed(mid_fade or 0.0))
		return

	fade0 = scan.fade_factor_cached(segment, d0)
	fade1 = scan.fade_factor_cached(segment, d1)
	bucket0 = _fade_bucket(fade0, buckets=fade_buckets)
	bucket1 = _fade_bucket(fade1, buckets=fade_buckets)

	if bucket0 < 0 and bucket1 < 0:
		return
	if bucket0 < 0:
		_queue_faded_span(collector, cache, start, end, t0, t1, faded_packed(fade1 or 0.0))
		return
	if bucket1 < 0:
		_queue_faded_span(collector, cache, start, end, t0, t1, faded_packed(fade0))
		return
	if bucket0 == bucket1:
		_queue_faded_span(collector, cache, start, end, t0, t1, faded_packed(fade0))
		return

	mid_t = (t0 + t1) * 0.5
	pm = lerp(start, end, mid_t)
	fade_m = scan.fade_factor_cached(segment, length(sub(pm, reveal_origin)))
	bucket_m = _fade_bucket(fade_m, buckets=fade_buckets)
	if bucket_m < 0:
		if bucket0 >= 0:
			_queue_faded_span(collector, cache, start, end, t0, mid_t, faded_packed(fade0))
		if bucket1 >= 0:
			_queue_faded_span(collector, cache, start, end, mid_t, t1, faded_packed(fade1))
		return

	if bucket0 == bucket_m:
		_queue_faded_span(collector, cache, start, end, t0, mid_t, faded_packed(fade0))
		_queue_faded_span(collector, cache, start, end, mid_t, t1, faded_packed(fade1))
		return

	if bucket1 == bucket_m:
		_queue_faded_span(collector, cache, start, end, t0, mid_t, faded_packed(fade0))
		_queue_faded_span(collector, cache, start, end, mid_t, t1, faded_packed(fade1))
		return

	_queue_faded_span(collector, cache, start, end, t0, mid_t, faded_packed(fade0))
	_queue_faded_span(collector, cache, start, end, mid_t, t1, faded_packed(fade1))


def _draw_fading_intervals(
	collector: ScreenLineCollector,
	cache: ProjectedVertexCache,
	scan: ScanDevice,
	segment: ScannedSegment,
	start: Vec3,
	end: Vec3,
	intervals: list[tuple[float, float]],
	*,
	fade_buckets: int = _FADE_BUCKETS,
	motion_stress: bool = False,
) -> None:
	"""Queue edge intervals with wave clipping and minimal fade splits."""
	state = scan.segment_render_state(segment)
	if state.outside_wave:
		return

	reveal_origin = state.reveal_origin
	uniform_bright = state.uniform_bright
	wave_interval = state.wave_interval

	for t0, t1 in intervals:
		if state.wave_covers_full:
			active = (t0, t1)
		else:
			active = intersect_intervals((t0, t1), wave_interval)
			if active is None:
				continue

		if active[1] - active[0] < 1e-5:
			continue

		if uniform_bright:
			_queue_faded_span(
				collector,
				cache,
				start,
				end,
				active[0],
				active[1],
				SCAN_PACKED,
			)
			continue

		_draw_faded_interval(
			collector,
			cache,
			scan,
			segment,
			start,
			end,
			reveal_origin,
			active[0],
			active[1],
			fade_buckets=fade_buckets,
			motion_stress=motion_stress,
		)


def draw_wireframe_scene(
	engine: hyperlite.Engine,
	camera: Camera,
	environment_edges: list[Edge],
	occluder_hulls: list[OccluderHull],
	scan: ScanDevice,
	occluders: list[Aabb],
	boundaries: list[BoundaryPlane],
	width: int,
	height: int,
	graphics: GameSettings,
	fov_y: float = DEFAULT_FOV_Y,
	camera_speed_sq: float = 0.0,
	scanner_ratio: float = 1.0,
	scan_time: float = 0.0,
	health_ratio: float = 1.0,
) -> None:
	"""Draw all active scanned geometry with live occlusion and per-segment fade."""
	if scanner_ratio < SCANNER_MIN_VISUAL_RATIO:
		engine.clear(*BLIND_CLEAR)
		return

	if not scan.is_active:
		engine.clear(*BLIND_CLEAR)
		return

	engine.clear(*BLIND_CLEAR)

	live_eye = camera.eye_position()
	scanned_faces = scan.scanned_faces
	face_index = scan.memory.face_index
	live_cache = (
		ScannedOccluderCache(live_eye, scanned_faces, face_index)
		if scanned_faces
		else None
	)
	cache = ProjectedVertexCache(camera, width, height, fov_y)
	collector = _FRAME_COLLECTOR
	collector.clear()
	if scan.active_segments:
		collector.reserve(max(256, len(scan.active_segments) * 2))
	scan.set_frame_hints(graphics, camera_speed_sq)
	scan.begin_render_frame()
	segment_count = len(scan.active_segments)
	render_perf = resolve_render_perf(
		graphics,
		segment_count=segment_count,
		camera_speed_sq=camera_speed_sq,
	)
	fade_buckets = (
		render_perf.fade_buckets_dense
		if segment_count >= render_perf.dense_fade_segment_threshold
		else render_perf.fade_buckets
	)
	motion_stress = render_perf.motion_stress

	for segment, draw_intervals in scan.prepare_segment_draws(
		live_eye,
		scanned_faces,
		live_cache,
		view_cache=cache,
		fov_y=fov_y,
	):
		if segment_distortion_skipped(
			segment.start,
			segment.end,
			scanner_ratio,
			scan_time,
		):
			continue
		_draw_fading_intervals(
			collector,
			cache,
			scan,
			segment,
			segment.start,
			segment.end,
			draw_intervals,
			fade_buckets=fade_buckets,
			motion_stress=motion_stress,
		)

	apply_scan_distortion(
		collector, scanner_ratio, scan_time, width, height, health_ratio=health_ratio,
	)
	collector.flush(engine)
	blit_scan_noise_overlay(engine, scanner_ratio, health_ratio, scan_time, width, height)
	blit_glyph_hallucinations(engine, health_ratio, scan_time, width, height)
