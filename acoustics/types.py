"""Shared data contracts between the game, acoustics middleware, and audio bridge."""

from __future__ import annotations

from dataclasses import dataclass

from math3d import Vec3
from occlusion import Aabb, BoundaryPlane


@dataclass(frozen=True)
class AcousticSource:
	"""One spatial source to ray-test from the listener."""

	# Matches ``LevelLoop.entity_id``.
	source_id: int
	position: Vec3
	# False for muted / repair-mode sources.
	enabled: bool = True
	# World-space velocity for Doppler (m/s); optional.
	velocity: Vec3 = (0.0, 0.0, 0.0)
	# Directivity pattern: omni | cardioid | horn | piston (see acoustics.directivity).
	directivity: str = "omni"
	# Primary radiation axis in world space (normalized at use time).
	directivity_axis: Vec3 = (0.0, 0.0, -1.0)
	# Effective circular-piston radius (m). 0 → point source (no ka beaming).
	speaker_radius_m: float = 0.0


@dataclass(frozen=True)
class AcousticSolid:
	"""One acoustic occluder with an optional authored material name."""

	bounds: Aabb
	material: str = "concrete"


@dataclass(frozen=True)
class AcousticWorldSnapshot:
	"""Immutable world state for one acoustics update."""

	listener_eye: Vec3
	# Room solids plus dynamic door panel when present.
	occluders: list[Aabb]
	boundaries: list[BoundaryPlane]
	sources: list[AcousticSource]
	# Bumps on level load / door angle change.
	geometry_revision: int
	# Parallel material names for ``occluders`` (empty → guess from AABB).
	occluder_materials: list[str] | None = None
	# Doorway portal bleed when the panel is partially open (0–1).
	portal_gain: float = 0.0
	portal_lpf_hz: float = 18_000.0
	# Door open fraction 0–1 for dynamic RT60 / MAM / portal graph.
	door_open_frac: float = 0.0
	# Room name for reverb zone lookup.
	room_name: str = ""
	# Room-shell material for Sabine/Eyring (walls/floor/ceiling).
	shell_material: str = "concrete"
	# Listener orientation for directivity and tap azimuth (radians).
	listener_yaw_rad: float = 0.0
	listener_pitch_rad: float = 0.0


@dataclass(frozen=True)
class LineOfSightResult:
	"""Result of a direct-path segment test."""

	clear: bool


@dataclass(frozen=True)
class ReflectionTap:
	"""One early-reflection arrival (pressure gain relative to direct).

	Includes arrival direction for directional spatialization and per-band
	energy for multiband rendering (not a single broadband LPF approximation).
	"""

	gain: float
	delay_s: float
	lpf_hz: float
	bounce_order: int = 1
	# World-space unit vector of incoming wave at listener (last path leg).
	arrival_dir: Vec3 = (0.0, 0.0, 1.0)
	# Octave-band pressure energy (125…4000 Hz) — drives 3-band tap split.
	band_energy: tuple[float, float, float, float, float, float] = (
		1.0,
		1.0,
		1.0,
		1.0,
		1.0,
		1.0,
	)
	# True for Lambert diffuse scatter taps (ISO 17497 s fraction).
	is_diffuse: bool = False


@dataclass(frozen=True)
class AcousticLink:
	"""Per-source acoustic path parameters for the Spherion bridge.

	Occlusion is path-native: ``direct_gain == 0`` means no clear direct ray.
	Audibility when blocked comes from transmission, diffraction, and/or
	``reflection_taps`` that still reach the listener.
	"""

	source_id: int
	# Direct path pressure gain (0 = no LOS). Spreading is owned by Spherion.
	direct_gain: float
	direct_lpf_hz: float
	# Airborne transmission through thin partitions (thick concrete → ~0).
	transmission_gain: float = 0.0
	transmission_lpf_hz: float = 400.0
	# Loudest early specular tap (compat / HUD); full cluster in reflection_taps.
	reflection_gain: float = 0.0
	reflection_delay_s: float = 0.0
	reflection_lpf_hz: float = 18_000.0
	# Validated specular paths (room ISM + AABB faces); empty if none reach.
	reflection_taps: tuple[ReflectionTap, ...] = ()
	# Diffraction around edges / portals when LOS is blocked (UTD path).
	diffraction_gain: float = 0.0
	# Excess diffracted delay ((s'+s)−|S−L|)/c — same convention as reflection taps.
	diffraction_delay_s: float = 0.0
	diffraction_lpf_hz: float = 1_200.0
	# Diagnostic: clear | partial | transmission | diffraction | reflected | blocked.
	path_mode: str = "clear"
	# Late-reverb wet send (0–1) into Spherion's Schroeder bus.
	reverb_send: float = 0.0
	# Geometry-derived RT60 for the active room (0 = use zone preset only).
	rt60_s: float = 0.0
	# First-reflection delay → late bus predelay (seconds).
	reverb_predelay_s: float = 0.0
	# Portal-graph edge boost when LOS is blocked across a door (0–1).
	portal_graph_gain: float = 0.0
	# Structure-borne floor/table coupling (dull LF path parallel to airborne).
	structure_borne_gain: float = 0.0
	structure_borne_lpf_hz: float = 450.0
	structure_borne_delay_s: float = 0.0
	# Extra diffracted arrivals beyond the primary (multi-edge soft shadow).
	diffraction_extra: tuple[ReflectionTap, ...] = ()
