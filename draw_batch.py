"""Bulk line drawing via Hyperlite ``push_commands`` (guide Path F — styled wireframe)."""

from __future__ import annotations

import struct

import hyperlite

DRAW_CMD_LINE = 2
_CMD_SIZE = 24
# DrawCommand: type(u8), line_width(u8), reserved(u16), x0,y0,x1,y1(i32), packed_color(u32)
_LINE_PACKER = struct.Struct("<BBH4iI")
_UNPACK_LINE = struct.Struct("<BBH4iI").unpack_from
_INITIAL_CAPACITY = 65536

# One queued screen segment: x0, y0, x1, y1, packed RGBA.
ScreenLine = tuple[int, int, int, int, int]


def pack_rgba(r: int, g: int, b: int, a: int = 255) -> int:
	"""Pack RGBA channels into a Hyperlite draw-command color word."""
	return (r & 0xFF) | ((g & 0xFF) << 8) | ((b & 0xFF) << 16) | ((a & 0xFF) << 24)


class LineBatch:
	"""Accumulates line segments in a reusable buffer for one native flush."""

	def __init__(self) -> None:
		self._buffer = bytearray(_INITIAL_CAPACITY * _CMD_SIZE)
		self._count = 0

	def _ensure(self, extra: int = 1) -> None:
		"""Grow the command buffer when the current frame needs more lines."""
		needed = (self._count + extra) * _CMD_SIZE
		if needed <= len(self._buffer):
			return
		growth = max(needed, len(self._buffer) * 2)
		self._buffer.extend(b"\0" * (growth - len(self._buffer)))

	def reserve(self, extra: int) -> None:
		"""Pre-grow the buffer before a large burst of ``line_packed`` calls."""
		if extra > 0:
			self._ensure(extra)

	def line_packed(
		self,
		x0: int,
		y0: int,
		x1: int,
		y1: int,
		packed_color: int,
		width: int = 1,
	) -> None:
		"""Queue one line segment with optional stroke width and packed RGBA."""
		if x0 == x1 and y0 == y1:
			return
		self._ensure()
		offset = self._count * _CMD_SIZE
		stroke = max(1, min(int(width), 255))
		_LINE_PACKER.pack_into(
			self._buffer,
			offset,
			DRAW_CMD_LINE,
			stroke,
			0,
			x0,
			y0,
			x1,
			y1,
			packed_color,
		)
		self._count += 1

	def line_packed_bulk(self, lines: list[ScreenLine]) -> None:
		"""Queue many line segments with one buffer growth check."""
		if not lines:
			return
		self._ensure(len(lines))
		pack = _LINE_PACKER.pack_into
		buffer = self._buffer
		index = self._count
		cmd = DRAW_CMD_LINE
		for x0, y0, x1, y1, packed_color in lines:
			if x0 == x1 and y0 == y1:
				continue
			pack(buffer, index * _CMD_SIZE, cmd, 1, 0, x0, y0, x1, y1, packed_color)
			index += 1
		self._count = index

	def line(
		self,
		x0: int,
		y0: int,
		x1: int,
		y1: int,
		r: int,
		g: int,
		b: int,
		a: int = 255,
		width: int = 1,
	) -> None:
		"""Queue one line segment."""
		self.line_packed(x0, y0, x1, y1, pack_rgba(r, g, b, a), width)

	def _sort_commands(self) -> None:
		"""Reorder queued lines so same width+color runs batch together at raster time."""
		count = self._count
		if count <= 1:
			return

		buf = self._buffer
		unpack = _UNPACK_LINE
		order = list(range(count))
		order.sort(key=lambda index: (unpack(buf, index * _CMD_SIZE)[1], unpack(buf, index * _CMD_SIZE)[7]))
		if order == list(range(count)):
			return

		sorted_buf = bytearray(count * _CMD_SIZE)
		pack_slice = memoryview(sorted_buf)
		for new_index, old_index in enumerate(order):
			start = old_index * _CMD_SIZE
			pack_slice[new_index * _CMD_SIZE : (new_index + 1) * _CMD_SIZE] = buf[start : start + _CMD_SIZE]
		self._buffer[: count * _CMD_SIZE] = sorted_buf

	def flush(self, engine: hyperlite.Engine, *, sort: bool = True) -> None:
		"""Submit all queued lines to the engine."""
		if self._count == 0:
			return
		if sort:
			self._sort_commands()
		byte_len = self._count * _CMD_SIZE
		engine.push_commands(memoryview(self._buffer)[:byte_len])
		self._count = 0

	def __len__(self) -> int:
		"""Number of queued line segments."""
		return self._count
