"""Regression checks for post-scan (scanned-face) live occlusion."""

from scan_occluders import (
	ScannedFace,
	ScannedFaceIndex,
	ScannedOccluderCache,
	build_scanned_occluders,
	clip_segment_scanned_occluder_intervals,
	_faces_relevant_to_segment,
)
from scan_snapshot import SnapshotSegment, live_occlusion_intervals


def _wall_face_x(x: float, z0: float, z1: float, y0: float = 0.0, y1: float = 3.0) -> ScannedFace:
	"""Axis-aligned wall patch on plane x = ``x`` spanning z and y."""
	return ScannedFace(
		axis=0,
		coord=x,
		span_a_axis=2,
		span_b_axis=1,
		min_a=z0,
		max_a=z1,
		min_b=y0,
		max_b=y1,
	)


def test_eye_between_wall_and_segment() -> None:
	"""Wall between eye and segment must be considered even when segment does not cross the plane."""
	eye = (20.0, 1.6, 10.0)
	start = (10.0, 2.0, 8.0)
	end = (10.0, 2.0, 12.0)
	wall = _wall_face_x(14.0, 0.0, 40.0)

	relevant = _faces_relevant_to_segment(eye, start, end, [wall])
	assert relevant, "interior wall between eye and segment must be relevant"

	cache = ScannedOccluderCache(eye, [wall], ScannedFaceIndex([wall]))
	visible = clip_segment_scanned_occluder_intervals(eye, start, end, [wall], cache)
	assert visible != [(0.0, 1.0)], "segment behind scanned wall should be partially or fully occluded"


def test_spatial_index_empty_falls_back_to_all_faces() -> None:
	"""An empty spatial query must still clip against all scanned faces."""
	eye = (20.0, 1.6, 10.0)
	start = (10.0, 2.0, 10.0)
	end = (10.0, 2.0, 10.0)
	wall = _wall_face_x(14.0, 0.0, 40.0)
	index = ScannedFaceIndex([wall])
	cache = ScannedOccluderCache(eye, [wall], index)

	visible = clip_segment_scanned_occluder_intervals(
		eye,
		start,
		end,
		[wall],
		cache,
		candidate_indices=[],
	)
	assert visible != [(0.0, 1.0)], "empty candidate list must fall back to full face tests"


def test_boundary_faces_vertex_bounded() -> None:
	"""Outer-room boundary hits should form a quad from scanned samples only."""
	from occlusion import BoundaryPlane

	boundaries = [
		BoundaryPlane(0, 0.0),
		BoundaryPlane(0, 40.0),
		BoundaryPlane(2, 0.0),
		BoundaryPlane(2, 40.0),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 3.0),
	]
	segments = [
		SnapshotSegment(
			(0.0, 1.0, 10.0),
			(0.0, 2.0, 10.0),
			[(0.0, 1.0)],
			hull_bounds=None,
		),
	]
	faces = build_scanned_occluders(segments, (5.0, 1.6, 10.0), boundaries, [])
	x_faces = [f for f in faces if f.axis == 0 and abs(f.coord) < 0.01]
	assert x_faces, "expected boundary face on x=0"
	face = x_faces[0]
	assert face.min_b <= 10.0 <= face.max_b, "face should cover scanned z"
	assert face.max_b - face.min_b < 5.0, "boundary face must not expand to full room depth"
	assert face.min_a <= 1.0 and face.max_a >= 2.0, "face should cover scanned y span"


def test_pillar_hull_produces_face() -> None:
	"""Solid pillar wire on the center plane must still produce a scanned occluder face."""
	bounds = ((8.0, 0.0, 3.0), (9.2, 4.0, 4.2))
	segments = [
		SnapshotSegment(
			(8.0, 0.0, 3.0),
			(8.0, 4.0, 3.0),
			[(0.0, 1.0)],
			hull_bounds=bounds,
		),
	]
	faces = build_scanned_occluders(segments, (6.0, 1.6, 3.5), [], [bounds])
	assert faces, "pillar segment should register at least one scanned face"
	face = faces[0]
	assert face.min_a <= 0.0 and face.max_a >= 4.0, "face should span scanned pillar height"


def test_live_intersection() -> None:
	"""Live occlusion must intersect with baked scan intervals."""
	eye = (20.0, 1.6, 10.0)
	start = (10.0, 2.0, 5.0)
	end = (10.0, 2.0, 15.0)
	wall = _wall_face_x(14.0, 0.0, 40.0)
	cache = ScannedOccluderCache(eye, [wall])
	baked = [(0.0, 1.0)]
	live = live_occlusion_intervals(start, end, baked, None, eye, [wall], cache)
	assert live != baked, "live pass should clip baked intervals when wall blocks sight"


def test_same_hull_segments_skip_self_occlusion() -> None:
	"""Wire on a solid must not live-occlude against that solid's own scanned faces."""
	from scan_memory import hull_key

	bounds = ((8.0, 0.0, 3.0), (9.2, 4.0, 4.2))
	segments = [
		SnapshotSegment(
			(12.0, 1.0, 6.0),
			(12.0, 2.0, 6.0),
			[(0.0, 1.0)],
			hull_bounds=bounds,
		),
	]
	faces = build_scanned_occluders(segments, (6.0, 1.6, 6.0), [], [bounds])
	assert faces, "expected scanned face from hull segment"
	eye = (10.0, 1.6, 6.0)
	visible = clip_segment_scanned_occluder_intervals(
		eye,
		segments[0].start,
		segments[0].end,
		faces,
		exclude_hull_key=hull_key(bounds),
	)
	assert visible == [(0.0, 1.0)], "same-hull wire must stay fully visible"


def test_one_sided_face_does_not_block_from_behind() -> None:
	"""Scanned faces only block sight from the front — not when both eye and target are behind."""
	wall = _wall_face_x(12.0, 0.0, 12.0)
	eye = (14.0, 1.6, 6.0)
	target = (16.0, 1.6, 6.0)
	cache = ScannedOccluderCache(eye, [wall])
	visible = clip_segment_scanned_occluder_intervals(eye, target, target, [wall], cache)
	assert visible == [(0.0, 1.0)], "same-side geometry should not be blocked by the wall face"


def main() -> None:
	test_eye_between_wall_and_segment()
	test_spatial_index_empty_falls_back_to_all_faces()
	test_boundary_faces_vertex_bounded()
	test_pillar_hull_produces_face()
	test_same_hull_segments_skip_self_occlusion()
	test_one_sided_face_does_not_block_from_behind()
	test_live_intersection()
	print("live occlusion checks passed")


if __name__ == "__main__":
	main()
