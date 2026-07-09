"""Horizontal gain slider for pause-menu audio settings."""

from __future__ import annotations

from dataclasses import dataclass

import hyperlite

from ui_text import draw_text, text_width

TRACK_COLOR = (28, 36, 48, 255)
FILL_COLOR = (48, 255, 120, 255)
HANDLE_COLOR = (200, 255, 220, 255)
VALUE_COLOR = (140, 170, 150, 255)


@dataclass
class GainSlider:
	"""Click/drag slider mapping a horizontal track to a float range."""

	x: int
	y: int
	width: int
	height: int
	value: float
	min_value: float = 0.0
	max_value: float = 4.0

	def contains(self, mouse_x: int, mouse_y: int) -> bool:
		"""Return True when the cursor is over the slider track."""
		pad = 6
		return (
			self.x - pad <= mouse_x < self.x + self.width + pad
			and self.y - pad <= mouse_y < self.y + self.height + pad
		)

	def value_from_mouse(self, mouse_x: int) -> float:
		"""Map a mouse X coordinate to a clamped slider value."""
		if self.width <= 0:
			return self.value
		t = (mouse_x - self.x) / float(self.width)
		t = max(0.0, min(1.0, t))
		return self.min_value + (t * (self.max_value - self.min_value))

	def draw(self, engine: hyperlite.Engine, label: str, *, text_scale: int = 2) -> None:
		"""Draw label, right-aligned value, track, fill, and handle."""
		label_y = self.y - max(14, (text_scale * 14))
		draw_text(engine, self.x, label_y, label, *VALUE_COLOR[:3], scale=text_scale)

		value_text = f"{self.value:.2f}"
		value_w = text_width(value_text, text_scale)
		draw_text(
			engine,
			self.x + self.width - value_w,
			label_y,
			value_text,
			HANDLE_COLOR[0],
			HANDLE_COLOR[1],
			HANDLE_COLOR[2],
			scale=text_scale,
		)

		engine.rect_fill(self.x, self.y, self.width, self.height, *TRACK_COLOR)
		span = self.max_value - self.min_value
		norm = (self.value - self.min_value) / span if span > 0.0 else 0.0
		fill_w = max(2, int(self.width * norm))
		engine.rect_fill(self.x, self.y, fill_w, self.height, *FILL_COLOR)

		handle_x = self.x + int((self.width - 6) * norm)
		engine.rect_fill(handle_x, self.y - 2, 6, self.height + 4, *HANDLE_COLOR)
