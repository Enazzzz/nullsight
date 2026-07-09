"""Minimal startup menu — title plus Start, Settings, and Quit."""

from __future__ import annotations

from dataclasses import dataclass

import hyperlite

from menu_background import MenuWireBackground
from menu_credits import MenuCredits
from menu_layout import menu_px, menu_text_scale
from settings import GameSettings
from ui_text import draw_text, text_width
from version import __version__

OVERLAY_COLOR = (4, 6, 10, 150)
TITLE_COLOR = (48, 255, 120, 255)
BUTTON_COLOR = (28, 36, 48, 255)
BUTTON_HOVER = (40, 54, 70, 255)
BUTTON_BORDER = (80, 200, 130, 255)
LABEL_COLOR = (200, 255, 220, 255)
HINT_COLOR = (100, 130, 110, 255)


@dataclass
class _Button:
	"""Screen-space clickable button."""

	label: str
	x: int
	y: int
	width: int
	height: int
	action: str
	text_scale: int = 2

	def contains(self, mouse_x: int, mouse_y: int) -> bool:
		"""Return True when the cursor is inside this button."""
		return (
			self.x <= mouse_x < self.x + self.width
			and self.y <= mouse_y < self.y + self.height
		)

	def draw(self, engine: hyperlite.Engine, hovered: bool) -> None:
		"""Render the button background, border, and label."""
		fill = BUTTON_HOVER if hovered else BUTTON_COLOR
		engine.rect_fill(self.x, self.y, self.width, self.height, *fill)
		engine.rect_outline(self.x, self.y, self.width, self.height, *BUTTON_BORDER)

		label_w = text_width(self.label, self.text_scale)
		label_h = 7 * self.text_scale
		text_x = self.x + (self.width - label_w) // 2
		text_y = self.y + (self.height - label_h) // 2
		draw_text(
			engine,
			text_x,
			text_y,
			self.label,
			LABEL_COLOR[0],
			LABEL_COLOR[1],
			LABEL_COLOR[2],
			scale=self.text_scale,
		)


@dataclass
class _TextLink:
	"""Screen-space text-only link with hover underline."""

	label: str
	x: int
	y: int
	width: int
	height: int
	text_scale: int
	action: str

	def contains(self, mouse_x: int, mouse_y: int) -> bool:
		"""Return True when the cursor is inside this link."""
		return (
			self.x <= mouse_x < self.x + self.width
			and self.y <= mouse_y < self.y + self.height
		)

	def draw(self, engine: hyperlite.Engine, hovered: bool) -> None:
		"""Render label text and an underline while hovered."""
		color = LABEL_COLOR if hovered else HINT_COLOR
		draw_text(
			engine,
			self.x,
			self.y,
			self.label,
			color[0],
			color[1],
			color[2],
			scale=self.text_scale,
		)
		if hovered:
			underline_y = self.y + 7 * self.text_scale + max(1, self.text_scale // 2)
			underline_h = max(1, self.text_scale // 2)
			engine.rect_fill(
				self.x,
				underline_y,
				self.width,
				underline_h,
				color[0],
				color[1],
				color[2],
				255,
			)


class MainMenu:
	"""Blocking startup screen shown before first-person gameplay."""

	def __init__(self, settings: GameSettings) -> None:
		self.settings = settings
		self.active = True
		self._buttons: list[_Button] = []
		self._text_links: list[_TextLink] = []
		self._prev_left_down = False
		self._pending_action: str | None = None
		self._background = MenuWireBackground()
		self._credits = MenuCredits()

	def dismiss(self, engine: hyperlite.Engine) -> None:
		"""Leave the menu and hand control to gameplay."""
		self.active = False
		self._pending_action = None
		engine.set_mouse_captured(True)

	def return_to_title(self, engine: hyperlite.Engine) -> None:
		"""Re-show the title screen after exiting gameplay from the pause menu."""
		self.active = True
		self._pending_action = None
		self._credits.stop()
		engine.set_mouse_captured(False)

	def credits_active(self) -> bool:
		"""Return True while the rolling credits overlay is visible."""
		return self._credits.credits_active()

	def consume_action(self) -> str | None:
		"""Return and clear a one-shot action (``start``, ``settings``, or ``quit``)."""
		action = self._pending_action
		self._pending_action = None
		return action

	def _px(self, value: float) -> int:
		"""Scale a layout pixel for the current menu scale setting."""
		return menu_px(value, self.settings.menu_scale)

	def _text_scale(self, base: int) -> int:
		"""Scale bitmap label text."""
		return menu_text_scale(base, self.settings.menu_scale)

	def _layout_buttons(self, width: int, height: int) -> None:
		"""Place Start, Settings, and Quit centered on screen."""
		button_w = self._px(220)
		button_h = self._px(44)
		gap = self._px(16)
		text_scale = self._text_scale(2)
		center_x = width // 2
		start_y = height // 2
		self._buttons = [
			_Button("START", center_x - button_w // 2, start_y, button_w, button_h, "start", text_scale),
			_Button(
				"SETTINGS",
				center_x - button_w // 2,
				start_y + button_h + gap,
				button_w,
				button_h,
				"settings",
				text_scale,
			),
			_Button(
				"QUIT",
				center_x - button_w // 2,
				start_y + (button_h + gap) * 2,
				button_w,
				button_h,
				"quit",
				text_scale,
			),
		]

	def _layout_text_links(self, width: int, height: int) -> None:
		"""Place the credits link in the bottom-right corner."""
		margin = self._px(16)
		label = "CREDITS"
		text_scale = self._text_scale(1)
		label_w = text_width(label, text_scale)
		label_h = 7 * text_scale
		self._text_links = [
			_TextLink(
				label,
				width - margin - label_w,
				height - margin - label_h,
				label_w,
				label_h,
				text_scale,
				"credits",
			),
		]

	def update(
		self,
		engine: hyperlite.Engine,
		dt: float = 0.0,
		*,
		handle_input: bool = True,
		audio=None,
	) -> None:
		"""Advance the backdrop and optionally handle menu button clicks."""
		if not self.active:
			return

		self._background.update(dt)

		if self._credits.credits_active():
			self._credits.update(engine, dt)
			return

		if not handle_input:
			return

		mouse_x, mouse_y = engine.mouse_pos()
		left_down = engine.mouse_button_down(hyperlite.MouseButtons.Left)
		clicked = left_down and not self._prev_left_down
		self._prev_left_down = left_down

		if not clicked:
			return

		for link in self._text_links:
			if link.contains(mouse_x, mouse_y):
				self._credits.start(engine, audio, menu_scale=self.settings.menu_scale)
				return

		for button in self._buttons:
			if button.contains(mouse_x, mouse_y):
				self._pending_action = button.action
				return

	def draw(self, engine: hyperlite.Engine, width: int, height: int) -> None:
		"""Draw animated wireframe backdrop, title, and buttons."""
		if not self.active:
			return

		self._background.draw(engine, width, height)
		engine.rect_fill(0, 0, width, height, *OVERLAY_COLOR)
		self._layout_buttons(width, height)
		self._layout_text_links(width, height)

		if self._credits.credits_active():
			self._credits.draw(engine, width, height, menu_scale=self.settings.menu_scale)
			return

		title = "NULLSIGHT"
		title_scale = self._text_scale(6)
		title_w = text_width(title, title_scale)
		title_h = 7 * title_scale
		title_x = (width - title_w) // 2
		title_y = max(self._px(48), height // 2 - self._px(160))
		draw_text(engine, title_x, title_y, title, TITLE_COLOR[0], TITLE_COLOR[1], TITLE_COLOR[2], scale=title_scale)

		version_label = f"V{__version__}"
		version_scale = max(1, round(title_scale / 4))
		version_w = text_width(version_label, version_scale)
		draw_text(
			engine,
			(width - version_w) // 2,
			title_y + title_h + self._px(12),
			version_label,
			HINT_COLOR[0],
			HINT_COLOR[1],
			HINT_COLOR[2],
			scale=version_scale,
		)

		mouse_x, mouse_y = engine.mouse_pos()
		for button in self._buttons:
			button.draw(engine, button.contains(mouse_x, mouse_y))
		for link in self._text_links:
			link.draw(engine, link.contains(mouse_x, mouse_y))
