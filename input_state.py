"""Per-frame input polling built on top of Hyperlite's minimal input API."""

from dataclasses import dataclass, field

import hyperlite

import keys


@dataclass
class InputState:
	"""Snapshot of player input for one frame."""

	move_x: float = 0.0
	move_z: float = 0.0
	look_dx: float = 0.0
	look_dy: float = 0.0
	scan_pressed: bool = False
	sprint_held: bool = False
	pause_pressed: bool = False
	interact_pressed: bool = False
	_prev_mouse: tuple[int, int] | None = field(default=None, repr=False)
	_prev_f11_down: bool = field(default=False, repr=False)
	_prev_escape_down: bool = field(default=False, repr=False)
	_prev_scan_down: bool = field(default=False, repr=False)
	_prev_e_down: bool = field(default=False, repr=False)
	_prev_right_down: bool = field(default=False, repr=False)

	def poll(self, engine: hyperlite.Engine, *, gameplay_active: bool) -> None:
		"""Read keyboard and mouse from the engine and fill this frame's state."""
		self.move_x = 0.0
		self.move_z = 0.0
		self.look_dx = 0.0
		self.look_dy = 0.0
		self.scan_pressed = False
		self.sprint_held = False
		self.pause_pressed = False
		self.interact_pressed = False

		escape_down = engine.key_down(keys.ESCAPE)
		if escape_down and not self._prev_escape_down and gameplay_active:
			self.pause_pressed = True
		self._prev_escape_down = escape_down

		f11_down = engine.key_down(keys.F11)
		if f11_down and not self._prev_f11_down:
			engine.set_fullscreen(not engine.is_fullscreen())
		self._prev_f11_down = f11_down

		if not gameplay_active:
			return

		if engine.key_down(keys.W):
			self.move_z += 1.0
		if engine.key_down(keys.S):
			self.move_z -= 1.0
		if engine.key_down(keys.A):
			self.move_x -= 1.0
		if engine.key_down(keys.D):
			self.move_x += 1.0

		self.sprint_held = engine.key_down(keys.SHIFT)

		if not engine.mouse_captured():
			if engine.key_down(keys.LEFT):
				self.look_dx -= 1.0
			if engine.key_down(keys.RIGHT):
				self.look_dx += 1.0
			if engine.key_down(keys.UP):
				self.look_dy -= 1.0
			if engine.key_down(keys.DOWN):
				self.look_dy += 1.0

		if engine.key_down(keys.SPACE) or engine.key_down(keys.TAB):
			scan_down = True
		else:
			scan_down = False
		if scan_down and not self._prev_scan_down:
			self.scan_pressed = True
		self._prev_scan_down = scan_down

		e_down = engine.key_down(keys.E)
		right_down = engine.mouse_button_down(hyperlite.MouseButtons.Right)
		interact_down = e_down or right_down
		if interact_down and not (self._prev_e_down or self._prev_right_down):
			self.interact_pressed = True
		self._prev_e_down = e_down
		self._prev_right_down = right_down

		if engine.mouse_captured():
			delta_x, delta_y = engine.mouse_delta()
			self.look_dx += float(delta_x)
			self.look_dy += float(delta_y)
		else:
			mouse_x, mouse_y = engine.mouse_pos()
			if self._prev_mouse is not None:
				prev_x, prev_y = self._prev_mouse
				self.look_dx += float(mouse_x - prev_x)
				self.look_dy += float(mouse_y - prev_y)
			self._prev_mouse = (mouse_x, mouse_y)

		length_sq = self.move_x * self.move_x + self.move_z * self.move_z
		if length_sq > 1.0:
			inv_len = length_sq ** -0.5
			self.move_x *= inv_len
			self.move_z *= inv_len
