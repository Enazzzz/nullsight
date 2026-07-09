"""Deferred screen-line collection before bulk flush."""

from __future__ import annotations

import array

import hyperlite

from draw_batch import ScreenLine

# Reused buffers for ``lines_bulk_colored``.
_PLAIN_SEGMENTS = array.array("i")
_PLAIN_COLORS = array.array("I")


def _ensure_plain_capacity(line_count: int) -> None:
	"""Grow shared segment/color arrays without per-frame allocation."""
	needed_coords = line_count * 4
	if len(_PLAIN_SEGMENTS) < needed_coords:
		_PLAIN_SEGMENTS.extend([0] * (needed_coords - len(_PLAIN_SEGMENTS)))
	if len(_PLAIN_COLORS) < line_count:
		_PLAIN_COLORS.extend([0] * (line_count - len(_PLAIN_COLORS)))


def _flush_colored_lines(engine: hyperlite.Engine, lines: list[ScreenLine]) -> int:
	"""Queue 1px opaque lines via native ``lines_bulk_colored``."""
	line_count = len(lines)
	if line_count == 0:
		return 0

	_ensure_plain_capacity(line_count)
	seg = _PLAIN_SEGMENTS
	col = _PLAIN_COLORS
	for index, (x0, y0, x1, y1, packed_color) in enumerate(lines):
		base = index * 4
		seg[base] = x0
		seg[base + 1] = y0
		seg[base + 2] = x1
		seg[base + 3] = y1
		col[index] = packed_color & 0xFFFFFFFF

	seg_view = memoryview(seg)[: line_count * 4]
	col_view = memoryview(col)[:line_count]
	engine.lines_bulk_colored(seg_view, col_view, 1)
	return line_count


class ScreenLineCollector:
	"""Collect projected segments, then flush as one opaque batch."""

	def __init__(self, initial_capacity: int = 16384) -> None:
		self._lines: list[ScreenLine | None] = [None] * initial_capacity
		self._count = 0

	def clear(self) -> None:
		"""Drop collected lines for the next frame."""
		self._count = 0

	def reserve(self, minimum: int) -> None:
		"""Grow backing storage to cut list reallocations on dense frames."""
		if minimum <= len(self._lines):
			return
		self._lines.extend([None] * (minimum - len(self._lines)))

	def add_packed(self, x0: int, y0: int, x1: int, y1: int, packed_color: int) -> None:
		"""Record one projected segment before flush."""
		if x0 == x1 and y0 == y1:
			return
		index = self._count
		if index >= len(self._lines):
			self._lines.extend([None] * max(4096, len(self._lines)))
		self._lines[index] = (x0, y0, x1, y1, packed_color)
		self._count = index + 1

	def __len__(self) -> int:
		"""Number of collected segments."""
		return self._count

	@property
	def lines(self) -> list[ScreenLine]:
		"""Collected segments (read-only use during flush)."""
		return self._lines[: self._count]  # type: ignore[return-value]

	def flush(self, engine: hyperlite.Engine) -> int:
		"""Submit collected lines; return native line count queued."""
		if self._count == 0:
			return 0

		core_lines = self._lines[: self._count]  # type: ignore[arg-type]
		native = _flush_colored_lines(engine, core_lines)
		self._count = 0
		return native
