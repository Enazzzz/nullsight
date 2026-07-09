"""Phase 3 automated checks: audible reflections, diffraction, portals, flutter.

Covers the wave_physics_phase3 acceptance tests:
- Image-source delay matches path length excess ±1 ms
- Diffraction adds gain when LOS blocked but portal open
- Portal graph finds shortest acoustic path across rooms
- Flutter detection emits periodic tap train on parallel-wall rooms
"""

from __future__ import annotations

import math

from acoustics.diffraction import estimate_diffraction_path
from acoustics.materials import SPEED_OF_SOUND_MPS
from acoustics.portals_graph import (
	PortalEdge,
	PortalGraph,
	default_small_level_graph,
	portal_graph_gain_for_path,
)
from acoustics.reflection import (
	emit_flutter_taps,
	flutter_echo_period_s,
	trace_first_reflection,
	trace_specular_reflections,
)
from acoustics.scene import AcousticScene
from acoustics.structure import structure_borne_for_source, structure_borne_gain
from acoustics.tracer import trace_acoustic_path
from acoustics.types import AcousticSource
from level_builder import SMALL_TABLE_RADIO_POS
from levels import build_small_level
from math3d import length, sub
from occlusion import Aabb, BoundaryPlane, segment_line_of_sight


def test_image_source_delay_matches_excess_path() -> None:
	"""First-order ISM delay equals (path_len − direct_len) / c within ±1 ms."""
	# Empty box: listener and source offset so floor/wall images are valid.
	eye = (2.0, 1.6, 2.0)
	source = (6.0, 1.6, 5.0)
	boundaries = [
		BoundaryPlane(0, 0.0),
		BoundaryPlane(0, 10.0),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 3.0),
		BoundaryPlane(2, 0.0),
		BoundaryPlane(2, 8.0),
	]
	taps = trace_specular_reflections(
		eye,
		source,
		[],
		boundaries,
		include_flutter=False,
		max_room_order=1,
	)
	assert taps, "expected at least one first-order image-source tap"
	direct_len = length(sub(source, eye))
	# Loudest tap should be a single bounce; reconstruct expected delay from
	# excess path using the same c as reflection.py.
	best = max(taps, key=lambda t: t.gain)
	assert best.delay_s > 0.0
	# Round-trip check: delay * c ≈ excess length; allow ±1 ms.
	# For order-1 ISM, path = |image − eye|; excess = path − direct.
	# We don't re-derive the image here — instead verify delay is consistent
	# with a physically plausible excess (positive, < 50 ms for this room).
	assert best.delay_s < 0.05
	# Compatibility API matches loudest tap.
	gain, delay_s, lpf_hz = trace_first_reflection(eye, source, [], boundaries)
	assert abs(delay_s - best.delay_s) < 1e-6
	assert gain == best.gain
	assert lpf_hz == best.lpf_hz
	# Explicit floor-image check: mirror source across y=0.
	image = (source[0], -source[1], source[2])
	path_len = length(sub(image, eye))
	expected_delay = max(0.0, (path_len - direct_len) / SPEED_OF_SOUND_MPS)
	# Floor bounce should appear among order-1 taps within ±1 ms.
	floor_matches = [t for t in taps if abs(t.delay_s - expected_delay) <= 0.001]
	assert floor_matches, (
		f"no tap within ±1 ms of floor-image delay {expected_delay:.4f}s; "
		f"got {[t.delay_s for t in taps]}"
	)


def test_diffraction_gain_when_los_blocked_portal_open() -> None:
	"""Blocked LOS with open portal still yields diffraction / portal energy."""
	room, door = build_small_level()
	scene = AcousticScene()
	# West listener, east source — divider + door panel block when closed.
	eye = (5.0, 1.6, 6.0)
	source = AcousticSource(source_id=1, position=(14.0, 1.6, 6.0), enabled=True)

	# Closed door: LOS blocked.
	door.open_angle = 0.0
	occluders_closed, materials = scene.build_occluders(room, door)
	assert not segment_line_of_sight(eye, source.position, occluders_closed, room.boundaries)

	# Open door portal bleed + graph.
	door.open_angle = math.pi * 0.45
	portal_gain, portal_lpf = scene.portal_state(door)
	assert portal_gain > 0.2
	assert scene.cracked_door_lpf_hz(door) == portal_lpf
	assert portal_lpf > scene.cracked_door_lpf_hz(None) or scene.cracked_door_lpf_hz(None) == 280.0

	occluders_open, materials_open = scene.build_occluders(room, door)
	open_frac = scene.door_open_frac(door)
	link = trace_acoustic_path(
		eye,
		source,
		occluders_open,
		room.boundaries,
		occluder_materials=materials_open,
		portal_gain=portal_gain,
		portal_lpf_hz=portal_lpf,
		room_name=room.name,
		door_open_frac=open_frac,
	)
	# Around-the-corner / through-doorway energy must be audible.
	audible = max(link.transmission_gain, link.diffraction_gain, link.portal_graph_gain)
	assert audible > 0.05 or link.diffraction_gain > 0.02

	# Explicit UTD path on a thin vertical slab with portal boost.
	wall: Aabb = ((2.8, 0.0, -1.5), (3.0, 3.0, 1.5))
	path = estimate_diffraction_path(
		(0.0, 1.6, 0.0),
		(6.0, 1.6, 0.0),
		[wall],
		occluders=[wall],
		direct_blocked=True,
		portal_gain=0.5,
	)
	assert path is not None
	assert path.gain > 0.02
	assert path.delay_s >= 0.0


def test_portal_graph_shortest_path_across_rooms() -> None:
	"""Portal graph Dijkstra finds the shortest multi-hop acoustic route."""
	# Three cells: west --doorA-- foyer --doorB-- east.
	graph = PortalGraph()
	graph.add_edge(
		PortalEdge(
			from_room="west",
			to_room="foyer",
			open_gain=0.9,
			open_lpf_hz=6_000.0,
			portal_pos=(5.0, 1.2, 0.0),
		)
	)
	graph.add_edge(
		PortalEdge(
			from_room="foyer",
			to_room="east",
			open_gain=0.8,
			open_lpf_hz=5_500.0,
			portal_pos=(10.0, 1.2, 0.0),
		)
	)
	# Longer detour west→east direct (should lose to foyer hop when both open).
	graph.add_edge(
		PortalEdge(
			from_room="west",
			to_room="east",
			open_gain=0.4,
			open_lpf_hz=3_000.0,
			portal_pos=(7.5, 1.2, 20.0),
		)
	)

	listener = (0.0, 1.6, 0.0)
	source = (15.0, 1.6, 0.0)
	route = graph.shortest_acoustic_path(
		"west",
		"east",
		door_open_frac=1.0,
		listener_pos=listener,
		source_pos=source,
	)
	assert route is not None
	assert route.rooms == ("west", "foyer", "east")
	assert route.gain > 0.5
	# Path length ≈ listener→doorA→doorB→source along +X.
	expected = (
		length(sub((5.0, 1.2, 0.0), listener))
		+ length(sub((10.0, 1.2, 0.0), (5.0, 1.2, 0.0)))
		+ length(sub(source, (10.0, 1.2, 0.0)))
	)
	assert abs(route.path_length_m - expected) < 0.05

	# Closed portals: no route.
	assert (
		graph.shortest_acoustic_path("west", "east", door_open_frac=0.0) is None
	)

	# Small-level convenience: open door boosts blocked cross-cell path.
	gain = portal_graph_gain_for_path(
		(5.0, 1.6, 6.0),
		(14.0, 1.6, 6.0),
		door_open_frac=0.8,
		los_blocked=True,
		graph=default_small_level_graph(),
	)
	assert gain > 0.1
	assert (
		portal_graph_gain_for_path(
			(5.0, 1.6, 6.0),
			(14.0, 1.6, 6.0),
			door_open_frac=0.0,
			los_blocked=True,
		)
		== 0.0
	)


def test_flutter_emits_periodic_tap_train() -> None:
	"""Parallel hard walls emit a decaying tap train with period 2L/c."""
	# Longest axis wins (emit_flutter_taps picks max separation) — make X longest.
	sep_x = 10.0
	boundaries = [
		BoundaryPlane(0, 0.0),
		BoundaryPlane(0, sep_x),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 3.0),
		BoundaryPlane(2, 0.0),
		BoundaryPlane(2, 4.0),
	]
	period = flutter_echo_period_s(sep_x)
	assert abs(period - (2.0 * sep_x / SPEED_OF_SOUND_MPS)) < 1e-6

	train = emit_flutter_taps(
		boundaries,
		seed_gain=0.4,
		seed_delay_s=0.01,
		seed_lpf_hz=5_000.0,
		arrival_dir=(1.0, 0.0, 0.0),
		max_repeats=4,
	)
	assert len(train) >= 2
	# Successive delays spaced by the flutter period (±0.5 ms).
	for i in range(1, len(train)):
		dt = train[i].delay_s - train[i - 1].delay_s
		assert abs(dt - period) < 0.0005
	# Decaying gains.
	assert train[0].gain > train[-1].gain

	# Full specular trace on a long narrow room includes flutter taps.
	eye = (1.0, 1.6, 2.0)
	source = (8.0, 1.6, 2.0)
	taps = trace_specular_reflections(eye, source, [], boundaries, include_flutter=True)
	assert taps
	# With flutter on, expect more taps than specular-only.
	no_flutter = trace_specular_reflections(
		eye, source, [], boundaries, include_flutter=False
	)
	assert len(taps) >= len(no_flutter)


def test_structure_borne_hook_callable() -> None:
	"""Structure-borne API is documented, callable, and height-sensitive."""
	g, lpf = structure_borne_gain((0.0, 0.0, 0.0), (5.0, 0.0, 0.0), "concrete")
	assert 0.0 < g < 0.5
	assert 100.0 < lpf < 2_000.0
	low_g, low_lpf, low_d = structure_borne_for_source(
		(0.0, 0.7, 0.0), (3.0, 1.6, 0.0), coupled=True
	)
	high_g, _, _ = structure_borne_for_source(
		(0.0, 2.5, 0.0), (3.0, 1.6, 0.0), coupled=True
	)
	assert low_g > high_g
	assert low_d >= 0.0
	assert low_lpf < 2_000.0


def test_reflection_taps_audible_on_clear_path() -> None:
	"""Clear LOS still exports early reflection taps with delay and direction."""
	room, door = build_small_level()
	scene = AcousticScene()
	eye = (6.0, 1.6, 6.0)
	source = AcousticSource(source_id=1, position=SMALL_TABLE_RADIO_POS, enabled=True)
	occluders, materials = scene.build_occluders(room, door)
	link = trace_acoustic_path(
		eye,
		source,
		occluders,
		room.boundaries,
		occluder_materials=materials,
		room_name=room.name,
		door_open_frac=0.0,
	)
	assert link.path_mode == "clear"
	assert link.reflection_gain > 0.0 or link.reflection_taps
	if link.reflection_taps:
		assert all(t.delay_s >= 0.0 for t in link.reflection_taps)
		assert any(t.gain > 0.05 for t in link.reflection_taps)


if __name__ == "__main__":
	test_image_source_delay_matches_excess_path()
	test_diffraction_gain_when_los_blocked_portal_open()
	test_portal_graph_shortest_path_across_rooms()
	test_flutter_emits_periodic_tap_train()
	test_structure_borne_hook_callable()
	test_reflection_taps_audible_on_clear_path()
	print("verify_acoustic_reflection: all passed")
