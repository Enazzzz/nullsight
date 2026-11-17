"""Persistent scanned geometry registry — deduplicated across overlapping scans."""

from dataclasses import dataclass, field

from math3d import Interval, Vec3, vertex_key
from occlusion import Aabb
from scan_occluders import ScannedFace, ScannedFaceIndex

# Canonical identity for one world edge (endpoints + optional hull).
SegmentKey = tuple[tuple[int, int, int], tuple[int, int, int], tuple | None]


def _hull_key(bounds: Aabb | None) -> tuple | None:
	"""Hashable hull identity for segment keys."""
	if bounds is None:
		return None
	box_min, box_max = bounds
	return (
		(round(box_min[0], 4), round(box_min[1], 4), round(box_min[2], 4)),
		(round(box_max[0], 4), round(box_max[1], 4), round(box_max[2], 4)),
	)


def hull_key(bounds: Aabb | None) -> tuple | None:
	"""Hashable hull identity for segment keys."""
	return _hull_key(bounds)


def segment_key(start: Vec3, end: Vec3, hull_bounds: Aabb | None = None) -> SegmentKey:
	"""Stable key for matching the same world edge across scans."""
	key_a = vertex_key(start)
	key_b = vertex_key(end)
	if key_a > key_b:
		key_a, key_b = key_b, key_a
	return key_a, key_b, _hull_key(hull_bounds)


@dataclass
class ScannedSegment:
	"""One deduplicated edge interval captured by the scan device."""

	start: Vec3
	end: Vec3
	intervals: list[Interval]
	hull_bounds: Aabb | None
	reveal_origin: Vec3
	start_time: float
	scanned: bool = True
	wave_complete: bool = False
	# Absolute world time when fade finishes; set by ScanDevice on merge/re-arm.
	expire_time: float = 0.0


@dataclass
class ScanMemory:
	"""All active scanned geometry and merged live-occlusion faces."""

	segments: list[ScannedSegment] = field(default_factory=list)
	segment_index: dict[SegmentKey, int] = field(default_factory=dict)
	scanned_faces: list[ScannedFace] = field(default_factory=list)
	face_index: ScannedFaceIndex | None = None
