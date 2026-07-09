"""Manual scanner repair — blind screen, invisible buttons, proximity tone."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import hyperlite

from keys import ESCAPE, R
from ui_text import draw_text, text_width

if TYPE_CHECKING:
	from nullsight_audio import NullsightAudio
	from player_vitals import PlayerVitals

# Blank repair screen.
_BLANK = (0, 0, 0, 255)
# Invisible hit targets — not drawn.
_BUTTON_COUNT = 5
_BUTTON_W = 56
_BUTTON_H = 44
_BUTTON_MARGIN = 24
# Proximity curve applied in NullsightAudio when crossfading bins.
_COUNTER_SCALE = 2
_COUNTER_COLOR = (48, 255, 120)
_COUNTER_Y = 18


@dataclass
class _HiddenButton:
	"""One invisible repair target on the blank screen."""

	x: int
	y: int
	width: int
	height: int

	def center(self) -> tuple[float, float]:
		"""Return the button center in screen pixels."""
		return (self.x + self.width * 0.5, self.y + self.height * 0.5)

	def contains(self, mouse_x: int, mouse_y: int) -> bool:
		"""Return True when the cursor is inside this target."""
		return (
			self.x <= mouse_x < self.x + self.width
			and self.y <= mouse_y < self.y + self.height
		)


@dataclass
class ScannerRepairMode:
	"""Blind five-button repair overlay toggled with R."""

	is_open: bool = False
	_buttons: list[_HiddenButton] = field(default_factory=list)
	_hits_remaining: int = _BUTTON_COUNT
	_prev_left_down: bool = False
	_prev_r_down: bool = False
	_rng: random.Random = field(default_factory=random.Random)

	def reset(self, audio: NullsightAudio | None = None) -> None:
		"""Close repair mode and silence probe audio."""
		if audio is not None:
			audio.end_repair_mode()
		self.is_open = False
		self._buttons.clear()
		self._hits_remaining = _BUTTON_COUNT
		self._prev_left_down = False

	def can_open(self, vitals: PlayerVitals) -> bool:
		"""Return True when manual repair is available."""
		return vitals.needs_scanner_repair()

	def open(self, engine: hyperlite.Engine, width: int, height: int) -> None:
		"""Enter blind repair — release the cursor for invisible button clicks."""
		self.is_open = True
		self._hits_remaining = _BUTTON_COUNT
		self._prev_left_down = False
		self._rng.seed(width * 7919 + height * 104729)
		self._layout_buttons(width, height)
		engine.set_mouse_captured(False)

	def close(self, engine: hyperlite.Engine, audio: NullsightAudio | None = None) -> None:
		"""Exit repair and restore first-person mouse capture."""
		self.reset(audio)
		engine.set_mouse_captured(True)

	def _layout_buttons(self, width: int, height: int) -> None:
		"""Place five non-overlapping invisible targets at random screen positions."""
		self._buttons.clear()
		attempts = 0
		while len(self._buttons) < _BUTTON_COUNT and attempts < 400:
			attempts += 1
			x = self._rng.randint(_BUTTON_MARGIN, max(_BUTTON_MARGIN, width - _BUTTON_W - _BUTTON_MARGIN))
			y = self._rng.randint(_BUTTON_MARGIN, max(_BUTTON_MARGIN, height - _BUTTON_H - _BUTTON_MARGIN))
			candidate = _HiddenButton(x, y, _BUTTON_W, _BUTTON_H)
			if any(self._boxes_overlap(candidate, other) for other in self._buttons):
				continue
			self._buttons.append(candidate)

		while len(self._buttons) < _BUTTON_COUNT:
			x = self._rng.randint(_BUTTON_MARGIN, max(_BUTTON_MARGIN, width - _BUTTON_W - _BUTTON_MARGIN))
			y = self._rng.randint(_BUTTON_MARGIN, max(_BUTTON_MARGIN, height - _BUTTON_H - _BUTTON_MARGIN))
			self._buttons.append(_HiddenButton(x, y, _BUTTON_W, _BUTTON_H))

	def _relocate_button(self, index: int, width: int, height: int) -> None:
		"""Move one button to a new random position after a successful hit."""
		for _ in range(120):
			x = self._rng.randint(_BUTTON_MARGIN, max(_BUTTON_MARGIN, width - _BUTTON_W - _BUTTON_MARGIN))
			y = self._rng.randint(_BUTTON_MARGIN, max(_BUTTON_MARGIN, height - _BUTTON_H - _BUTTON_MARGIN))
			candidate = _HiddenButton(x, y, _BUTTON_W, _BUTTON_H)
			others = [btn for idx, btn in enumerate(self._buttons) if idx != index]
			if any(self._boxes_overlap(candidate, other) for other in others):
				continue
			self._buttons[index] = candidate
			return
		self._buttons[index] = _HiddenButton(
			self._rng.randint(_BUTTON_MARGIN, max(_BUTTON_MARGIN, width - _BUTTON_W - _BUTTON_MARGIN)),
			self._rng.randint(_BUTTON_MARGIN, max(_BUTTON_MARGIN, height - _BUTTON_H - _BUTTON_MARGIN)),
			_BUTTON_W,
			_BUTTON_H,
		)

	@staticmethod
	def _boxes_overlap(a: _HiddenButton, b: _HiddenButton) -> bool:
		"""Return True when two invisible targets intersect."""
		return not (
			a.x + a.width <= b.x
			or b.x + b.width <= a.x
			or a.y + a.height <= b.y
			or b.y + b.height <= a.y
		)

	def _proximity_score(self, mouse_x: int, mouse_y: int, width: int, height: int) -> float:
		"""Return 0–1 proximity to the nearest hidden button (1 = on target)."""
		if not self._buttons:
			return 0.0

		min_dist = min(
			math.hypot(mouse_x - cx, mouse_y - cy)
			for cx, cy in (button.center() for button in self._buttons)
		)
		max_dist = math.hypot(width, height) * 0.34
		raw = max(0.0, min(1.0, 1.0 - min_dist / max(max_dist, 1.0)))
		return raw

	def _repair_amount(self, vitals: PlayerVitals) -> float:
		"""Integrity restored by the next successful hidden-button hit."""
		missing = vitals.max_scanner - vitals.scanner
		if missing <= 0.0 or self._hits_remaining <= 0:
			return 0.0
		return missing / self._hits_remaining

	def consume_open_pressed(self, engine: hyperlite.Engine, vitals: PlayerVitals) -> bool:
		"""Return True when R was pressed this frame and repair may open."""
		r_down = engine.key_down(R)
		pressed = r_down and not self._prev_r_down
		self._prev_r_down = r_down
		return pressed and not self.is_open and self.can_open(vitals)

	def update(
		self,
		engine: hyperlite.Engine,
		vitals: PlayerVitals,
		audio: NullsightAudio | None,
		width: int,
		height: int,
	) -> bool:
		"""Handle repair input and audio; return True when repair finished."""
		if not self.is_open:
			return False

		if engine.key_down(ESCAPE):
			self.close(engine, audio)
			return False

		mouse_x, mouse_y = engine.mouse_pos()
		if audio is not None:
			audio.update_repair_proximity(self._proximity_score(mouse_x, mouse_y, width, height))

		left_down = engine.mouse_button_down(hyperlite.MouseButtons.Left)
		clicked = left_down and not self._prev_left_down
		self._prev_left_down = left_down

		if clicked:
			for index, button in enumerate(self._buttons):
				if not button.contains(mouse_x, mouse_y):
					continue
				if audio is not None:
					audio.play_repair_clunk()
				amount = self._repair_amount(vitals)
				vitals.repair_scanner(amount)
				self._hits_remaining = max(0, self._hits_remaining - 1)
				if vitals.scanner >= vitals.max_scanner - 1e-4 or self._hits_remaining <= 0:
					self.close(engine, audio)
					return True
				self._relocate_button(index, width, height)
				break

		if not vitals.needs_scanner_repair():
			self.close(engine, audio)
			return True

		return False

	def on_open(self, audio: NullsightAudio | None) -> None:
		"""Duck world audio and start the repair proximity drone."""
		if audio is not None:
			audio.begin_repair_mode()

	def draw(self, engine: hyperlite.Engine, width: int, height: int) -> None:
		"""Draw the blank repair screen and remaining-hit counter."""
		if not self.is_open:
			return
		engine.rect_fill(0, 0, width, height, *_BLANK)

		label = f"{self._hits_remaining} LEFT"
		label_w = text_width(label, _COUNTER_SCALE)
		draw_text(
			engine,
			(width - label_w) // 2,
			_COUNTER_Y,
			label,
			_COUNTER_COLOR[0],
			_COUNTER_COLOR[1],
			_COUNTER_COLOR[2],
			scale=_COUNTER_SCALE,
		)
