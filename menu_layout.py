"""Shared menu layout scaling for title and pause UI."""

DEFAULT_MENU_SCALE = 1.0
MENU_SCALE_MIN = 0.75
MENU_SCALE_MAX = 2.0


def menu_px(value: float, menu_scale: float) -> int:
	"""Scale a pixel dimension for the current menu scale setting."""
	return max(1, round(value * menu_scale))


def menu_text_scale(base: int, menu_scale: float) -> int:
	"""Scale bitmap text; keeps at least one pixel per glyph unit."""
	return max(1, round(base * menu_scale))
