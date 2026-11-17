"""First-person camera for NULLSIGHT."""

import math

from math3d import Vec3
from player_collision import resolve_xz_move, resolve_vertical
from occlusion import Aabb

# Vertical field of view — shared by renderer and scan frustum clipping.
DEFAULT_FOV_Y = math.radians(70.0)

# Gravity and jump (meters / seconds).
GRAVITY = -20.0
JUMP_SPEED = 7.0


class Camera:
	"""Yaw/pitch camera with gravity, jumping, and sliding collision."""

	def __init__(
		self,
		position: Vec3,
		yaw: float = 0.0,
		pitch: float = 0.0,
		move_speed: float = 3.0,
		look_sensitivity: float = 0.003,
		eye_height: float = 1.6,
		pitch_limit: float = 1.45,
	) -> None:
		self.position = position
		self.yaw = yaw
		self.pitch = pitch
		self.move_speed = move_speed
		self.look_sensitivity = look_sensitivity
		self.eye_height = eye_height
		self.pitch_limit = pitch_limit
		# Vertical velocity (m/s); grounded flag updated by move_with_collision.
		self.velocity_y = 0.0
		self.grounded = True

	def eye_position(self) -> Vec3:
		"""Return the world-space eye point (feet position plus standing height)."""
		x, y, z = self.position
		return x, y + self.eye_height, z

	def apply_look(self, look_dx: float, look_dy: float) -> None:
		"""Rotate yaw/pitch from mouse or keyboard look input."""
		self.yaw += look_dx * self.look_sensitivity
		self.pitch -= look_dy * self.look_sensitivity
		self.pitch = max(-self.pitch_limit, min(self.pitch_limit, self.pitch))

	def move(self, move_x: float, move_z: float, dt: float, *, speed_multiplier: float = 1.0) -> None:
		"""Move on the XZ plane relative to current yaw (no collision / gravity)."""
		if move_x == 0.0 and move_z == 0.0:
			return

		sin_yaw = math.sin(self.yaw)
		cos_yaw = math.cos(self.yaw)
		forward_x = sin_yaw
		forward_z = cos_yaw
		right_x = cos_yaw
		right_z = -sin_yaw

		scale = self.move_speed * speed_multiplier * dt
		dx = (right_x * move_x + forward_x * move_z) * scale
		dz = (right_z * move_x + forward_z * move_z) * scale
		x, y, z = self.position
		self.position = (x + dx, y, z + dz)

	def move_with_collision(
		self,
		move_x: float,
		move_z: float,
		dt: float,
		collidables: list[Aabb],
		bounds: tuple[float, float, float, float],
		*,
		speed_multiplier: float = 1.0,
		jump_pressed: bool = False,
		base_floor_y: float = 0.0,
		ceiling_y: float | None = None,
		walkables: list[Aabb] | None = None,
	) -> None:
		"""Move with sliding collision, gravity, jump, and walkable surfaces."""
		if jump_pressed and self.grounded:
			self.velocity_y = JUMP_SPEED
			self.grounded = False

		sin_yaw = math.sin(self.yaw)
		cos_yaw = math.cos(self.yaw)
		forward_x = sin_yaw
		forward_z = cos_yaw
		right_x = cos_yaw
		right_z = -sin_yaw

		scale = self.move_speed * speed_multiplier * dt
		dx = (right_x * move_x + forward_x * move_z) * scale
		dz = (right_z * move_x + forward_z * move_z) * scale
		x, y, z = self.position
		new_x, new_z = resolve_xz_move(
			x, z, dx, dz, collidables, bounds, feet_y=y,
		)

		floors = walkables if walkables is not None else []
		self.velocity_y += GRAVITY * dt
		new_y, self.velocity_y, self.grounded = resolve_vertical(
			new_x,
			y,
			new_z,
			self.velocity_y,
			dt,
			floors,
			base_floor_y,
			ceiling_y=ceiling_y,
		)
		self.position = (new_x, new_y, new_z)

	def clamp_to_room(self, min_x: float, max_x: float, min_z: float, max_z: float, margin: float = 0.35) -> None:
		"""Keep the player inside walkable room bounds."""
		x, y, z = self.position
		x = max(min_x + margin, min(max_x - margin, x))
		z = max(min_z + margin, min(max_z - margin, z))
		self.position = (x, y, z)

	def world_to_camera(self, point: Vec3) -> Vec3:
		"""Transform a world point into camera space (X right, Y up, Z forward)."""
		eye = self.eye_position()
		x = point[0] - eye[0]
		y = point[1] - eye[1]
		z = point[2] - eye[2]

		cos_yaw = math.cos(self.yaw)
		sin_yaw = math.sin(self.yaw)
		rx = x * cos_yaw - z * sin_yaw
		rz = x * sin_yaw + z * cos_yaw

		cos_pitch = math.cos(self.pitch)
		sin_pitch = math.sin(self.pitch)
		cy = y * cos_pitch - rz * sin_pitch
		fz = y * sin_pitch + rz * cos_pitch
		return rx, cy, fz


def camera_from_scan_pose(eye: Vec3, yaw: float, pitch: float) -> Camera:
	"""Build a frozen view camera from the scan trigger pose."""
	x, y, z = eye
	# Feet sit eye_height below the scan eye so world_to_camera matches the pulse.
	return Camera(position=(x, 0.0, z), yaw=yaw, pitch=pitch, eye_height=y)
