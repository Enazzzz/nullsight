"""Minimal bitmap glyphs for UI labels (Hyperlite has no font renderer)."""

import hyperlite

# Each glyph is a 5-wide × 7-tall bitmap row string ('1' = lit pixel).
_GLYPHS: dict[str, tuple[str, ...]] = {
	"C": (
		"01110",
		"10001",
		"10000",
		"10000",
		"10000",
		"10001",
		"01110",
	),
	"L": (
		"10000",
		"10000",
		"10000",
		"10000",
		"10000",
		"10000",
		"11111",
	),
	"O": (
		"01110",
		"10001",
		"10001",
		"10001",
		"10001",
		"10001",
		"01110",
	),
	"S": (
		"01111",
		"10000",
		"10000",
		"01110",
		"00001",
		"00001",
		"11110",
	),
	"E": (
		"11111",
		"10000",
		"10000",
		"11110",
		"10000",
		"10000",
		"11111",
	),
	"R": (
		"11110",
		"10001",
		"10001",
		"11110",
		"10100",
		"10010",
		"10001",
	),
	"U": (
		"10001",
		"10001",
		"10001",
		"10001",
		"10001",
		"10001",
		"01110",
	),
	"M": (
		"10001",
		"11011",
		"10101",
		"10101",
		"10001",
		"10001",
		"10001",
	),
	"P": (
		"11110",
		"10001",
		"10001",
		"11110",
		"10000",
		"10000",
		"10000",
	),
	"A": (
		"01110",
		"10001",
		"10001",
		"11111",
		"10001",
		"10001",
		"10001",
	),
	"D": (
		"11110",
		"10001",
		"10001",
		"10001",
		"10001",
		"10001",
		"11110",
	),
	"F": (
		"11111",
		"10000",
		"10000",
		"11110",
		"10000",
		"10000",
		"10000",
	),
	"I": (
		"11111",
		"00100",
		"00100",
		"00100",
		"00100",
		"00100",
		"11111",
	),
	"N": (
		"10001",
		"11001",
		"10101",
		"10011",
		"10001",
		"10001",
		"10001",
	),
	"G": (
		"01110",
		"10001",
		"10000",
		"10111",
		"10001",
		"10001",
		"01110",
	),
	"H": (
		"10001",
		"10001",
		"10001",
		"11111",
		"10001",
		"10001",
		"10001",
	),
	"T": (
		"11111",
		"00100",
		"00100",
		"00100",
		"00100",
		"00100",
		"00100",
	),
	"B": (
		"11110",
		"10001",
		"10001",
		"11110",
		"10001",
		"10001",
		"11110",
	),
	"K": (
		"10001",
		"10010",
		"10100",
		"11000",
		"10100",
		"10010",
		"10001",
	),
	"V": (
		"10001",
		"10001",
		"10001",
		"01010",
		"01010",
		"00100",
		"00100",
	),
	"Y": (
		"10001",
		"10001",
		"10001",
		"01110",
		"00100",
		"00100",
		"00100",
	),
	"Q": (
		"01110",
		"10001",
		"10001",
		"10001",
		"10101",
		"10010",
		"01101",
	),
	".": (
		"00000",
		"00000",
		"00000",
		"00000",
		"00000",
		"00100",
		"00100",
	),
	"0": (
		"01110",
		"10001",
		"10011",
		"10101",
		"11001",
		"10001",
		"01110",
	),
	"1": (
		"00100",
		"01100",
		"00100",
		"00100",
		"00100",
		"00100",
		"01110",
	),
	"2": (
		"01110",
		"10001",
		"00001",
		"00110",
		"01000",
		"10000",
		"11111",
	),
	"3": (
		"11110",
		"00001",
		"00001",
		"01110",
		"00001",
		"00001",
		"11110",
	),
	"4": (
		"00010",
		"00110",
		"01010",
		"10010",
		"11111",
		"00010",
		"00010",
	),
	"5": (
		"11111",
		"10000",
		"10000",
		"11110",
		"00001",
		"00001",
		"11110",
	),
	"6": (
		"01110",
		"10000",
		"10000",
		"11110",
		"10001",
		"10001",
		"01110",
	),
	"7": (
		"11111",
		"00001",
		"00010",
		"00100",
		"01000",
		"01000",
		"01000",
	),
	"8": (
		"01110",
		"10001",
		"10001",
		"01110",
		"10001",
		"10001",
		"01110",
	),
	"9": (
		"01110",
		"10001",
		"10001",
		"01111",
		"00001",
		"00001",
		"01110",
	),
	"J": (
		"00111",
		"00010",
		"00010",
		"00010",
		"00010",
		"10010",
		"01100",
	),
	"W": (
		"10001",
		"10001",
		"10001",
		"10001",
		"10101",
		"11011",
		"10001",
	),
	"X": (
		"10001",
		"10001",
		"01010",
		"00100",
		"01010",
		"10001",
		"10001",
	),
	"Z": (
		"11111",
		"00001",
		"00010",
		"00100",
		"01000",
		"10000",
		"11111",
	),
	"a": (
		"00000",
		"00000",
		"01110",
		"00001",
		"01111",
		"10001",
		"01111",
	),
	"b": (
		"10000",
		"10000",
		"11110",
		"10001",
		"10001",
		"10001",
		"11110",
	),
	"c": (
		"00000",
		"00000",
		"01110",
		"10000",
		"10000",
		"10001",
		"01110",
	),
	"d": (
		"00001",
		"00001",
		"01111",
		"10001",
		"10001",
		"10001",
		"01111",
	),
	"e": (
		"00000",
		"00000",
		"01110",
		"10001",
		"11111",
		"10000",
		"01110",
	),
	"f": (
		"00110",
		"01001",
		"01000",
		"11100",
		"01000",
		"01000",
		"01000",
	),
	"g": (
		"00000",
		"01111",
		"10001",
		"10001",
		"01111",
		"00001",
		"01110",
	),
	"h": (
		"10000",
		"10000",
		"10110",
		"11001",
		"10001",
		"10001",
		"10001",
	),
	"i": (
		"00000",
		"00100",
		"00000",
		"01100",
		"00100",
		"00100",
		"01110",
	),
	"j": (
		"00010",
		"00000",
		"00110",
		"00010",
		"00010",
		"10010",
		"01100",
	),
	"k": (
		"10000",
		"10000",
		"10010",
		"10100",
		"11000",
		"10100",
		"10010",
	),
	"l": (
		"01100",
		"00100",
		"00100",
		"00100",
		"00100",
		"00100",
		"01110",
	),
	"m": (
		"00000",
		"00000",
		"11010",
		"10101",
		"10101",
		"10001",
		"10001",
	),
	"n": (
		"00000",
		"00000",
		"10110",
		"11001",
		"10001",
		"10001",
		"10001",
	),
	"o": (
		"00000",
		"00000",
		"01110",
		"10001",
		"10001",
		"10001",
		"01110",
	),
	"p": (
		"00000",
		"00000",
		"11110",
		"10001",
		"10001",
		"11110",
		"10000",
	),
	"q": (
		"00000",
		"00000",
		"01111",
		"10001",
		"10001",
		"01111",
		"00001",
	),
	"r": (
		"00000",
		"00000",
		"10110",
		"11001",
		"10000",
		"10000",
		"10000",
	),
	"s": (
		"00000",
		"00000",
		"01111",
		"10000",
		"01110",
		"00001",
		"11110",
	),
	"t": (
		"00000",
		"01000",
		"11110",
		"01000",
		"01000",
		"01001",
		"00110",
	),
	"u": (
		"00000",
		"00000",
		"10001",
		"10001",
		"10001",
		"10001",
		"01110",
	),
	"v": (
		"00000",
		"00000",
		"10001",
		"10001",
		"10001",
		"01010",
		"00100",
	),
	"w": (
		"00000",
		"00000",
		"10001",
		"10001",
		"10101",
		"10101",
		"01010",
	),
	"x": (
		"00000",
		"00000",
		"10001",
		"01010",
		"00100",
		"01010",
		"10001",
	),
	"y": (
		"00000",
		"10001",
		"10001",
		"10001",
		"01111",
		"00001",
		"01110",
	),
	"z": (
		"00000",
		"00000",
		"11111",
		"00010",
		"00100",
		"01000",
		"11111",
	),
	" ": (
		"00000",
		"00000",
		"00000",
		"00000",
		"00000",
		"00000",
		"00000",
	),
	"-": (
		"00000",
		"00000",
		"00000",
		"11111",
		"00000",
		"00000",
		"00000",
	),
	"/": (
		"00001",
		"00010",
		"00010",
		"00100",
		"01000",
		"01000",
		"10000",
	),
	":": (
		"00000",
		"00100",
		"00000",
		"00000",
		"00000",
		"00100",
		"00000",
	),
	"?": (
		"01110",
		"10001",
		"00001",
		"00110",
		"00100",
		"00000",
		"00100",
	),
	"%": (
		"10001",
		"10010",
		"00100",
		"01000",
		"01000",
		"10001",
		"00000",
	),
	",": (
		"00000",
		"00000",
		"00000",
		"00000",
		"00100",
		"00100",
		"01000",
	),
	"'": (
		"00100",
		"00100",
		"01000",
		"00000",
		"00000",
		"00000",
		"00000",
	),
	"\"": (
		"01010",
		"01010",
		"00000",
		"00000",
		"00000",
		"00000",
		"00000",
	),
	"(": (
		"00010",
		"00100",
		"01000",
		"01000",
		"01000",
		"00100",
		"00010",
	),
	")": (
		"01000",
		"00100",
		"00010",
		"00010",
		"00010",
		"00100",
		"01000",
	),
	"+": (
		"00000",
		"00100",
		"00100",
		"11111",
		"00100",
		"00100",
		"00000",
	),
	"=": (
		"00000",
		"00000",
		"11111",
		"00000",
		"11111",
		"00000",
		"00000",
	),
	"_": (
		"00000",
		"00000",
		"00000",
		"00000",
		"00000",
		"00000",
		"11111",
	),
	"&": (
		"01100",
		"10010",
		"10100",
		"01000",
		"10101",
		"10010",
		"01101",
	),
}


def _lookup_glyph(char: str) -> tuple[str, ...] | None:
	"""Resolve a character to a 5×7 bitmap; uppercase fallback for unknown symbols."""
	glyph = _GLYPHS.get(char)
	if glyph is not None:
		return glyph
	if char.isalpha():
		return _GLYPHS.get(char.upper())
	return None


def _char_advance(char: str, scale: int) -> int:
	"""Horizontal advance for one character at the given scale."""
	if char == " ":
		return 3 * scale
	glyph = _lookup_glyph(char)
	if glyph is None:
		return 4 * scale
	return (len(glyph[0]) + 1) * scale


def _plot_glyph_pixels(
	buf: bytearray,
	buf_w: int,
	buf_h: int,
	origin_x: int,
	origin_y: int,
	char: str,
	scale: int,
	r: int,
	g: int,
	b: int,
	a: int,
) -> int:
	"""Stamp one glyph into an RGBA buffer; returns horizontal advance in pixels."""
	glyph = _lookup_glyph(char)
	if glyph is None:
		return _char_advance(char, scale)

	for row_index, row in enumerate(glyph):
		for col_index, cell in enumerate(row):
			if cell != "1":
				continue
			px = origin_x + col_index * scale
			py = origin_y + row_index * scale
			for sy in range(scale):
				for sx in range(scale):
					x = px + sx
					y = py + sy
					if x < 0 or y < 0 or x >= buf_w or y >= buf_h:
						continue
					offset = (y * buf_w + x) * 4
					buf[offset] = r
					buf[offset + 1] = g
					buf[offset + 2] = b
					buf[offset + 3] = a

	return _char_advance(char, scale)


def rasterize_glyph(
	char: str,
	*,
	scale: int = 2,
	fg_rgb: tuple[int, int, int] = (48, 255, 120),
	alpha: int = 255,
) -> tuple[bytes, int, int]:
	"""Rasterize a single glyph into a tight RGBA buffer for ``blit_rgba``."""
	glyph = _lookup_glyph(char)
	if glyph is None:
		return b"", 0, 0

	width = len(glyph[0]) * scale
	height = len(glyph) * scale
	buf = bytearray(width * height * 4)
	_plot_glyph_pixels(
		buf,
		width,
		height,
		0,
		0,
		char,
		scale,
		fg_rgb[0],
		fg_rgb[1],
		fg_rgb[2],
		alpha,
	)
	return bytes(buf), width, height


def rasterize_text_block(
	lines: list[str],
	*,
	scale: int = 2,
	fg_rgb: tuple[int, int, int] = (48, 255, 120),
	shadow_rgb: tuple[int, int, int] = (0, 0, 0),
	shadow_alpha: int = 180,
	shadow_dx: int = 1,
	shadow_dy: int = 1,
	line_height: int | None = None,
	padding: int = 2,
) -> tuple[bytes, int, int]:
	"""Rasterize labeled lines into a tight RGBA buffer for ``blit_rgba``."""
	if not lines:
		return b"", 0, 0

	row_h = line_height if line_height is not None else 7 * scale + 4
	text_w = max(text_width(line, scale=scale) for line in lines)
	width = text_w + padding * 2 + shadow_dx
	height = padding * 2 + len(lines) * row_h + shadow_dy
	buf = bytearray(width * height * 4)

	y = padding
	for line in lines:
		shadow_y = y + shadow_dy
		cursor_x = padding
		for char in line:
			cursor_x += _plot_glyph_pixels(
				buf, width, height, cursor_x, shadow_y, char, scale,
				shadow_rgb[0], shadow_rgb[1], shadow_rgb[2], shadow_alpha,
			)
		cursor_x = padding
		for char in line:
			cursor_x += _plot_glyph_pixels(
				buf, width, height, cursor_x, y, char, scale,
				fg_rgb[0], fg_rgb[1], fg_rgb[2], 255,
			)
		y += row_h

	return bytes(buf), width, height


def draw_text(
	engine: hyperlite.Engine,
	x: int,
	y: int,
	text: str,
	r: int,
	g: int,
	b: int,
	scale: int = 2,
) -> int:
	"""Draw label text (upper and lower case); returns total pixel width drawn."""
	cursor_x = x
	for char in text:
		glyph = _lookup_glyph(char)
		if glyph is None:
			cursor_x += _char_advance(char, scale)
			continue

		for row_index, row in enumerate(glyph):
			for col_index, cell in enumerate(row):
				if cell != "1":
					continue
				px = cursor_x + col_index * scale
				py = y + row_index * scale
				engine.rect_fill(px, py, scale, scale, r, g, b, 255)

		cursor_x += _char_advance(char, scale)

	return cursor_x - x


def text_width(text: str, scale: int = 2) -> int:
	"""Return pixel width of a label at the given scale."""
	width = 0
	for char in text:
		width += _char_advance(char, scale)
	return max(0, width - scale)
