"""Draw delayed hover tooltips for the pause menu."""

from __future__ import annotations

import hyperlite

from menu_layout import menu_px, menu_text_scale
from menu_tooltips import MENU_TOOLTIPS, tooltip_body_lines
from ui_text import draw_text, text_width

TOOLTIP_HOVER_DELAY_SEC = 1.5
_TOOLTIP_BG = (10, 14, 20, 248)
_TOOLTIP_BORDER = (80, 200, 130, 255)
_TOOLTIP_TEXT = (200, 255, 220, 255)
_IMPACT_COLORS = {
	"LOW": (100, 230, 140, 255),
	"MED": (255, 190, 70, 255),
	"HIGH": (255, 90, 90, 255),
}


def draw_setting_tooltip(
	engine: hyperlite.Engine,
	screen_width: int,
	screen_height: int,
	anchor_x: int,
	anchor_y: int,
	tooltip_key: str,
	menu_scale: float = 1.0,
) -> None:
	"""Render a tooltip box near the hover anchor."""
	tip = MENU_TOOLTIPS.get(tooltip_key)
	if tip is None:
		return

	lines = tooltip_body_lines(tip)
	scale = menu_text_scale(1, menu_scale)
	pad = menu_px(8, menu_scale)
	line_gap = menu_px(3, menu_scale)
	line_h = 7 * scale + line_gap
	text_w = max(text_width(line, scale) for line in lines)
	box_w = text_w + pad * 2
	box_h = pad * 2 + line_h * len(lines) - line_gap

	# Prefer below-right of the row; flip inside the screen.
	offset = menu_px(16, menu_scale)
	box_x = anchor_x + offset
	box_y = anchor_y + menu_px(8, menu_scale)
	margin = menu_px(8, menu_scale)
	if box_x + box_w > screen_width - margin:
		box_x = max(margin, anchor_x - box_w - margin)
	if box_y + box_h > screen_height - margin:
		box_y = max(margin, anchor_y - box_h - margin)

	engine.rect_fill(box_x, box_y, box_w, box_h, *_TOOLTIP_BG)
	engine.rect_outline(box_x, box_y, box_w, box_h, *_TOOLTIP_BORDER)

	text_x = box_x + pad
	text_y = box_y + pad
	for index, line in enumerate(lines):
		if index == len(lines) - 1 and line.startswith("PERF IMPACT"):
			color = _IMPACT_COLORS.get(tip.impact, _TOOLTIP_TEXT)
		else:
			color = _TOOLTIP_TEXT
		draw_text(
			engine,
			text_x,
			text_y,
			line,
			color[0],
			color[1],
			color[2],
			scale=scale,
		)
		text_y += line_h
