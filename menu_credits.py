"""Full-screen rolling credits overlay for the title screen."""

from __future__ import annotations

from dataclasses import dataclass

import hyperlite

import keys
from credits_data import CreditsData, CreditsEntry, load_credits_data, scroll_delay_for_title_sync
from menu_layout import menu_px, menu_text_scale
from ui_text import draw_text, text_width

OVERLAY_COLOR = (2, 4, 8, 210)
TITLE_COLOR = (48, 255, 120, 255)
HEADING_COLOR = (200, 255, 220, 255)
NAME_COLOR = (140, 180, 155, 255)
SKIP_COLOR = (90, 115, 100, 255)
SKIP_HOVER_COLOR = (200, 255, 220, 255)

# Extra screen heights of padding before the first line and after the last.
_LEAD_IN_SCREEN_HEIGHTS = 1.15
_TRAIL_OUT_SCREEN_HEIGHTS = 0.85


@dataclass
class _PlacedLine:
	"""One rendered credits row with precomputed vertical offset in the scroll block."""

	text: str
	y_offset: int
	text_scale: int
	color: tuple[int, int, int, int]


class MenuCredits:
	"""Scrolls configurable credit lines upward over a darkened backdrop."""

	def __init__(self, data: CreditsData | None = None) -> None:
		self._data = data or load_credits_data()
		self.active = False
		self._scroll_delay_remaining = 0.0
		self._scroll_y = 0.0
		self._total_scroll = 0.0
		self._placed_lines: list[_PlacedLine] = []
		self._layout_width = 0
		self._layout_height = 0
		self._prev_left_down = False
		self._prev_escape_down = False
		self._audio = None

	def credits_active(self) -> bool:
		"""Return True while the credits roll is on screen."""
		return self.active

	def start(self, engine: hyperlite.Engine, audio=None, *, menu_scale: float = 1.0) -> None:
		"""Begin the credits roll and optional soundtrack."""
		width, height = engine.window_size()
		self._rebuild_layout(width, height, menu_scale)
		self.active = True
		self._scroll_delay_remaining = self._scroll_delay_for_start(height)
		self._scroll_y = 0.0
		self._prev_left_down = engine.mouse_button_down(hyperlite.MouseButtons.Left)
		self._prev_escape_down = engine.key_down(keys.ESCAPE)
		self._audio = audio
		if audio is not None:
			audio.on_credits_show(
				self._data.music.file,
				start_seconds=self._data.music.start_seconds,
			)

	def stop(self) -> None:
		"""End the roll immediately and restore title music."""
		if not self.active:
			return
		self.active = False
		self._scroll_delay_remaining = 0.0
		if self._audio is not None:
			self._audio.on_credits_hide()
			self._audio = None

	def update(self, engine: hyperlite.Engine, dt: float) -> None:
		"""Advance scroll position and handle skip input."""
		if not self.active:
			return

		left_down = engine.mouse_button_down(hyperlite.MouseButtons.Left)
		clicked = left_down and not self._prev_left_down
		self._prev_left_down = left_down

		escape_down = engine.key_down(keys.ESCAPE)
		escape_clicked = escape_down and not self._prev_escape_down
		self._prev_escape_down = escape_down

		if clicked or escape_clicked:
			self.stop()
			return

		if self._scroll_delay_remaining > 0.0:
			if dt > 0.0:
				self._scroll_delay_remaining = max(0.0, self._scroll_delay_remaining - dt)
			return

		if dt > 0.0:
			self._scroll_y += self._data.scroll_speed_px_per_sec * dt

		if self._scroll_y >= self._total_scroll:
			self.stop()

	def draw(self, engine: hyperlite.Engine, width: int, height: int, *, menu_scale: float = 1.0) -> None:
		"""Draw the dark overlay, scrolling lines, and skip hint."""
		if not self.active:
			return

		if width != self._layout_width or height != self._layout_height:
			self._rebuild_layout(width, height, menu_scale)

		engine.rect_fill(0, 0, width, height, *OVERLAY_COLOR)

		base_y = int(height * _LEAD_IN_SCREEN_HEIGHTS) - int(self._scroll_y)
		for line in self._placed_lines:
			screen_y = base_y + line.y_offset
			if screen_y < -line.text_scale * 10 or screen_y > height + line.text_scale * 10:
				continue
			text_w = text_width(line.text, line.text_scale)
			text_x = (width - text_w) // 2
			draw_text(
				engine,
				text_x,
				screen_y,
				line.text,
				line.color[0],
				line.color[1],
				line.color[2],
				scale=line.text_scale,
			)

		self._draw_skip_hint(engine, width, height, menu_scale)

	def _px(self, value: float, menu_scale: float) -> int:
		"""Scale a layout pixel for the current menu scale setting."""
		return menu_px(value, menu_scale)

	def _text_scale(self, base: int, menu_scale: float) -> int:
		"""Scale bitmap label text."""
		return menu_text_scale(base, menu_scale)

	def _style_for_entry(self, entry: CreditsEntry, menu_scale: float) -> tuple[int, tuple[int, int, int, int], int]:
		"""Return text scale, RGBA color, and trailing gap for one entry type."""
		if entry.entry_type == "title":
			return self._text_scale(4, menu_scale), TITLE_COLOR, self._px(28, menu_scale)
		if entry.entry_type == "heading":
			return self._text_scale(2, menu_scale), HEADING_COLOR, self._px(10, menu_scale)
		if entry.entry_type == "role":
			return self._text_scale(1, menu_scale), NAME_COLOR, self._px(6, menu_scale)
		# name, body, and unknown types share the default body style.
		return self._text_scale(2, menu_scale), NAME_COLOR, self._px(8, menu_scale)

	def _scroll_delay_for_start(self, height: int) -> float:
		"""Return the stationary hold before scroll, synced to music when configured."""
		title_appear = self._data.music.title_appear_seconds
		if title_appear is not None:
			return scroll_delay_for_title_sync(
				height=height,
				scroll_speed_px_per_sec=self._data.scroll_speed_px_per_sec,
				music_start_seconds=self._data.music.start_seconds,
				title_appear_seconds=title_appear,
				lead_in_screen_heights=_LEAD_IN_SCREEN_HEIGHTS,
			)
		return self._data.scroll_delay_seconds

	def _rebuild_layout(self, width: int, height: int, menu_scale: float) -> None:
		"""Precompute line offsets for the current viewport size."""
		self._layout_width = width
		self._layout_height = height
		self._placed_lines = []

		gap_row_h = self._px(18, menu_scale)
		cursor_y = 0
		for entry in self._data.entries:
			if entry.entry_type == "gap":
				cursor_y += gap_row_h * entry.gap_lines
				continue
			text_scale, color, gap_after = self._style_for_entry(entry, menu_scale)
			self._placed_lines.append(
				_PlacedLine(
					text=entry.text,
					y_offset=cursor_y,
					text_scale=text_scale,
					color=color,
				)
			)
			cursor_y += 7 * text_scale + gap_after

		content_height = cursor_y
		lead_in = int(height * _LEAD_IN_SCREEN_HEIGHTS)
		trail_out = int(height * _TRAIL_OUT_SCREEN_HEIGHTS)
		self._total_scroll = float(lead_in + content_height + trail_out)

	def _draw_skip_hint(self, engine: hyperlite.Engine, width: int, height: int, menu_scale: float) -> None:
		"""Draw a minimal skip link centered along the bottom edge."""
		label = "CLICK OR ESC TO SKIP"
		text_scale = self._text_scale(1, menu_scale)
		label_w = text_width(label, text_scale)
		label_h = 7 * text_scale
		margin = self._px(14, menu_scale)
		x = (width - label_w) // 2
		y = height - margin - label_h

		mouse_x, mouse_y = engine.mouse_pos()
		hovered = x <= mouse_x < x + label_w and y <= mouse_y < y + label_h
		color = SKIP_HOVER_COLOR if hovered else SKIP_COLOR
		draw_text(engine, x, y, label, color[0], color[1], color[2], scale=text_scale)
		if hovered:
			underline_y = y + label_h + max(1, self._px(1, menu_scale))
			engine.rect_fill(x, underline_y, label_w, max(1, text_scale // 2), color[0], color[1], color[2], 255)
