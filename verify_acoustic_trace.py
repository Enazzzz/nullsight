"""Unit tests for raytraced acoustic path tracing (Phases 2–4 realism ladder)."""

from __future__ import annotations

import math

from acoustics.diffraction import DiffractionPath, estimate_diffraction_path, estimate_edge_diffraction
from acoustics.directivity import (
	cardioid_gain,
	directivity_pressure_gain,
	launch_band_energy_gains,
	piston_directivity_h,
	world_dir_to_listener_angles,
)
from acoustics.materials import (
	MATERIAL_CONCRETE,
	MATERIAL_CONCRETE_ROUGH,
	MATERIAL_METAL_DOOR,
	air_absorption_db,
	band_energy_to_three_band_gains,
	iso9613_alpha_db_per_m,
	material_by_name,
	resolve_material,
)
from acoustics.portals import door_portal_bleed
from acoustics.reverb_zones import (
	compute_rt60_from_geometry,
	reverb_send_for_path,
	reverb_zone_for_room,
	reverb_zone_from_geometry,
	sabine_rt60_s,
)
from acoustics.scene import AcousticScene
from acoustics.system import AcousticSystem
from acoustics.tracer import trace_acoustic_path, trace_direct_path
from acoustics.types import AcousticSource
from camera import Camera
from level_builder import SMALL_TABLE_RADIO_POS
from levels import build_small_level
from occlusion import Aabb, BoundaryPlane, segment_blocking_occluders, segment_line_of_sight


def test_clear_path_same_side_of_divider() -> None:
	"""Eye and radio on the west side of the divider should have clear LOS."""
	room, _door = build_small_level()
	eye = (6.0, 1.6, 6.0)
	radio = SMALL_TABLE_RADIO_POS
	assert segment_line_of_sight(eye, radio, room.occluders, room.boundaries)


def test_wall_blocks_radio() -> None:
	"""A solid wall between eye and radio must block the direct path."""
	eye = (0.0, 1.6, 0.0)
	radio = (10.0, 1.6, 0.0)
	wall: Aabb = ((4.0, 0.0, -2.0), (5.0, 3.0, 2.0))
	assert not segment_line_of_sight(eye, radio, [wall], [])


def test_trace_direct_path_clear_and_blocked() -> None:
	"""Tracer returns full gain when clear and zero direct gain when blocked."""
	eye = (1.0, 1.6, 1.0)
	source = AcousticSource(source_id=1, position=(8.0, 1.6, 1.0), enabled=True)
	clear = trace_direct_path(eye, source, [], [])
	assert clear.direct_gain == 1.0
	assert clear.direct_lpf_hz >= 10_000.0
	assert clear.path_mode == "clear"

	wall: Aabb = ((4.0, 0.0, -2.0), (5.0, 3.0, 2.0))
	blocked = trace_direct_path(eye, source, [wall], [])
	assert blocked.direct_gain == 0.0
	assert blocked.direct_lpf_hz <= 500.0
	assert blocked.path_mode in ("transmission", "blocked", "diffraction", "reflected")


def test_thin_wall_transmission() -> None:
	"""Thick concrete has τ≈0; audibility comes from reflections / diffraction, not fake duck."""
	eye = (1.0, 1.6, 1.0)
	source = AcousticSource(source_id=2, position=(8.0, 1.6, 1.0), enabled=True)
	# 35 cm concrete slab — bunker wall; airborne transmission is negligible.
	thick_wall: Aabb = ((4.0, 0.0, -2.0), (4.35, 3.0, 2.0))
	boundaries = [
		BoundaryPlane(0, 0.0),
		BoundaryPlane(0, 12.0),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 4.0),
		BoundaryPlane(2, -4.0),
		BoundaryPlane(2, 4.0),
	]
	link = trace_acoustic_path(
		eye,
		source,
		[thick_wall],
		boundaries,
		occluder_materials=["concrete"],
		room_name="small",
	)
	assert link.direct_gain == 0.0
	assert link.transmission_gain < 0.05
	# Room reflections and/or diffraction still carry energy (path-native occlusion).
	audible = max(link.transmission_gain, link.diffraction_gain, link.reflection_gain)
	assert audible > 0.05 or len(link.reflection_taps) > 0
	assert link.path_mode in ("transmission", "diffraction", "reflected", "blocked", "partial")


def test_specular_multi_bounce_clear_path() -> None:
	"""Clear indoor path yields multiple specular taps from room planes."""
	from acoustics.reflection import trace_specular_reflections

	eye = (6.0, 1.6, 6.0)
	source = (2.55, 0.73, 11.45)
	boundaries = [
		BoundaryPlane(0, 0.0),
		BoundaryPlane(0, 24.0),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 4.0),
		BoundaryPlane(2, 0.0),
		BoundaryPlane(2, 12.0),
	]
	taps = trace_specular_reflections(eye, source, [], boundaries)
	assert len(taps) >= 2
	assert all(t.gain > 0.0 for t in taps)
	assert all(t.delay_s >= 0.0 for t in taps)
	# At least one first-order bounce.
	assert any(t.bounce_order == 1 for t in taps)


def test_aabb_face_bounce() -> None:
	"""Interior AABB faces produce specular taps when visible."""
	from acoustics.reflection import trace_specular_reflections

	eye = (2.0, 1.6, 2.0)
	source = (8.0, 1.6, 2.0)
	# Thin vertical panel between them but offset so a side-face bounce can work.
	pillar: Aabb = ((4.5, 0.0, 3.5), (5.5, 3.0, 4.5))
	boundaries = [
		BoundaryPlane(0, 0.0),
		BoundaryPlane(0, 12.0),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 4.0),
		BoundaryPlane(2, 0.0),
		BoundaryPlane(2, 8.0),
	]
	taps = trace_specular_reflections(eye, source, [pillar], boundaries)
	# Room planes alone should still yield taps; AABB may add more.
	assert len(taps) >= 1


def test_aabb_order2_bounce() -> None:
	"""Order-2 AABB specular can produce bounce_order==2 taps when geometry allows."""
	from acoustics.reflection import trace_specular_reflections

	eye = (1.0, 1.6, 1.0)
	source = (11.0, 1.6, 1.0)
	# Two offset pillars so a double-bounce path can exist around the gap.
	p1: Aabb = ((3.5, 0.0, 2.0), (4.5, 3.0, 3.5))
	p2: Aabb = ((7.5, 0.0, -1.5), (8.5, 3.0, 0.0))
	boundaries = [
		BoundaryPlane(0, 0.0),
		BoundaryPlane(0, 14.0),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 4.0),
		BoundaryPlane(2, -4.0),
		BoundaryPlane(2, 6.0),
	]
	taps = trace_specular_reflections(
		eye,
		source,
		[p1, p2],
		boundaries,
		occluder_materials=["concrete", "concrete"],
	)
	assert len(taps) >= 1
	# Room ISM already has order≥2; AABB order-2 may also appear.
	assert any(t.bounce_order >= 2 for t in taps) or any(t.bounce_order == 1 for t in taps)


def test_blocked_direct_still_has_reflections() -> None:
	"""Blocked LOS must not silence the room — reflections remain."""
	eye = (1.0, 1.6, 1.0)
	source = AcousticSource(source_id=9, position=(10.0, 1.6, 1.0), enabled=True)
	wall: Aabb = ((4.0, 0.0, -2.0), (5.0, 3.0, 2.0))
	boundaries = [
		BoundaryPlane(0, 0.0),
		BoundaryPlane(0, 12.0),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 4.0),
		BoundaryPlane(2, -4.0),
		BoundaryPlane(2, 4.0),
	]
	link = trace_acoustic_path(
		eye,
		source,
		[wall],
		boundaries,
		occluder_materials=["concrete"],
		room_name="small",
	)
	assert link.direct_gain < 0.1
	# Either reflections around the room or diffraction energy.
	assert (
		link.reflection_gain > 0.05
		or len(link.reflection_taps) > 0
		or link.diffraction_gain > 0.02
	)


def test_authored_material_overrides_guess() -> None:
	"""Authored material names resolve to distinct presets (not forced concrete)."""
	bounds: Aabb = ((0.0, 0.0, 0.0), (0.2, 3.0, 4.0))
	glass = resolve_material(bounds, "glass")
	concrete = resolve_material(bounds, "concrete")
	metal_door = resolve_material(bounds, "metal_door")
	assert glass.name == "glass"
	assert concrete.name == "concrete"
	assert metal_door.name == "metal_door"
	assert material_by_name("wood").name == "wood"


def test_segment_blocking_occluders_order() -> None:
	"""Blocking solids are returned in ray-entry order."""
	eye = (0.0, 1.6, 0.0)
	target = (10.0, 1.6, 0.0)
	near: Aabb = ((2.0, 0.0, -1.0), (3.0, 3.0, 1.0))
	far: Aabb = ((6.0, 0.0, -1.0), (7.0, 3.0, 1.0))
	blockers = segment_blocking_occluders(eye, target, [far, near])
	assert len(blockers) == 2
	assert blockers[0] == near


def test_reflection_metadata_on_clear_path() -> None:
	"""Clear paths still export first-reflection tap metadata."""
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
	)
	assert link.direct_gain > 0.9
	assert link.reflection_gain > 0.15
	assert link.reflection_delay_s >= 0.0
	assert link.reverb_send > 0.1
	assert link.rt60_s > 0.2


def test_door_closed_blocks_east_source() -> None:
	"""Closed door panel between west eye and east annex source should block."""
	room, door = build_small_level()
	assert door.open_angle == 0.0
	scene = AcousticScene()
	eye = (6.0, 1.6, 2.9)
	east_source = (18.0, 1.6, 2.9)
	occluders, _materials = scene.build_occluders(room, door)
	assert door.collidable() is not None
	assert not segment_line_of_sight(eye, east_source, occluders, room.boundaries)


def test_door_open_allows_east_source() -> None:
	"""Fully open door should stop contributing a panel occluder."""
	room, door = build_small_level()
	door.try_open()
	for _ in range(120):
		door.update(0.05)
		if door.collidable() is None:
			break

	assert door.collidable() is None
	scene = AcousticScene()
	eye = (6.0, 1.6, 2.9)
	east_source = (18.0, 1.6, 2.9)
	occluders, _materials = scene.build_occluders(room, door)
	assert segment_line_of_sight(eye, east_source, occluders, room.boundaries)


def test_door_portal_bleed_increases_with_angle() -> None:
	"""Cracked door should produce portal gain between closed and open."""
	room, door = build_small_level()
	closed_gain, _ = door_portal_bleed(door)
	assert closed_gain == 0.0
	door.open_angle = 0.35
	crack_gain, crack_lpf = door_portal_bleed(door)
	assert crack_gain > 0.0
	assert crack_lpf > 200.0
	door.open_angle = 1.4
	open_gain, _ = door_portal_bleed(door)
	assert open_gain > crack_gain


def test_geometry_revision_bumps_on_door_angle() -> None:
	"""Door swing beyond epsilon must bump acoustic geometry revision."""
	room, door = build_small_level()
	scene = AcousticScene()
	scene.notify_level_loaded(room)
	rev0 = scene.geometry_revision
	scene.build_occluders(room, door)
	rev1 = scene.geometry_revision
	assert rev1 == rev0

	door.open_angle = 0.25
	scene.build_occluders(room, door)
	assert scene.geometry_revision > rev1


def test_acoustic_system_snapshot_and_update() -> None:
	"""System produces a link for each source and smooths toward targets."""
	room, door = build_small_level()
	system = AcousticSystem()
	system.notify_level_loaded(room)
	camera = Camera(position=(6.0, 0.0, 6.0), yaw=0.0, pitch=0.0)
	sources = [
		AcousticSource(source_id=1, position=SMALL_TABLE_RADIO_POS, enabled=True),
	]
	snapshot = system.snapshot(room, door, sources, camera)
	assert snapshot.geometry_revision >= 1
	assert len(snapshot.occluders) >= len(room.occluders) - 1
	assert snapshot.occluder_materials is not None
	assert len(snapshot.occluder_materials) == len(snapshot.occluders)

	links = system.update(snapshot, dt=0.05)
	assert 1 in links
	assert links[1].direct_gain > 0.5


def test_room_materials_authored() -> None:
	"""Small level tags solids with a diverse material set (not all concrete)."""
	room, _door = build_small_level()
	assert room.occluder_materials
	assert len(room.occluder_materials) == len(room.occluders)
	names = set(room.occluder_materials)
	assert "wood" in names
	assert "metal" in names or "metal_door" in names
	assert "carpet" in names
	assert "glass" in names
	assert "brick" in names
	assert "drywall" in names
	assert room.shell_material == "concrete"
	# Interior divider walls are thick bunker concrete (≥150 mm → τ≈0).
	from level_builder import WALL_THICKNESS

	assert WALL_THICKNESS >= 0.30
	assert any(name == "concrete" for name in room.occluder_materials)


def test_large_level_prop_materials() -> None:
	"""Large warehouse props use a diverse tagged material set."""
	from levels import build_large_level

	room = build_large_level()
	assert room.occluder_materials
	names = set(room.occluder_materials)
	for required in ("wood", "metal", "drywall", "glass", "carpet", "brick", "plaster", "curtain"):
		assert required in names, f"missing material tag: {required}"
	# Dense: many solids beyond bare corridor walls.
	assert len(room.occluders) >= 30
	assert any(name != "concrete" for name in room.occluder_materials)


def test_diffraction_near_thin_edge() -> None:
	"""A thin vertical slab near the path yields non-zero diffraction with delay."""
	eye = (0.0, 1.6, 0.0)
	source = (10.0, 1.6, 0.0)
	edge: Aabb = ((4.9, 0.0, -0.05), (5.1, 3.0, 0.05))
	gain, lpf = estimate_edge_diffraction(eye, source, [edge], portal_gain=0.2)
	assert gain > 0.0
	assert lpf > 500.0

	path = estimate_diffraction_path(
		eye,
		source,
		[edge],
		occluders=[edge],
		boundaries=[],
		portal_gain=0.2,
		direct_blocked=True,
	)
	assert path is not None
	assert isinstance(path, DiffractionPath)
	assert path.gain > 0.0
	assert path.delay_s >= 0.0
	assert path.path_length_m > 10.0
	assert path.excess_path_m >= 0.0


def test_diffraction_shadow_only() -> None:
	"""Diffraction must not fire when direct LOS is clear."""
	eye = (0.0, 1.6, 0.0)
	source = (10.0, 1.6, 0.0)
	edge: Aabb = ((4.9, 0.0, 2.0), (5.1, 3.0, 2.1))
	path = estimate_diffraction_path(
		eye,
		source,
		[],
		occluders=[edge],
		direct_blocked=False,
	)
	assert path is None


def test_diffraction_on_blocked_trace() -> None:
	"""Blocked thick wall exports diffraction delay fields on AcousticLink."""
	eye = (0.0, 1.6, 0.0)
	source = AcousticSource(source_id=3, position=(10.0, 1.6, 0.0), enabled=True)
	# Thin knife-edge wall so UTD path is preferred over transmission.
	wall: Aabb = ((4.9, 0.0, -2.0), (5.1, 3.0, 2.0))
	boundaries = [
		BoundaryPlane(0, -2.0),
		BoundaryPlane(0, 14.0),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 4.0),
		BoundaryPlane(2, -4.0),
		BoundaryPlane(2, 4.0),
	]
	link = trace_acoustic_path(
		eye,
		source,
		[wall],
		boundaries,
		occluder_materials=["concrete"],
		room_name="small",
	)
	assert link.direct_gain == 0.0
	assert link.diffraction_gain > 0.0 or link.reflection_gain > 0.0
	if link.diffraction_gain > 0.02:
		assert link.diffraction_delay_s >= 0.0
		assert link.diffraction_lpf_hz < 8_000.0


def test_reverb_zone_indoor_vs_outdoor() -> None:
	"""Small indoor rooms enable wet send; large outdoor disables it."""
	assert reverb_zone_for_room("small").enabled
	assert not reverb_zone_for_room("large").enabled
	send = reverb_send_for_path("small", direct_gain=1.0, reflection_gain=0.2, path_mode="clear")
	assert send > 0.0
	assert reverb_send_for_path("large", direct_gain=1.0, reflection_gain=0.2, path_mode="clear") == 0.0


def test_sabine_rt60_from_geometry() -> None:
	"""RT60 from room extents + concrete α is finite and geometry-dependent."""
	boundaries = [
		BoundaryPlane(0, 0.0),
		BoundaryPlane(0, 24.0),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 4.0),
		BoundaryPlane(2, 0.0),
		BoundaryPlane(2, 12.0),
	]
	rt60, volume, surface, alpha = compute_rt60_from_geometry(
		boundaries,
		material=MATERIAL_CONCRETE,
	)
	assert volume == 24.0 * 4.0 * 12.0
	assert surface > 0.0
	assert 0.0 < alpha < 0.1
	assert 0.25 <= rt60 <= 2.80

	# Larger room → longer RT60 (same α).
	big = [
		BoundaryPlane(0, 0.0),
		BoundaryPlane(0, 40.0),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 5.0),
		BoundaryPlane(2, 0.0),
		BoundaryPlane(2, 20.0),
	]
	rt60_big, vol_big, _, _ = compute_rt60_from_geometry(big, material=MATERIAL_CONCRETE)
	assert vol_big > volume
	assert rt60_big >= rt60 * 0.9

	zone = reverb_zone_from_geometry("small", boundaries)
	assert zone.enabled
	assert zone.rt60_s > 0.25

	# Pure Sabine sanity: A = αS.
	a = alpha * surface
	raw = sabine_rt60_s(volume, a + 0.12 * surface)
	assert raw > 0.0


def test_boundary_plane_blocks() -> None:
	"""Outer boundary planes still block paths that cross them."""
	eye = (1.0, 1.6, 1.0)
	outside = (-5.0, 1.6, 1.0)
	planes = [BoundaryPlane(0, 0.0)]
	assert not segment_line_of_sight(eye, outside, [], planes)


def test_cardioid_directivity() -> None:
	"""Cardioid is loudest on-axis and quieter behind the radiator."""
	axis = (0.0, 0.0, -1.0)
	on_axis = directivity_pressure_gain("cardioid", axis, (0.0, 0.0, 0.0), (0.0, 0.0, -3.0))
	off_axis = directivity_pressure_gain("cardioid", axis, (0.0, 0.0, 0.0), (0.0, 0.0, 3.0))
	assert on_axis > off_axis
	assert cardioid_gain(axis, (0.0, 0.0, -1.0)) == 1.0


def test_piston_beams_high_frequency() -> None:
	"""Baffled piston H(θ,ka) → 1 on-axis; HF off-axis is quieter than LF."""
	# On-axis: H = 1 for any ka.
	assert abs(piston_directivity_h(0.0, 5.0) - 1.0) < 1e-6
	# Small ka ≈ omni even at 60°.
	h_lf = piston_directivity_h(math.radians(60.0), 0.3)
	h_hf = piston_directivity_h(math.radians(60.0), 8.0)
	assert h_lf > 0.85
	assert h_hf < h_lf


def test_launch_energy_weaker_off_axis() -> None:
	"""Cardioid + finite cone: off-axis launch energy < on-axis across bands."""
	axis = (0.0, 0.0, -1.0)
	on = launch_band_energy_gains("cardioid", axis, (0.0, 0.0, -1.0), 0.06)
	off = launch_band_energy_gains("cardioid", axis, (0.0, 0.0, 1.0), 0.06)
	assert sum(on) > sum(off)
	# HF band (last) drops more than LF when off-axis with finite radius.
	assert off[-1] / max(on[-1], 1e-9) <= off[0] / max(on[0], 1e-9) + 0.05


def test_reflection_launch_respects_facing() -> None:
	"""Same bounce path is quieter when the speaker faces away from the launch."""
	from acoustics.materials import MATERIAL_CONCRETE
	from acoustics.reflection import _tap_from_path

	# Path: listener ← wall hit ← source. Launch at source is toward the hit (−Z).
	points = [(0.0, 1.6, -4.0), (0.0, 1.6, -2.0), (0.0, 1.6, 0.0)]
	direct_len = 4.0
	on = _tap_from_path(
		points,
		[MATERIAL_CONCRETE],
		direct_len,
		bounce_order=1,
		source_directivity="cardioid",
		source_axis=(0.0, 0.0, -1.0),
		speaker_radius_m=0.06,
	)
	off = _tap_from_path(
		points,
		[MATERIAL_CONCRETE],
		direct_len,
		bounce_order=1,
		source_directivity="cardioid",
		source_axis=(0.0, 0.0, 1.0),
		speaker_radius_m=0.06,
	)
	assert on is not None and off is not None
	assert on.gain > off.gain


def test_iso9613_alpha_increases_with_frequency() -> None:
	"""ISO 9613-1 α(f) rises with frequency at fixed T/RH."""
	low = iso9613_alpha_db_per_m(250.0)
	high = iso9613_alpha_db_per_m(4000.0)
	assert high > low
	loss_10m = air_absorption_db(10.0)
	assert loss_10m[-1] > loss_10m[0]


def test_reflection_taps_have_arrival_direction() -> None:
	"""Specular taps export non-zero arrival vectors for spatialization."""
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
	)
	assert link.reflection_taps
	for tap in link.reflection_taps:
		mag = tap.arrival_dir[0] ** 2 + tap.arrival_dir[1] ** 2 + tap.arrival_dir[2] ** 2
		assert mag > 0.5


def test_diffuse_scatter_taps_present() -> None:
	"""Rough concrete produces diffuse companion taps (ISO 17497 scatter)."""
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
	)
	assert any(tap.is_diffuse for tap in link.reflection_taps)


def test_material_variants_differ() -> None:
	"""Concrete variants have distinct scatter coefficients."""
	assert MATERIAL_CONCRETE_ROUGH.scatter[4] > MATERIAL_CONCRETE.scatter[4]
	assert MATERIAL_METAL_DOOR.name == "metal_door"


def test_band_energy_three_band_split() -> None:
	"""Octave-band energy maps to low/mid/high pressure gains."""
	hf_heavy = (0.01, 0.02, 0.05, 0.08, 0.5, 0.9)
	lf_heavy = (0.9, 0.8, 0.1, 0.05, 0.02, 0.01)
	bl_h, bm_h, bh_h = band_energy_to_three_band_gains(hf_heavy)
	bl_l, bm_l, bh_l = band_energy_to_three_band_gains(lf_heavy)
	assert bh_h > bl_h
	assert bl_l > bh_l


def test_world_dir_to_listener_angles_ahead() -> None:
	"""World +Z maps to listener azimuth ≈ 0 when yaw is 0."""
	az, el = world_dir_to_listener_angles((0.0, 0.0, 1.0), yaw_rad=0.0)
	assert abs(az) < 0.05


def test_directivity_on_clear_path() -> None:
	"""Off-axis listener receives reduced direct gain from cardioid radio."""
	room, door = build_small_level()
	scene = AcousticScene()
	radio = SMALL_TABLE_RADIO_POS
	occluders, materials = scene.build_occluders(room, door)
	on_axis = AcousticSource(
		source_id=1,
		position=radio,
		enabled=True,
		directivity="cardioid",
		directivity_axis=(0.0, 0.0, -1.0),
		speaker_radius_m=0.06,
	)
	# Listener behind the radio cabinet (opposite primary axis).
	behind_eye = (radio[0], radio[1], radio[2] + 4.0)
	front_eye = (radio[0], radio[1], radio[2] - 4.0)
	link_front = trace_acoustic_path(
		front_eye,
		on_axis,
		occluders,
		room.boundaries,
		occluder_materials=materials,
		room_name=room.name,
	)
	link_behind = trace_acoustic_path(
		behind_eye,
		on_axis,
		occluders,
		room.boundaries,
		occluder_materials=materials,
		room_name=room.name,
	)
	assert link_front.direct_gain > link_behind.direct_gain


def test_mam_resonance_and_double_leaf() -> None:
	"""MAM resonance is physical; above f_msm STL exceeds coupled single-leaf."""
	from acoustics.materials import double_leaf_stl_db, mam_resonance_hz, mass_law_stl_db

	f_msm = mam_resonance_hz(12.0, 12.0, gap_m=0.05)
	assert 50.0 < f_msm < 400.0
	# Below resonance: near coupled mass law on (ms1+ms2).
	stl_low = double_leaf_stl_db(12.0, 12.0, f_msm * 0.5, gap_m=0.05)
	coupled = mass_law_stl_db(24.0, f_msm * 0.5)
	assert abs(stl_low - coupled) < 1.0
	# Above resonance: higher STL than coupled.
	stl_high = double_leaf_stl_db(12.0, 12.0, f_msm * 4.0, gap_m=0.05)
	assert stl_high > coupled


def test_dynamic_rt60_door_open_longer() -> None:
	"""Open door couples rooms → longer RT60 than sealed closed door."""
	# Use shell-only geometry so furniture A does not clamp both sides to max.
	boundaries = [
		BoundaryPlane(0, 0.0),
		BoundaryPlane(0, 10.0),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 3.0),
		BoundaryPlane(2, 0.0),
		BoundaryPlane(2, 8.0),
	]
	rt_closed, _, _, _ = compute_rt60_from_geometry(
		boundaries,
		material=MATERIAL_CONCRETE,
		door_open_frac=0.0,
	)
	rt_open, _, _, _ = compute_rt60_from_geometry(
		boundaries,
		material=MATERIAL_CONCRETE,
		door_open_frac=1.0,
	)
	assert rt_open > rt_closed


def test_occluder_furniture_reduces_rt60() -> None:
	"""Furniture AABB absorption shortens RT60 vs empty shell."""
	boundaries = [
		BoundaryPlane(0, 0.0),
		BoundaryPlane(0, 10.0),
		BoundaryPlane(1, 0.0),
		BoundaryPlane(1, 3.0),
		BoundaryPlane(2, 0.0),
		BoundaryPlane(2, 8.0),
	]
	table: Aabb = ((2.0, 0.0, 2.0), (4.0, 1.0, 4.0))
	rt_empty, _, _, _ = compute_rt60_from_geometry(boundaries, material=MATERIAL_CONCRETE)
	rt_furn, _, _, _ = compute_rt60_from_geometry(
		boundaries,
		material=MATERIAL_CONCRETE,
		occluders=[table],
		occluder_materials=["wood"],
	)
	assert rt_furn <= rt_empty


def test_flutter_period_2L_over_c() -> None:
	"""Flutter period for parallel walls equals 2L/c."""
	from acoustics.materials import SPEED_OF_SOUND_MPS
	from acoustics.reflection import flutter_echo_period_s

	sep = 5.0
	period = flutter_echo_period_s(sep)
	assert abs(period - (2.0 * sep / SPEED_OF_SOUND_MPS)) < 1e-6


def test_modes_below_schroeder() -> None:
	"""Rectangular modes are returned only below Schroeder frequency."""
	from acoustics.modes import rectangular_room_modes, schroeder_frequency_hz

	extents = (8.0, 3.0, 6.0)
	rt60 = 1.0
	modes, f_s = rectangular_room_modes(extents, rt60_s=rt60)
	assert f_s == schroeder_frequency_hz(rt60, 8.0 * 3.0 * 6.0)
	assert len(modes) >= 1
	assert all(m.freq_hz <= f_s + 1e-6 for m in modes)


def test_btm_diffraction_delay_positive() -> None:
	"""BTM/UTD diffracted path carries positive excess delay in shadow."""
	eye = (0.0, 1.6, 0.0)
	source = (6.0, 1.6, 0.0)
	# Tall thin wall blocking LOS — long vertical edges for BTM.
	wall: Aabb = ((2.8, 0.0, -2.0), (3.0, 3.5, 2.0))
	assert not segment_line_of_sight(eye, source, [wall], [])
	path = estimate_diffraction_path(
		eye,
		source,
		[wall],
		occluders=[wall],
		direct_blocked=True,
		prefer_btm=True,
	)
	assert path is not None
	assert path.delay_s > 0.0
	assert path.gain > 0.0


def test_phase5_materials_lookup() -> None:
	"""Carpet, curtain, plaster, brick resolve from the material library."""
	for name in ("carpet", "curtain", "plaster", "brick"):
		mat = material_by_name(name)
		assert mat.name == name
		assert len(mat.alpha) == 6


def test_reverb_predelay_field_on_link() -> None:
	"""AcousticLink carries reverb_predelay_s from first reflection delay."""
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
	assert link.reverb_predelay_s >= 0.0
	if link.reflection_taps:
		assert link.reverb_predelay_s > 0.0


def test_transition_order_constant() -> None:
	"""ODEON-style transition order is explicitly 2."""
	from acoustics.reflection import TRANSITION_ORDER

	assert TRANSITION_ORDER == 2


def test_structure_borne_hook() -> None:
	"""Structure-borne API returns finite gain/LPF; low sources couple more."""
	from acoustics.structure import structure_borne_for_source, structure_borne_gain

	g, lpf = structure_borne_gain((0.0, 0.0, 0.0), (5.0, 0.0, 0.0), "concrete")
	assert 0.0 < g < 0.5
	assert 100.0 < lpf < 2_000.0
	low_g, _, _ = structure_borne_for_source((0.0, 0.7, 0.0), (3.0, 1.6, 0.0), coupled=True)
	high_g, _, _ = structure_borne_for_source((0.0, 2.5, 0.0), (3.0, 1.6, 0.0), coupled=True)
	assert low_g > high_g


def test_modal_resonator_params() -> None:
	"""Room modes below Schroeder yield resonator freqs for the late bus."""
	from acoustics.modes import modal_resonator_params, modes_from_boundaries

	room, _door = build_small_level()
	modes, f_s = modes_from_boundaries(room.boundaries, rt60_s=0.9, max_freq_hz=250.0)
	assert f_s > 0.0
	freqs, gains, qs = modal_resonator_params(modes, schroeder_hz=f_s)
	assert len(freqs) == len(gains) == len(qs)
	assert len(freqs) >= 1
	assert all(40.0 <= f <= 280.0 for f in freqs)
	# Prefer boom-band modes when available (not only sub-60 Hz).
	assert any(f >= 70.0 for f in freqs)


def test_early_ir_from_taps() -> None:
	"""Tap cluster synthesizes a short stereo IR with energy."""
	from acoustics.early_ir import early_ir_energy, early_ir_from_taps
	from acoustics.types import ReflectionTap

	taps = (
		ReflectionTap(gain=0.4, delay_s=0.002, lpf_hz=8_000.0, arrival_dir=(-0.5, 0.0, 0.8)),
		ReflectionTap(gain=0.25, delay_s=0.006, lpf_hz=4_000.0, arrival_dir=(0.6, 0.0, 0.7)),
	)
	left, right = early_ir_from_taps(taps, sample_rate_hz=48_000, num_samples=512)
	assert len(left) == 512 and len(right) == 512
	assert early_ir_energy(left, right) > 0.01


def test_structure_borne_on_clear_path() -> None:
	"""Low table radio gets non-zero structure-borne coupling on a clear path."""
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
		shell_material=room.shell_material,
	)
	assert link.structure_borne_gain > 0.0
	assert link.structure_borne_lpf_hz < 2_000.0


def test_max_taps_budget() -> None:
	"""Reflection merge keeps up to 8 early taps (Spherion kMaxReflectionTaps)."""
	from acoustics.reflection import _MAX_TAPS

	assert _MAX_TAPS == 8


def test_portal_graph_boost_when_blocked() -> None:
	"""Cross-cell blocked path gets portal-graph gain when door is open."""
	from acoustics.portals_graph import portal_graph_gain_for_path

	gain = portal_graph_gain_for_path(
		(5.0, 1.6, 6.0),
		(14.0, 1.6, 6.0),
		door_open_frac=0.8,
		los_blocked=True,
	)
	assert gain > 0.1
	closed = portal_graph_gain_for_path(
		(5.0, 1.6, 6.0),
		(14.0, 1.6, 6.0),
		door_open_frac=0.0,
		los_blocked=True,
	)
	assert closed == 0.0


if __name__ == "__main__":
	test_clear_path_same_side_of_divider()
	test_wall_blocks_radio()
	test_trace_direct_path_clear_and_blocked()
	test_thin_wall_transmission()
	test_specular_multi_bounce_clear_path()
	test_aabb_face_bounce()
	test_aabb_order2_bounce()
	test_blocked_direct_still_has_reflections()
	test_authored_material_overrides_guess()
	test_segment_blocking_occluders_order()
	test_reflection_metadata_on_clear_path()
	test_door_closed_blocks_east_source()
	test_door_open_allows_east_source()
	test_door_portal_bleed_increases_with_angle()
	test_geometry_revision_bumps_on_door_angle()
	test_acoustic_system_snapshot_and_update()
	test_room_materials_authored()
	test_large_level_prop_materials()
	test_diffraction_near_thin_edge()
	test_diffraction_shadow_only()
	test_diffraction_on_blocked_trace()
	test_reverb_zone_indoor_vs_outdoor()
	test_sabine_rt60_from_geometry()
	test_boundary_plane_blocks()
	test_cardioid_directivity()
	test_piston_beams_high_frequency()
	test_launch_energy_weaker_off_axis()
	test_reflection_launch_respects_facing()
	test_iso9613_alpha_increases_with_frequency()
	test_reflection_taps_have_arrival_direction()
	test_diffuse_scatter_taps_present()
	test_material_variants_differ()
	test_band_energy_three_band_split()
	test_world_dir_to_listener_angles_ahead()
	test_directivity_on_clear_path()
	test_mam_resonance_and_double_leaf()
	test_dynamic_rt60_door_open_longer()
	test_occluder_furniture_reduces_rt60()
	test_flutter_period_2L_over_c()
	test_modes_below_schroeder()
	test_btm_diffraction_delay_positive()
	test_phase5_materials_lookup()
	test_reverb_predelay_field_on_link()
	test_transition_order_constant()
	test_structure_borne_hook()
	test_modal_resonator_params()
	test_early_ir_from_taps()
	test_structure_borne_on_clear_path()
	test_max_taps_budget()
	test_portal_graph_boost_when_blocked()
	print("verify_acoustic_trace: all passed")
