"""Minimal gameplay HUD — vitals with phosphor-green chrome."""

from __future__ import annotations

from dataclasses import dataclass

import hyperlite

from player_vitals import PlayerVitals
from ui_text import draw_text, text_width

# Shared HUD chrome — scan phosphor green borders and labels.
_HUD_BORDER = (48, 255, 120, 255)
_HUD_LABEL = (48, 255, 120, 255)

_LABEL_HEALTH = "HEALTH"
_LABEL_STAMINA = "STAMINA"
_LABEL_SCANNER = "SCANNER"

_MARGIN = 14
_BAR_W = 148
_BAR_H = 20
_ROW_GAP = 8
_LABEL_SCALE = 2
_LABEL_GAP = 8
# Inset fill area inside the green outline.
_BORDER_INSET = 1
# Pixel font height at scale 2 — used to vertically center labels on thick bars.
_LABEL_HEIGHT = 7 * _LABEL_SCALE


@dataclass(frozen=True)
class _BarPalette:
	"""Bright fill and darker drained tones for one vital bar."""

	fill: tuple[int, int, int, int]
	drain: tuple[int, int, int, int]


# Per-stat colors: current level is bright; missing capacity is a darker same hue.
_HEALTH = _BarPalette(
	fill=(228, 56, 64, 255),
	drain=(72, 14, 18, 255),
)
_STAMINA = _BarPalette(
	fill=(72, 148, 255, 255),
	drain=(18, 38, 92, 255),
)
_SCANNER = _BarPalette(
	fill=(32, 148, 72, 255),
	drain=(10, 44, 22, 255),
)


def _label_y(bar_y: int) -> int:
	"""Vertically center a label against a thick bar row."""
	return bar_y + max(0, (_BAR_H - _LABEL_HEIGHT) // 2)


def _draw_vital_bar(
	engine: hyperlite.Engine,
	x: int,
	y: int,
	width: int,
	height: int,
	ratio: float,
	palette: _BarPalette,
) -> None:
	"""Draw a bar: bright fill for current amount, darker hue for drained capacity."""
	ratio = max(0.0, min(1.0, ratio))
	inner_x = x + _BORDER_INSET
	inner_y = y + _BORDER_INSET
	inner_w = width - _BORDER_INSET * 2
	inner_h = height - _BORDER_INSET * 2

	# Full track shows the depleted portion in the darker tone.
	engine.rect_fill(inner_x, inner_y, inner_w, inner_h, *palette.drain)
	fill_w = max(0, int(round(inner_w * ratio)))
	if fill_w > 0:
		engine.rect_fill(inner_x, inner_y, fill_w, inner_h, *palette.fill)

	engine.rect_outline(x, y, width, height, *_HUD_BORDER)


def _draw_labeled_bar(
	engine: hyperlite.Engine,
	label: str,
	bar_x: int,
	bar_y: int,
	ratio: float,
	palette: _BarPalette,
) -> None:
	"""Draw label to the left of a bordered vital bar."""
	label_x = bar_x - _LABEL_GAP - text_width(label, _LABEL_SCALE)
	draw_text(
		engine,
		label_x,
		_label_y(bar_y),
		label,
		_HUD_LABEL[0],
		_HUD_LABEL[1],
		_HUD_LABEL[2],
		scale=_LABEL_SCALE,
	)
	_draw_vital_bar(engine, bar_x, bar_y, _BAR_W, _BAR_H, ratio, palette)


def draw_gameplay_hud(engine: hyperlite.Engine, vitals: PlayerVitals, width: int, height: int) -> None:
	"""Draw scanner left; health and stamina stacked on the right."""
	row_stride = _BAR_H + _ROW_GAP

	# Left — scanner integrity (darker green fill).
	scanner_y = height - _MARGIN - _BAR_H
	scanner_bar_x = _MARGIN + text_width(_LABEL_SCANNER, _LABEL_SCALE) + _LABEL_GAP
	_draw_labeled_bar(
		engine,
		_LABEL_SCANNER,
		scanner_bar_x,
		scanner_y,
		vitals.scanner_ratio,
		_SCANNER,
	)

	# Right — health (red) above stamina (blue), right-aligned.
	right_bar_x = width - _MARGIN - _BAR_W
	stamina_y = height - _MARGIN - _BAR_H
	health_y = stamina_y - row_stride

	_draw_labeled_bar(
		engine,
		_LABEL_HEALTH,
		right_bar_x,
		health_y,
		vitals.health_ratio,
		_HEALTH,
	)
	_draw_labeled_bar(
		engine,
		_LABEL_STAMINA,
		right_bar_x,
		stamina_y,
		vitals.stamina_ratio,
		_STAMINA,
	)
