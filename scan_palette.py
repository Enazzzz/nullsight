"""Precomputed scan wireframe colors for fast fading."""

from draw_batch import pack_rgba

# Scan wireframe palette — green phosphor look.
SCAN_RGBA = (48, 255, 120, 255)
SCAN_PACKED = pack_rgba(*SCAN_RGBA)

# 256-step fade lookup for the scan phosphor color.
FADED_PACKED: tuple[int, ...] = tuple(
	pack_rgba(int(SCAN_RGBA[0] * fade), int(SCAN_RGBA[1] * fade), int(SCAN_RGBA[2] * fade), 255)
	for fade in (index / 255.0 for index in range(256))
)


def faded_packed(fade: float) -> int:
	"""Return a precomputed packed color for a fade strength in [0, 1]."""
	index = int(max(0.0, min(1.0, fade)) * 255.0 + 0.5)
	return FADED_PACKED[index]
