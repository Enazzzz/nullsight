"""Escape-menu overlay with Resume, Settings, and Close."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import hyperlite

from engine_config import sync_present_mode, sync_vsync
from menu_layout import MENU_SCALE_MAX, MENU_SCALE_MIN, menu_px, menu_text_scale
from menu_slider import GainSlider
from menu_tooltip_ui import TOOLTIP_HOVER_DELAY_SEC, draw_setting_tooltip
from settings import GameSettings
from settings_store import save_settings
from ui_text import draw_text, text_width

if TYPE_CHECKING:
	from nullsight_audio import NullsightAudio

# Pause screen palette.
OVERLAY_COLOR = (6, 8, 12, 255)
PANEL_COLOR = (16, 22, 30, 255)
PANEL_BORDER = (48, 255, 120, 255)
BUTTON_COLOR = (28, 36, 48, 255)
BUTTON_HOVER = (40, 54, 70, 255)
BUTTON_BORDER = (80, 200, 130, 255)
LABEL_COLOR = (200, 255, 220, 255)
MUTED_COLOR = (140, 170, 150, 255)

SETTINGS_ROW_GAP = 52
SETTINGS_FIRST_ROW_Y = 84
# OSD submenu rows: label, ``OsdSettings`` field name.
_OSD_ROWS: tuple[tuple[str, str], ...] = (
	("FPS CUR", "fps_cur"),
	("AVG 6S", "fps_avg"),
	("MIN 6S", "fps_min"),
	("MAX 6S", "fps_max"),
	("INP MS", "inp_ms"),
	("SIM MS", "sim_ms"),
	("SCENE MS", "scene_ms"),
	("UI MS", "ui_ms"),
	("RAST MS", "rast_ms"),
	("QUEUE MS", "rec_ms"),
	("PRE MS", "pre_ms"),
	("ACOUSTIC", "acoustic_path"),
)
# Graphics perf rows: label, ``PerfSettings`` field name.
_GRAPHICS_ROWS: tuple[tuple[str, str], ...] = (
	("COARSE FADE", "coarse_fade"),
	("DENSE FADE", "dense_coarse_fade"),
	("OCCL STRIDE", "throttle_live_occlusion"),
	("OCCL CACHE", "wide_occlusion_cache"),
	("SKIP CLIP", "skip_screen_clip_moving"),
	("FADE MOVE", "simple_fade_moving"),
)
# Audio submenu rows: label, ``AudioSettings`` field name.
_AUDIO_TOGGLE_ROWS: tuple[tuple[str, str], ...] = (
	("AUDIO", "enabled"),
	("HRTF", "hrtf_enabled"),
	("RAY AUDIO", "acoustic_obstruction"),
	("DOPPLER", "enable_doppler"),
	("AIR ABS", "enable_hf_air_absorption"),
	("PROP DELAY", "enable_propagation_delay"),
	("HEAD SHADOW", "enable_head_shadow"),
	("OUTDOOR AIR", "use_atmospheric_conditions"),
)
# label, AudioSettings field, min, max
_AUDIO_GAIN_SLIDERS: tuple[tuple[str, str, float, float], ...] = (
	("MASTER GAIN", "master_gain", 0.0, 2.0),
	("SPATIAL GAIN", "spatial_gain", 0.0, 8.0),
	("MUSIC GAIN", "music_gain", 0.0, 1.5),
)
_AUDIO_SLIDER_ROW_H = 56
_GRAPHICS_SLIDER_ROW_H = 56
_SETTING_ROW_H = 44


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


class PauseMenu:
	"""Fullscreen pause overlay toggled with Escape."""

	def __init__(self, settings: GameSettings) -> None:
		self.settings = settings
		self.is_open = False
		self._from_title_screen = False
		self._screen = "main"
		self._buttons: list[_Button] = []
		self._prev_left_down = False
		self._tooltip_hover_key: str | None = None
		self._tooltip_hover_since = 0.0
		self._gain_sliders: list[tuple[str, GainSlider]] = []
		self._graphics_sliders: list[tuple[str, GainSlider]] = []
		self._dragging_gain_attr: str | None = None
		self._dragging_graphics_attr: str | None = None
		self._suppress_next_click = False

	def _px(self, value: float) -> int:
		"""Scale a layout pixel for the current menu scale setting."""
		return menu_px(value, self.settings.menu_scale)

	def _ts(self, base: int) -> int:
		"""Scale bitmap label text."""
		return menu_text_scale(base, self.settings.menu_scale)

	def _row_gap(self) -> int:
		"""Vertical spacing between settings rows."""
		return self._px(SETTINGS_ROW_GAP)

	def _first_row_y(self, panel_y: int) -> int:
		"""Y offset of the first settings row inside a panel."""
		return panel_y + self._px(SETTINGS_FIRST_ROW_Y)

	def _label_y(self, panel_y: int) -> int:
		"""Y offset for row labels beside toggles."""
		return panel_y + self._px(94)

	def _settings_nav_layout(self, panel_x: int, panel_w: int) -> tuple[int, int]:
		"""Return x and width for full-width submenu nav buttons in the settings panel."""
		margin = self._px(24)
		nav_x = panel_x + margin
		nav_w = panel_w - margin * 2
		return nav_x, nav_w

	def _reset_tooltip_hover(self) -> None:
		"""Clear delayed tooltip state after screen change or menu close."""
		self._tooltip_hover_key = None
		self._tooltip_hover_since = 0.0

	def open(self, engine: hyperlite.Engine, *, from_title_screen: bool = False) -> None:
		"""Show the pause menu and release mouse capture for clicking."""
		self._from_title_screen = from_title_screen
		self.is_open = True
		self._screen = "main"
		self._reset_tooltip_hover()
		engine.set_mouse_captured(False)

	def open_settings(self, engine: hyperlite.Engine, *, from_title_screen: bool = False) -> None:
		"""Open the settings hub directly (title screen shortcut or in-game)."""
		self._from_title_screen = from_title_screen
		self.is_open = True
		self._screen = "settings"
		self._reset_tooltip_hover()
		self._suppress_next_click = True
		engine.set_mouse_captured(False)

	def close(self, engine: hyperlite.Engine) -> None:
		"""Close the pause overlay without resuming gameplay."""
		self.is_open = False
		self._screen = "main"
		self._from_title_screen = False
		self._reset_tooltip_hover()
		engine.set_mouse_captured(False)

	def close_to_title(self, engine: hyperlite.Engine) -> None:
		"""Close settings overlay and return to the title screen."""
		self.is_open = False
		self._screen = "main"
		self._from_title_screen = False
		self._reset_tooltip_hover()
		self._suppress_next_click = True
		engine.set_mouse_captured(False)

	def resume(self, engine: hyperlite.Engine) -> None:
		"""Hide the menu and return to first-person control."""
		self._from_title_screen = False
		self.is_open = False
		self._screen = "main"
		self._reset_tooltip_hover()
		engine.set_mouse_captured(True)

	def _panel(self, width: int, height: int, screen: str) -> tuple[int, int, int, int]:
		"""Return panel width, height, x, and y for a menu screen."""
		panel_w = min(self._px(420), width - self._px(80))
		row_gap = self._row_gap()
		first_row = self._px(SETTINGS_FIRST_ROW_Y)
		if screen == "main":
			panel_h = min(self._px(280), height - self._px(80))
		elif screen == "settings":
			panel_h = min(self._px(40) + first_row + row_gap * 7 + self._px(80), height - self._px(80))
		elif screen == "audio":
			slider_block = self._px(_AUDIO_SLIDER_ROW_H) * len(_AUDIO_GAIN_SLIDERS)
			toggle_block = row_gap * len(_AUDIO_TOGGLE_ROWS)
			panel_h = min(self._px(40) + first_row + slider_block + toggle_block + self._px(80), height - self._px(40))
		elif screen == "osd":
			panel_h = min(self._px(40) + first_row + row_gap * len(_OSD_ROWS) + self._px(80), height - self._px(40))
		elif screen == "graphics":
			slider_block = self._px(_GRAPHICS_SLIDER_ROW_H)
			panel_h = min(
				self._px(40) + first_row + slider_block + row_gap * len(_GRAPHICS_ROWS) + self._px(80),
				height - self._px(40),
			)
		else:
			panel_h = min(self._px(300), height - self._px(80))
		panel_x = (width - panel_w) // 2
		panel_y = (height - panel_h) // 2
		return panel_w, panel_h, panel_x, panel_y

	def _back_button(self, width: int, panel_y: int, panel_h: int, action: str) -> _Button:
		"""Place a centered back button at the bottom of a panel."""
		back_w = self._px(160)
		back_h = self._px(44)
		return _Button(
			"BACK",
			(width - back_w) // 2,
			panel_y + panel_h - back_h - self._px(24),
			back_w,
			back_h,
			action,
			self._ts(2),
		)

	def _toggle_button(
		self,
		enabled: bool,
		x: int,
		y: int,
		width: int,
		height: int,
		action: str,
	) -> _Button:
		"""Build an ON/OFF toggle button."""
		return _Button("ON" if enabled else "OFF", x, y, width, height, action, self._ts(2))

	def _layout_main_buttons(self, width: int, height: int) -> list[_Button]:
		"""Compute centered Resume / Settings / Main Menu button placement."""
		button_w = self._px(220)
		button_h = self._px(48)
		gap = self._px(14)
		text_scale = self._ts(2)
		total_h = button_h * 3 + gap * 2
		start_y = (height - total_h) // 2
		center_x = (width - button_w) // 2

		return [
			_Button("RESUME", center_x, start_y, button_w, button_h, "resume", text_scale),
			_Button("SETTINGS", center_x, start_y + button_h + gap, button_w, button_h, "settings", text_scale),
			_Button(
				"MAIN MENU",
				center_x,
				start_y + (button_h + gap) * 2,
				button_w,
				button_h,
				"main_menu",
				text_scale,
			),
		]

	def _layout_settings_buttons(self, width: int, height: int) -> list[_Button]:
		"""Compute engine toggles and submenu entries."""
		panel_w, panel_h, panel_x, panel_y = self._panel(width, height, "settings")

		toggle_w = self._px(88)
		toggle_h = self._px(40)
		toggle_x = panel_x + panel_w - toggle_w - self._px(24)
		row_gap = self._row_gap()
		vsync_row_y = self._first_row_y(panel_y)
		present_row_y = vsync_row_y + row_gap
		quiet_row_y = present_row_y + row_gap
		cap_row_y = quiet_row_y + row_gap
		osd_row_y = cap_row_y + row_gap
		graphics_row_y = osd_row_y + row_gap
		audio_row_y = graphics_row_y + row_gap

		nav_x, nav_w = self._settings_nav_layout(panel_x, panel_w)
		text_scale = self._ts(2)

		return [
			self._toggle_button(
				self.settings.vsync,
				toggle_x,
				vsync_row_y,
				toggle_w,
				toggle_h,
				"toggle_vsync",
			),
			self._toggle_button(
				self.settings.double_buffered_present,
				toggle_x,
				present_row_y,
				toggle_w,
				toggle_h,
				"toggle_double_buffer",
			),
			self._toggle_button(
				self.settings.quiet_unfocused,
				toggle_x,
				quiet_row_y,
				toggle_w,
				toggle_h,
				"toggle_quiet_unfocused",
			),
			self._toggle_button(
				self.settings.cap_fps_unfocused,
				toggle_x,
				cap_row_y,
				toggle_w,
				toggle_h,
				"toggle_cap_fps_unfocused",
			),
			_Button("OSD", nav_x, osd_row_y, nav_w, toggle_h, "osd", text_scale),
			_Button("GRAPHICS", nav_x, graphics_row_y, nav_w, toggle_h, "graphics", text_scale),
			_Button("AUDIO", nav_x, audio_row_y, nav_w, toggle_h, "audio", text_scale),
			self._back_button(width, panel_y, panel_h, "back_main"),
		]

	def _layout_osd_buttons(self, width: int, height: int) -> list[_Button]:
		"""Compute per-line OSD toggles and back-to-settings button."""
		panel_w, panel_h, panel_x, panel_y = self._panel(width, height, "osd")
		osd = self.settings.osd

		toggle_w = self._px(88)
		toggle_h = self._px(40)
		toggle_x = panel_x + panel_w - toggle_w - self._px(24)
		row_y = self._first_row_y(panel_y)
		row_gap = self._row_gap()

		buttons: list[_Button] = []
		for _label, attr in _OSD_ROWS:
			enabled = bool(getattr(osd, attr))
			action = f"toggle_osd_{attr}"
			buttons.append(
				self._toggle_button(enabled, toggle_x, row_y, toggle_w, toggle_h, action)
			)
			row_y += row_gap

		buttons.append(self._back_button(width, panel_y, panel_h, "back_settings"))
		return buttons

	def _layout_graphics_buttons(self, width: int, height: int) -> list[_Button]:
		"""Compute menu scale slider, graphics perf toggles, and back button."""
		panel_w, panel_h, panel_x, panel_y = self._panel(width, height, "graphics")
		perf = self.settings.perf

		toggle_w = self._px(88)
		toggle_h = self._px(40)
		toggle_x = panel_x + panel_w - toggle_w - self._px(24)
		slider_top = self._first_row_y(panel_y)
		slider_w = panel_w - self._px(48)
		slider_row_h = self._px(_GRAPHICS_SLIDER_ROW_H)

		self._graphics_sliders = [
			(
				"menu_scale",
				GainSlider(
					panel_x + self._px(24),
					slider_top + self._px(28),
					slider_w,
					self._px(14),
					float(self.settings.menu_scale),
					MENU_SCALE_MIN,
					MENU_SCALE_MAX,
				),
			)
		]

		row_y = slider_top + slider_row_h
		row_gap = self._row_gap()
		buttons: list[_Button] = []
		for _label, attr in _GRAPHICS_ROWS:
			enabled = bool(getattr(perf, attr))
			action = f"toggle_perf_{attr}"
			buttons.append(
				self._toggle_button(enabled, toggle_x, row_y, toggle_w, toggle_h, action)
			)
			row_y += row_gap

		buttons.append(self._back_button(width, panel_y, panel_h, "back_settings"))
		return buttons

	def _layout_audio_buttons(self, width: int, height: int) -> list[_Button]:
		"""Compute spatial audio toggles and back-to-settings button."""
		panel_w, panel_h, panel_x, panel_y = self._panel(width, height, "audio")
		audio = self.settings.audio

		toggle_w = self._px(88)
		toggle_h = self._px(40)
		toggle_x = panel_x + panel_w - toggle_w - self._px(24)
		slider_top = self._first_row_y(panel_y)
		slider_w = panel_w - self._px(48)
		slider_row_h = self._px(_AUDIO_SLIDER_ROW_H)

		self._gain_sliders = []
		row_y = slider_top
		for _label, attr, min_value, max_value in _AUDIO_GAIN_SLIDERS:
			value = float(getattr(audio, attr))
			self._gain_sliders.append(
				(
					attr,
					GainSlider(
						panel_x + self._px(24),
						row_y + self._px(28),
						slider_w,
						self._px(14),
						value,
						min_value,
						max_value,
					),
				)
			)
			row_y += slider_row_h

		row_y += self._px(8)
		row_gap = self._row_gap()
		buttons: list[_Button] = []
		for _label, attr in _AUDIO_TOGGLE_ROWS:
			enabled = bool(getattr(audio, attr))
			action = f"toggle_audio_{attr}"
			buttons.append(
				self._toggle_button(enabled, toggle_x, row_y, toggle_w, toggle_h, action)
			)
			row_y += row_gap

		buttons.append(self._back_button(width, panel_y, panel_h, "back_settings"))
		return buttons

	def _setting_row_rect(
		self,
		panel_x: int,
		panel_w: int,
		row_y: int,
	) -> tuple[int, int, int, int]:
		"""Return a hover hit box for one settings label plus toggle row."""
		return (panel_x + self._px(4), row_y - self._px(6), panel_w - self._px(8), self._px(_SETTING_ROW_H))

	def _tooltip_regions(self, width: int, height: int) -> list[tuple[str, int, int, int, int]]:
		"""Return tooltip keys and screen rects for the active menu screen."""
		if self._screen == "settings":
			panel_w, _, panel_x, panel_y = self._panel(width, height, "settings")
			row_y = self._first_row_y(panel_y)
			row_gap = self._row_gap()
			nav_x, nav_w = self._settings_nav_layout(panel_x, panel_w)
			row_h = self._px(_SETTING_ROW_H)
			return [
				("toggle_vsync", *self._setting_row_rect(panel_x, panel_w, row_y)),
				(
					"toggle_double_buffer",
					*self._setting_row_rect(panel_x, panel_w, row_y + row_gap),
				),
				(
					"quiet_unfocused",
					*self._setting_row_rect(panel_x, panel_w, row_y + row_gap * 2),
				),
				(
					"cap_fps_unfocused",
					*self._setting_row_rect(panel_x, panel_w, row_y + row_gap * 3),
				),
				("osd", nav_x, row_y + row_gap * 4, nav_w, row_h),
				("graphics", nav_x, row_y + row_gap * 5, nav_w, row_h),
				("audio", nav_x, row_y + row_gap * 6, nav_w, row_h),
			]

		if self._screen == "audio":
			panel_w, _, panel_x, panel_y = self._panel(width, height, "audio")
			regions: list[tuple[str, int, int, int, int]] = []
			row_y = self._first_row_y(panel_y)
			slider_row_h = self._px(_AUDIO_SLIDER_ROW_H)
			for _label, attr, _min_value, _max_value in _AUDIO_GAIN_SLIDERS:
				regions.append((attr, *self._setting_row_rect(panel_x, panel_w, row_y)))
				row_y += slider_row_h
			row_y += self._px(8)
			row_gap = self._row_gap()
			for _label, attr in _AUDIO_TOGGLE_ROWS:
				regions.append((attr, *self._setting_row_rect(panel_x, panel_w, row_y)))
				row_y += row_gap
			return regions

		if self._screen == "osd":
			panel_w, _, panel_x, panel_y = self._panel(width, height, "osd")
			row_y = self._first_row_y(panel_y)
			row_gap = self._row_gap()
			regions: list[tuple[str, int, int, int, int]] = []
			for _label, attr in _OSD_ROWS:
				regions.append((attr, *self._setting_row_rect(panel_x, panel_w, row_y)))
				row_y += row_gap
			return regions

		if self._screen == "graphics":
			panel_w, _, panel_x, panel_y = self._panel(width, height, "graphics")
			regions: list[tuple[str, int, int, int, int]] = []
			row_y = self._first_row_y(panel_y)
			regions.append(("menu_scale", *self._setting_row_rect(panel_x, panel_w, row_y)))
			row_y += self._px(_GRAPHICS_SLIDER_ROW_H)
			row_gap = self._row_gap()
			for _label, attr in _GRAPHICS_ROWS:
				regions.append((attr, *self._setting_row_rect(panel_x, panel_w, row_y)))
				row_y += row_gap
			return regions

		return []

	def _tooltip_key_at(self, mouse_x: int, mouse_y: int, width: int, height: int) -> str | None:
		"""Return the tooltip key under the cursor, if any."""
		for key, rect_x, rect_y, rect_w, rect_h in self._tooltip_regions(width, height):
			if rect_x <= mouse_x < rect_x + rect_w and rect_y <= mouse_y < rect_y + rect_h:
				return key
		return None

	def _update_tooltip_hover(self, mouse_x: int, mouse_y: int, width: int, height: int) -> str | None:
		"""Track hover duration; return key when the delay has elapsed."""
		hover_key = self._tooltip_key_at(mouse_x, mouse_y, width, height)
		now = time.perf_counter()
		if hover_key is None:
			self._reset_tooltip_hover()
			return None
		if hover_key != self._tooltip_hover_key:
			self._tooltip_hover_key = hover_key
			self._tooltip_hover_since = now
			return None
		if now - self._tooltip_hover_since >= TOOLTIP_HOVER_DELAY_SEC:
			return hover_key
		return None

	def _go_to_screen(self, screen: str) -> None:
		"""Switch menu screen and reset tooltip timing."""
		self._screen = screen
		self._reset_tooltip_hover()

	def _layout_buttons(self, width: int, height: int) -> list[_Button]:
		"""Return buttons for the active menu screen."""
		if self._screen == "main":
			return self._layout_main_buttons(width, height)
		if self._screen == "graphics":
			return self._layout_graphics_buttons(width, height)
		if self._screen == "audio":
			return self._layout_audio_buttons(width, height)
		if self._screen == "osd":
			return self._layout_osd_buttons(width, height)
		return self._layout_settings_buttons(width, height)

	def _toggle_osd(self, attr: str) -> None:
		"""Flip one boolean field on the nested OSD settings object."""
		current = getattr(self.settings.osd, attr)
		setattr(self.settings.osd, attr, not current)

	def _toggle_perf(self, attr: str) -> None:
		"""Flip one boolean field on the nested perf settings object."""
		current = getattr(self.settings.perf, attr)
		setattr(self.settings.perf, attr, not current)

	def _toggle_audio(self, attr: str, audio: NullsightAudio | None) -> None:
		"""Flip one boolean field on the nested audio settings object."""
		current = getattr(self.settings.audio, attr)
		setattr(self.settings.audio, attr, not current)
		if audio is not None:
			audio.apply_settings(self.settings.audio)
			# When ray obstruction is turned off, restore clear-path gains/LPF.
			if attr == "acoustic_obstruction" and not self.settings.audio.acoustic_obstruction:
				audio.clear_acoustic_obstruction()

	def _set_audio_gain(self, attr: str, value: float, audio: NullsightAudio | None) -> None:
		"""Update one float gain field from an audio submenu slider."""
		for _label, field_name, min_value, max_value in _AUDIO_GAIN_SLIDERS:
			if field_name != attr:
				continue
			clamped = max(min_value, min(max_value, value))
			setattr(self.settings.audio, attr, clamped)
			if audio is not None:
				audio.apply_settings(self.settings.audio)
			return

	def _set_menu_scale(self, value: float) -> None:
		"""Update global title and pause menu scale."""
		clamped = max(MENU_SCALE_MIN, min(MENU_SCALE_MAX, value))
		self.settings.menu_scale = clamped

	def _handle_graphics_sliders(
		self,
		engine: hyperlite.Engine,
		mouse_x: int,
		mouse_y: int,
		left_down: bool,
		clicked: bool,
	) -> None:
		"""Drag or click the menu scale slider on the graphics screen."""
		if not self._graphics_sliders:
			return

		if clicked:
			for attr, slider in self._graphics_sliders:
				if slider.contains(mouse_x, mouse_y):
					self._dragging_graphics_attr = attr
					break

		if not left_down:
			self._dragging_graphics_attr = None

		if self._dragging_graphics_attr is None:
			return

		for attr, slider in self._graphics_sliders:
			if attr != self._dragging_graphics_attr:
				continue
			new_value = slider.value_from_mouse(mouse_x)
			current = float(self.settings.menu_scale)
			if abs(new_value - current) >= 0.01:
				self._set_menu_scale(new_value)
				self._persist_settings()
				width, height = engine.window_size()
				self._buttons = self._layout_buttons(width, height)
			return

	def _handle_audio_sliders(
		self,
		engine: hyperlite.Engine,
		audio: NullsightAudio | None,
		mouse_x: int,
		mouse_y: int,
		left_down: bool,
		clicked: bool,
	) -> None:
		"""Drag or click gain sliders on the audio settings screen."""
		if not self._gain_sliders:
			return

		if clicked:
			for attr, slider in self._gain_sliders:
				if slider.contains(mouse_x, mouse_y):
					self._dragging_gain_attr = attr
					break

		if not left_down:
			self._dragging_gain_attr = None

		if self._dragging_gain_attr is None:
			return

		for attr, slider in self._gain_sliders:
			if attr != self._dragging_gain_attr:
				continue
			new_value = slider.value_from_mouse(mouse_x)
			current = float(getattr(self.settings.audio, attr))
			if abs(new_value - current) >= 0.01:
				self._set_audio_gain(attr, new_value, audio)
				self._persist_settings()
				width, height = engine.window_size()
				self._buttons = self._layout_buttons(width, height)
			return

	def _persist_settings(self) -> None:
		"""Write player preferences to AppData after a menu change."""
		save_settings(self.settings)

	def update(self, engine: hyperlite.Engine, audio: NullsightAudio | None = None) -> str | None:
		"""Handle hover clicks; returns ``resume``, ``main_menu``, or None."""
		if not self.is_open:
			return None

		width, height = engine.window_size()
		self._buttons = self._layout_buttons(width, height)

		mouse_x, mouse_y = engine.mouse_pos()
		left_down = engine.mouse_button_down(hyperlite.MouseButtons.Left)
		clicked = left_down and not self._prev_left_down
		self._prev_left_down = left_down

		if self._suppress_next_click:
			self._suppress_next_click = False
			clicked = False

		if self._screen == "audio":
			self._handle_audio_sliders(engine, audio, mouse_x, mouse_y, left_down, clicked)
		elif self._screen == "graphics":
			self._handle_graphics_sliders(engine, mouse_x, mouse_y, left_down, clicked)

		if not clicked:
			return None

		for button in self._buttons:
			if not button.contains(mouse_x, mouse_y):
				continue

			if button.action == "resume":
				if self._from_title_screen:
					self.close_to_title(engine)
					return None
				return "resume"
			if button.action == "settings":
				self._go_to_screen("settings")
				return None
			if button.action == "graphics":
				self._go_to_screen("graphics")
				return None
			if button.action == "audio":
				self._go_to_screen("audio")
				return None
			if button.action == "osd":
				self._go_to_screen("osd")
				return None
			if button.action == "back_main":
				if self._from_title_screen:
					self.close_to_title(engine)
				else:
					self._go_to_screen("main")
				return None
			if button.action == "back_settings":
				self._go_to_screen("settings")
				return None
			if button.action == "toggle_vsync":
				self.settings.vsync = not self.settings.vsync
				sync_vsync(engine, self.settings)
				self._persist_settings()
				self._buttons = self._layout_buttons(width, height)
				return None
			if button.action == "toggle_double_buffer":
				self.settings.double_buffered_present = not self.settings.double_buffered_present
				sync_present_mode(engine, self.settings)
				self._persist_settings()
				self._buttons = self._layout_buttons(width, height)
				return None
			if button.action == "toggle_quiet_unfocused":
				self.settings.quiet_unfocused = not self.settings.quiet_unfocused
				self._persist_settings()
				self._buttons = self._layout_buttons(width, height)
				return None
			if button.action == "toggle_cap_fps_unfocused":
				self.settings.cap_fps_unfocused = not self.settings.cap_fps_unfocused
				self._persist_settings()
				self._buttons = self._layout_buttons(width, height)
				return None
			if button.action.startswith("toggle_osd_"):
				attr = button.action.removeprefix("toggle_osd_")
				if hasattr(self.settings.osd, attr):
					self._toggle_osd(attr)
					self._persist_settings()
					self._buttons = self._layout_buttons(width, height)
				return None
			if button.action.startswith("toggle_perf_"):
				attr = button.action.removeprefix("toggle_perf_")
				if hasattr(self.settings.perf, attr):
					self._toggle_perf(attr)
					self._persist_settings()
					self._buttons = self._layout_buttons(width, height)
				return None
			if button.action.startswith("toggle_audio_"):
				attr = button.action.removeprefix("toggle_audio_")
				if hasattr(self.settings.audio, attr):
					self._toggle_audio(attr, audio)
					self._persist_settings()
					self._buttons = self._layout_buttons(width, height)
				return None
			return button.action

		return None

	def _draw_title(self, engine: hyperlite.Engine, width: int, panel_y: int, title: str) -> None:
		"""Draw a centered panel title."""
		title_scale = self._ts(2)
		title_w = text_width(title, title_scale)
		draw_text(
			engine,
			(width - title_w) // 2,
			panel_y + self._px(24),
			title,
			PANEL_BORDER[0],
			PANEL_BORDER[1],
			PANEL_BORDER[2],
			scale=title_scale,
		)

	def _draw_settings_rows(self, engine: hyperlite.Engine, width: int, height: int) -> None:
		"""Draw labels beside VSYNC toggles (submenu rows use button labels only)."""
		_, _, panel_x, panel_y = self._panel(width, height, "settings")
		label_scale = self._ts(2)
		label_x = panel_x + self._px(24)
		row_gap = self._row_gap()
		vsync_label_y = self._label_y(panel_y)
		present_label_y = vsync_label_y + row_gap
		quiet_label_y = present_label_y + row_gap
		cap_label_y = quiet_label_y + row_gap

		draw_text(engine, label_x, vsync_label_y, "VSYNC", *MUTED_COLOR[:3], scale=label_scale)
		draw_text(engine, label_x, present_label_y, "ASYNC PRESENT", *MUTED_COLOR[:3], scale=label_scale)
		draw_text(engine, label_x, quiet_label_y, "QUIET UNFOCUS", *MUTED_COLOR[:3], scale=label_scale)
		draw_text(engine, label_x, cap_label_y, "CAP 10 FPS", *MUTED_COLOR[:3], scale=label_scale)

	def _draw_audio_rows(self, engine: hyperlite.Engine, width: int, height: int) -> None:
		"""Draw gain sliders and labels beside spatial audio toggles."""
		_, _, panel_x, panel_y = self._panel(width, height, "audio")
		label_scale = self._ts(2)
		label_x = panel_x + self._px(24)
		row_y = (
			self._first_row_y(panel_y)
			+ self._px(_AUDIO_SLIDER_ROW_H) * len(_AUDIO_GAIN_SLIDERS)
			+ self._px(8)
		)
		row_gap = self._row_gap()
		for label, attr, _min_value, _max_value in _AUDIO_GAIN_SLIDERS:
			for slider_attr, slider in self._gain_sliders:
				if slider_attr != attr:
					continue
				slider.value = float(getattr(self.settings.audio, attr))
				slider.draw(engine, label, text_scale=label_scale)
		for label, _attr in _AUDIO_TOGGLE_ROWS:
			draw_text(engine, label_x, row_y, label, *MUTED_COLOR[:3], scale=label_scale)
			row_y += row_gap

	def _draw_osd_rows(self, engine: hyperlite.Engine, width: int, height: int) -> None:
		"""Draw labels beside OSD stat toggles."""
		_, _, panel_x, panel_y = self._panel(width, height, "osd")
		label_scale = self._ts(2)
		label_x = panel_x + self._px(24)
		row_y = self._label_y(panel_y)
		row_gap = self._row_gap()
		for label, _attr in _OSD_ROWS:
			draw_text(engine, label_x, row_y, label, *MUTED_COLOR[:3], scale=label_scale)
			row_y += row_gap

	def _draw_graphics_rows(self, engine: hyperlite.Engine, width: int, height: int) -> None:
		"""Draw menu scale slider and labels beside graphics perf toggles."""
		_, _, panel_x, panel_y = self._panel(width, height, "graphics")
		label_scale = self._ts(2)
		label_x = panel_x + self._px(24)
		for _attr, slider in self._graphics_sliders:
			slider.value = float(self.settings.menu_scale)
			slider.draw(engine, "MENU SCALE", text_scale=label_scale)
		row_y = self._label_y(panel_y) + self._px(_GRAPHICS_SLIDER_ROW_H)
		row_gap = self._row_gap()
		for label, _attr in _GRAPHICS_ROWS:
			draw_text(engine, label_x, row_y, label, *MUTED_COLOR[:3], scale=label_scale)
			row_y += row_gap

	def draw(self, engine: hyperlite.Engine, width: int, height: int) -> None:
		"""Draw the dim overlay, panel, and buttons."""
		if not self.is_open:
			return

		engine.rect_fill(0, 0, width, height, *OVERLAY_COLOR)

		panel_w, panel_h, panel_x, panel_y = self._panel(width, height, self._screen)
		engine.rect_fill(panel_x, panel_y, panel_w, panel_h, *PANEL_COLOR)
		engine.rect_outline(panel_x, panel_y, panel_w, panel_h, *PANEL_BORDER)

		if self._screen == "main":
			title = "PAUSED"
		elif self._screen == "graphics":
			title = "GRAPHICS"
		elif self._screen == "audio":
			title = "AUDIO"
		elif self._screen == "osd":
			title = "OSD"
		else:
			title = "SETTINGS"
		self._draw_title(engine, width, panel_y, title)

		mouse_x, mouse_y = engine.mouse_pos()
		self._buttons = self._layout_buttons(width, height)
		if self._screen == "settings":
			self._draw_settings_rows(engine, width, height)
		elif self._screen == "graphics":
			self._draw_graphics_rows(engine, width, height)
		elif self._screen == "audio":
			self._draw_audio_rows(engine, width, height)
		elif self._screen == "osd":
			self._draw_osd_rows(engine, width, height)

		for button in self._buttons:
			button.draw(engine, button.contains(mouse_x, mouse_y))

		tooltip_key = self._update_tooltip_hover(mouse_x, mouse_y, width, height)
		if tooltip_key is not None:
			draw_setting_tooltip(
				engine,
				width,
				height,
				mouse_x,
				mouse_y,
				tooltip_key,
				self.settings.menu_scale,
			)
