"""Animated wireframe backdrop for the title screen."""

from __future__ import annotations

import math

import hyperlite

from camera import Camera
from draw_batch import pack_rgba
from math3d import Vec3, clip_segment_to_min_z
from renderer import NEAR_PLANE_Z, ProjectedVertexCache
from screen_batch import ScreenLineCollector

# Dark base cleared before wireframe lines draw.
BG_CLEAR = (2, 4, 8, 255)

# Scroll speeds in world units per second (left faster than up).
SCROLL_LEFT = 1.85
SCROLL_UP = SCROLL_LEFT * 0.5

# Large tile — 2× line density vs prior menu backdrop (76² cells).
TILE_SPAN = 180.0
CELL_COUNT = 76
CELL_STEP = TILE_SPAN / CELL_COUNT

# Fixed view: 25° yaw, 60° downward pitch — wide FOV and large tile for edge-to-edge fill.
MENU_YAW = math.radians(25.0)
MENU_PITCH = math.radians(-60.0)
MENU_EYE = (-14.0, 21.0, -18.0)
MENU_FOV_Y = math.radians(90.0)

# Random height lattice — wide cells + smooth blending for rolling hills.
NOISE_CELL = 11.0
HEIGHT_AMP = 4.2
# Re-roll lattice values slowly; cross-fade buckets so bumps drift instead of popping.
RANDOM_BUCKET_S = 8.0

# Cull margin in pixels — small pad so edge lines are not clipped harshly.
_SCREEN_MARGIN = 12

# Wire tint — ~25% brighter than prior menu backdrop.
_WIRE_COLOR = pack_rgba(28, 163, 98, 255)
_WIRE_COLOR_FAR = pack_rgba(18, 90, 60, 206)

# Reused line buffer for the menu backdrop.
_COLLECTOR = ScreenLineCollector()


def _hash_cell(ix: int, iz: int, bucket: int) -> float:
	"""Fast deterministic pseudo-random value in [0, 1) for one lattice cell."""
	h = (ix * 374761393) ^ (iz * 668265263) ^ (bucket * 1442695041)
	h = (h ^ (h >> 13)) * 1274126177
	h ^= h >> 16
	return (h & 0xFFFF) / 65535.0


def _smoothstep(value: float) -> float:
	"""Ease a 0–1 blend for softer height transitions."""
	clamped = max(0.0, min(1.0, value))
	return clamped * clamped * (3.0 - 2.0 * clamped)


def _sample_height_lattice(sample_x: float, sample_z: float, bucket: int) -> float:
	"""Bilinear value-noise height from one random lattice bucket."""
	lattice_x = sample_x / NOISE_CELL
	lattice_z = sample_z / NOISE_CELL
	x0 = math.floor(lattice_x)
	z0 = math.floor(lattice_z)
	fx = _smoothstep(lattice_x - x0)
	fz = _smoothstep(lattice_z - z0)

	ix0 = int(x0)
	iz0 = int(z0)
	h00 = _hash_cell(ix0, iz0, bucket)
	h10 = _hash_cell(ix0 + 1, iz0, bucket)
	h01 = _hash_cell(ix0, iz0 + 1, bucket)
	h11 = _hash_cell(ix0 + 1, iz0 + 1, bucket)

	top = h00 + (h10 - h00) * fx
	bottom = h01 + (h11 - h01) * fx
	value = top + (bottom - top) * fz
	return (value * 2.0 - 1.0) * HEIGHT_AMP


def _sample_height(sample_x: float, sample_z: float, time_s: float) -> float:
	"""Smooth random height with cross-faded lattice buckets over time."""
	bucket_f = time_s / RANDOM_BUCKET_S
	bucket = int(bucket_f)
	blend = _smoothstep(bucket_f - bucket)
	current = _sample_height_lattice(sample_x, sample_z, bucket)
	nxt = _sample_height_lattice(sample_x, sample_z, bucket + 1)
	return current + (nxt - current) * blend


def _project_camera_point(
	cache: ProjectedVertexCache,
	camera_point: Vec3,
) -> tuple[int, int] | None:
	"""Project a camera-space point; return None when behind the near plane."""
	if camera_point[2] < NEAR_PLANE_Z:
		return None
	forward_z = camera_point[2]
	screen_x = round(cache.width * 0.5 + camera_point[0] * cache._focal / forward_z)
	screen_y = round(cache.height * 0.5 - camera_point[1] * cache._focal / forward_z)
	return int(screen_x), int(screen_y)


def _point_on_screen(x: int, y: int, width: int, height: int, margin: int) -> bool:
	"""Return True when a pixel lies inside the screen plus margin."""
	return -margin <= x < width + margin and -margin <= y < height + margin


def _segment_on_screen(
	x0: int,
	y0: int,
	x1: int,
	y1: int,
	width: int,
	height: int,
	margin: int,
) -> bool:
	"""Return True when at least one endpoint is on-screen, or the segment crosses the view."""
	if _point_on_screen(x0, y0, width, height, margin) or _point_on_screen(x1, y1, width, height, margin):
		return True

	left = -margin
	right = width + margin
	top = -margin
	bottom = height + margin
	# Both endpoints on the same outside half — fully off-screen, safe to drop.
	if x0 < left and x1 < left:
		return False
	if x0 >= right and x1 >= right:
		return False
	if y0 < top and y1 < top:
		return False
	if y0 >= bottom and y1 >= bottom:
		return False
	# Diagonal crossing: endpoints on opposite sides — keep and let the rasterizer clip.
	return True


def _depth_color(camera_z: float) -> int:
	"""Two-step depth tint — cheaper than a full gradient per segment."""
	if camera_z < 10.0:
		return _WIRE_COLOR
	return _WIRE_COLOR_FAR


class MenuWireBackground:
	"""Scrolling random wireframe plane viewed from a fixed oblique angle."""

	def __init__(self) -> None:
		self._time_s = 0.0
		self._scroll_x = 0.0
		self._scroll_z = 0.0
		self._camera = Camera(position=(MENU_EYE[0], 0.0, MENU_EYE[2]), yaw=MENU_YAW, pitch=MENU_PITCH)
		self._camera.eye_height = MENU_EYE[1]

	def update(self, dt: float) -> None:
		"""Advance scroll offsets and random lattice phase."""
		if dt <= 0.0:
			return
		self._time_s += dt
		self._scroll_x = (self._scroll_x + SCROLL_LEFT * dt) % TILE_SPAN
		self._scroll_z = (self._scroll_z + SCROLL_UP * dt) % TILE_SPAN

	def draw(self, engine: hyperlite.Engine, width: int, height: int) -> None:
		"""Render the tiled random height field; cull only fully off-screen segments."""
		engine.clear(*BG_CLEAR)
		if width <= 0 or height <= 0:
			return

		cache = ProjectedVertexCache(self._camera, width, height, fov_y=MENU_FOV_Y)
		margin = _SCREEN_MARGIN
		collector = _COLLECTOR
		collector.clear()
		collector.reserve(4096)

		half = TILE_SPAN * 0.5

		# Camera-space vertex + packed color — always built; segments clip at draw time.
		prev_row: list[tuple[Vec3, int]] = []

		def _build_vertex(base_x: float, base_z: float) -> tuple[Vec3, int]:
			"""Sample height and transform to camera space."""
			# Continuous scroll coords — never wrap per vertex or adjacent grid points
			# can land on opposite sides of the noise field and spike in height.
			sample_x = base_x - self._scroll_x
			sample_z = base_z + self._scroll_z
			world_y = _sample_height(sample_x, sample_z, self._time_s)
			cam = cache.world_to_camera((base_x, world_y, base_z))
			return cam, _depth_color(cam[2])

		def _queue_segment(a: tuple[Vec3, int], b: tuple[Vec3, int]) -> None:
			"""Draw a segment unless both clipped endpoints lie fully off-screen."""
			cam_a, color_a = a
			cam_b, color_b = b
			if cam_a[2] < NEAR_PLANE_Z and cam_b[2] < NEAR_PLANE_Z:
				return

			clipped = clip_segment_to_min_z(cam_a, cam_b, NEAR_PLANE_Z)
			if clipped is None:
				return
			clip_a, clip_b = clipped

			screen_a = _project_camera_point(cache, clip_a)
			screen_b = _project_camera_point(cache, clip_b)
			if screen_a is None or screen_b is None:
				return

			x0, y0 = screen_a
			x1, y1 = screen_b
			if x0 == x1 and y0 == y1:
				return
			if not _segment_on_screen(x0, y0, x1, y1, width, height, margin):
				return

			color = color_a if clip_a is cam_a else color_b
			collector.add_packed(x0, y0, x1, y1, color)

		for row in range(CELL_COUNT + 1):
			base_z = -half + row * CELL_STEP
			cur_row: list[tuple[Vec3, int]] = []
			for col in range(CELL_COUNT + 1):
				base_x = -half + col * CELL_STEP
				cur_row.append(_build_vertex(base_x, base_z))

			if row > 0:
				for col in range(CELL_COUNT):
					_queue_segment(cur_row[col], cur_row[col + 1])
				for col in range(CELL_COUNT + 1):
					_queue_segment(prev_row[col], cur_row[col])

			prev_row = cur_row

		collector.flush(engine)
