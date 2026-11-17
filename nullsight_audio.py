"""Spherion bridge for NULLSIGHT — listener sync and level sound placement."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from acoustics.directivity import (
	DEFAULT_FACING,
	DEFAULT_SPEAKER_RADIUS_M,
	world_dir_to_listener_angles,
)
from acoustics.materials import band_energy_to_three_band_gains
from acoustics.modes import modal_resonator_params, modes_from_boundaries
from acoustics.reverb_zones import reverb_zone_from_geometry, wet_level_from_rt60
from acoustics.types import AcousticLink, AcousticSource, ReflectionTap
from audio_assets import (
	DEFAULT_HRTF_FILENAME,
	ensure_bundled_audio_assets,
	ensure_repair_clunk_wav,
	ensure_repair_tone_bank,
	resolve_hrtf_path,
)
from camera import Camera
from level_builder import AWAKENING_RADIO_POS, SMALL_TABLE_RADIO_POS
from room import RoomData
from settings import AudioSettings

# Main-menu music asset (direct stereo, not spatialized).
MENU_MUSIC_FILENAME = "Legends  Nugroove TM.mp3"
MENU_MUSIC_START_SEC = 115.0  # 1:55 into the track
MENU_MUSIC_FADE_IN_SEC = 2.5
MENU_MUSIC_FADE_OUT_SEC = 1.5
CREDITS_MUSIC_FADE_IN_SEC = 4.0
CREDITS_MUSIC_FADE_OUT_SEC = 1.5
# Master gain multiplier while unfocused and quiet-unfocus is enabled.
UNFOCUSED_MASTER_GAIN = 0.2
UNFOCUSED_FADE_SEC = 0.45
# Repair minigame — crossfaded bin gain and ducked world audio.
_REPAIR_TONE_GAIN = 0.34
_REPAIR_CLUNK_GAIN = 0.62
_REPAIR_PROXIMITY_EXP = 2.35


# Logical sound name, asset key, world XYZ, loop gain, speaker facing/size.
@dataclass(frozen=True)
class LevelSound:
	"""One spatial loop placed in world space."""

	asset_key: str
	x: float
	y: float
	z: float
	gain: float = 0.35
	loop: bool = True
	# Radiation pattern: omni | cardioid | horn (piston size via speaker_radius_m).
	directivity: str = "omni"
	# Primary radiation axis in world space.
	directivity_axis: tuple[float, float, float] = (0.0, 0.0, -1.0)
	# Effective circular-piston radius (m); 0 → point source.
	speaker_radius_m: float = 0.0


@dataclass
class LevelLoop:
	"""Runtime handle for one spatial loop in the world."""

	entity_id: int
	asset_key: str
	base_x: float
	base_y: float
	base_z: float
	base_gain: float
	phantom: bool = False
	# Extra multiplier (e.g. phantom fade) applied with obstruction.
	runtime_gain_scale: float = 1.0
	# Last known world position for velocity estimation.
	last_x: float = 0.0
	last_y: float = 0.0
	last_z: float = 0.0
	# Estimated velocity (m/s).
	vx: float = 0.0
	vy: float = 0.0
	vz: float = 0.0
	# Speaker facing + size (copied from LevelSound at spawn).
	directivity: str = "omni"
	directivity_axis: tuple[float, float, float] = (0.0, 0.0, -1.0)
	speaker_radius_m: float = 0.0


def _menu_music_path() -> Path | None:
	"""Return the bundled menu music file when present on disk."""
	path = Path(__file__).resolve().parent / "assets" / MENU_MUSIC_FILENAME
	return path if path.is_file() else None


def _credits_music_path(filename: str) -> Path | None:
	"""Return a bundled credits music file when present on disk."""
	path = Path(__file__).resolve().parent / "assets" / filename
	return path if path.is_file() else None


def _authored_speaker(asset_key: str) -> tuple[str, tuple[float, float, float], float]:
	"""Return (pattern, axis, radius_m) defaults for a level asset key."""
	radius = DEFAULT_SPEAKER_RADIUS_M.get(asset_key, 0.0)
	axis = DEFAULT_FACING.get(asset_key, (0.0, 0.0, -1.0))
	if asset_key == "old_radio":
		return "cardioid", axis, radius
	if asset_key in ("beacon", "hum"):
		return "horn", axis, radius
	return "omni", axis, radius


def _level_sounds(room: RoomData) -> list[LevelSound]:
	"""Return spatial loops for the active room.

	Facility room JSON ``authored_sounds`` wins when present; otherwise fall
	back to the hard-coded probe layouts by room name.
	"""
	authored = getattr(room, "authored_sounds", None) or []
	if authored:
		sounds: list[LevelSound] = []
		for entry in authored:
			asset = str(entry.get("asset_key", "hum"))
			pos = entry.get("position") or [0.0, 1.0, 0.0]
			prop = entry.get("propagation") or {}
			pat, axis, radius = _authored_speaker(asset)
			if "directivity" in prop:
				pat = str(prop["directivity"])
			if "axis" in prop:
				raw_axis = prop["axis"]
				axis = (float(raw_axis[0]), float(raw_axis[1]), float(raw_axis[2]))
			sounds.append(
				LevelSound(
					asset,
					float(pos[0]),
					float(pos[1]),
					float(pos[2]),
					gain=float(entry.get("volume", entry.get("gain", 0.25))),
					loop=bool(entry.get("loop", True)),
					directivity=pat,
					directivity_axis=axis,
					speaker_radius_m=radius,
				)
			)
		return sounds

	if room.name == "large":
		center_x = (room.bounds[0] + room.bounds[1]) * 0.5
		center_z = (room.bounds[2] + room.bounds[3]) * 0.5
		# Ceiling beacons aim down; floor hums face +Z into the open volume.
		b_pat, b_axis, b_r = _authored_speaker("beacon")
		h_pat, h_axis, h_r = _authored_speaker("hum")
		return [
			LevelSound(
				"beacon", center_x, 2.0, center_z + 8.0, gain=0.30,
				directivity=b_pat, directivity_axis=b_axis, speaker_radius_m=b_r,
			),
			LevelSound(
				"hum", 8.0, 1.2, 8.0, gain=0.22,
				directivity=h_pat, directivity_axis=h_axis, speaker_radius_m=h_r,
			),
			LevelSound(
				"hum", 32.0, 1.0, 34.0, gain=0.20,
				directivity=h_pat, directivity_axis=h_axis, speaker_radius_m=h_r,
			),
			LevelSound(
				"beacon", 20.0, 1.8, 24.0, gain=0.25,
				directivity=b_pat, directivity_axis=b_axis, speaker_radius_m=b_r,
			),
		]

	# Awakening / small rooms — one old radio at the authored shelf position.
	if room.name == "awakening":
		radio_x, radio_y, radio_z = AWAKENING_RADIO_POS
	else:
		radio_x, radio_y, radio_z = SMALL_TABLE_RADIO_POS
	r_pat, r_axis, r_r = _authored_speaker("old_radio")
	# West-wall radio faces into the room (+X); table radio faces -Z.
	if room.name == "awakening":
		r_axis = (1.0, 0.0, 0.0)
	return [
		LevelSound(
			"old_radio", radio_x, radio_y, radio_z, gain=0.10,
			directivity=r_pat, directivity_axis=r_axis, speaker_radius_m=r_r,
		),
	]


def _room_uses_outdoor_air(room: RoomData) -> bool:
	"""Return True when the level should use live atmospheric physics (large/outdoor)."""
	return room.name == "large"


def _build_engine_config(
	audio: AudioSettings,
	*,
	hrtf_path: str,
) -> object:
	"""Build a Spherion ``EngineConfig`` for v0.2 physics and distance rolloff."""
	import spherion

	config = spherion.EngineConfig()
	config.sample_rate_hz = 48_000
	config.order = audio.order
	config.block_size = audio.block_size
	config.hrtf_path = hrtf_path
	config.max_audible_distance_m = audio.max_audible_distance_m
	config.distance_rolloff = 1
	config.reference_distance_m = 1.0
	config.min_distance_m = 0.5
	config.enable_hf_air_absorption = audio.enable_hf_air_absorption
	config.enable_doppler = audio.enable_doppler
	config.enable_propagation_delay = audio.enable_propagation_delay
	config.enable_head_shadow = audio.enable_head_shadow
	config.use_atmospheric_conditions = False
	config.atmospheric_temperature_c = audio.atmospheric_temperature_c
	config.atmospheric_relative_humidity_percent = audio.atmospheric_relative_humidity_percent
	config.preserve_stereo_width = True
	return config


def _apply_room_acoustics(
	engine: object,
	room: RoomData,
	audio: AudioSettings,
	*,
	door_open_frac: float = 0.0,
) -> tuple[float, float]:
	"""Configure indoor vs outdoor air and late-reverb bus for the active room.

	Indoor RT60 comes from Sabine/Eyring on room shell geometry + materials.
	Modal resonators add standing-wave LF coloration below Schroeder.
	Returns ``(rt60_s, wet_level)`` so callers can seed the live-RT60 cache.
	"""
	if _room_uses_outdoor_air(room) and audio.use_atmospheric_conditions:
		engine.set_use_atmospheric_conditions(True)
		engine.notify_atmospheric_changed(
			audio.atmospheric_temperature_c,
			audio.atmospheric_relative_humidity_percent,
		)
	else:
		engine.set_use_atmospheric_conditions(False)

	shell = getattr(room, "shell_material", "concrete") or "concrete"
	zone = reverb_zone_from_geometry(
		room.name,
		room.boundaries,
		material_name=shell,
		door_open_frac=door_open_frac,
		occluders=room.occluders,
		occluder_materials=room.occluder_materials,
	)
	wet = 0.0
	setter = getattr(engine, "set_late_reverb", None)
	if setter is not None:
		if not zone.enabled:
			setter(zone.rt60_s, 0.0)
			wet = 0.0
		else:
			wet = max(wet_level_from_rt60(zone.rt60_s), 0.35 if zone.enabled else 0.0)
			setter(zone.rt60_s, wet)

	# Audible room modes on the late bus (empty when outdoor / no RT60).
	modal_setter = getattr(engine, "set_late_reverb_modal_resonators", None)
	if modal_setter is not None:
		if zone.enabled and zone.rt60_s > 0.05:
			# Include modes up to ~250 Hz even when f_s is low (large rooms).
			modes, f_s = modes_from_boundaries(
				room.boundaries, rt60_s=zone.rt60_s, max_freq_hz=250.0
			)
			freqs, gains, qs = modal_resonator_params(modes, schroeder_hz=f_s)
			modal_setter(freqs, gains, qs)
		else:
			modal_setter([], [], [])
	return zone.rt60_s if zone.enabled else 0.0, wet


class NullsightAudio:
	"""Owns a Spherion ``AudioWorld`` and syncs NULLSIGHT camera pose each frame."""

	def __init__(
		self,
		room: RoomData,
		*,
		audio_settings: AudioSettings | None = None,
		enabled: bool = True,
		order: int = 3,
		block_size: int = 256,
		master_gain: float = 1.0,
		max_audible_distance_m: float = 80.0,
		hrtf_enabled: bool = True,
		hrtf_filename: str = DEFAULT_HRTF_FILENAME,
		spatial_gain: float = 2.0,
	) -> None:
		self._world = None
		self._reason: str | None = None
		self._menu_music = None
		self._menu_fade = 0.0
		self._menu_fading_in = False
		self._menu_fading_out = False
		self._credits_music = None
		self._credits_fade = 0.0
		self._credits_fading_in = False
		self._credits_fading_out = False
		self._credits_restore_menu = False
		self._sample_rate_hz = 48_000
		self._asset_paths: dict[str, Path] = {}
		self._loops: list[LevelLoop] = []
		self._spatial_gain = spatial_gain
		self._audio_settings = audio_settings or AudioSettings()
		self._focused = True
		self._quiet_unfocused_enabled = True
		self._focus_gain_mult = 1.0
		self._repair_mode = False
		self._repair_bins: list[object] = []
		self._repair_clunk: object | None = None
		# Last listener orientation for tap azimuth conversion.
		self._listener_yaw_rad = 0.0
		self._listener_pitch_rad = 0.0
		self._loaded_hrtf_path: str | None = None
		self._room = room
		# Per-entity obstruction gain from acoustic ray links (1.0 = clear).
		self._obstruction_gain: dict[int, float] = {}
		# Latest smoothed links (direct + transmission + reflection metadata).
		self._acoustic_links: dict[int, AcousticLink] = {}
		# Last late-bus RT60 pushed from live door-coupled Sabine/Eyring.
		self._last_live_rt60_s: float = -1.0
		self._last_live_wet: float = -1.0

		if audio_settings is not None:
			enabled = audio_settings.enabled
			order = audio_settings.order
			block_size = audio_settings.block_size
			master_gain = audio_settings.master_gain
			max_audible_distance_m = audio_settings.max_audible_distance_m
			hrtf_enabled = audio_settings.hrtf_enabled
			hrtf_filename = audio_settings.hrtf_filename
			spatial_gain = audio_settings.spatial_gain

		if not enabled:
			self._reason = "disabled"
			return

		try:
			import spherion
		except ImportError:
			self._reason = "spherion not installed"
			return

		try:
			self._asset_paths = ensure_bundled_audio_assets()
		except OSError as exc:
			self._reason = f"asset setup failed: {exc}"
			return

		asset_paths = self._asset_paths

		hrtf_path = ""
		if hrtf_enabled:
			resolved = resolve_hrtf_path(hrtf_filename or DEFAULT_HRTF_FILENAME)
			if resolved is not None:
				hrtf_path = str(resolved)
				self._loaded_hrtf_path = hrtf_path

		engine_config = _build_engine_config(self._audio_settings, hrtf_path=hrtf_path)
		self._world = spherion.AudioWorld(config=engine_config)
		self._world.set_master_gain(master_gain)
		self._sample_rate_hz = self._world.engine.sample_rate_hz
		rt60_s, wet = _apply_room_acoustics(self._world.engine, room, self._audio_settings)
		self._last_live_rt60_s = rt60_s
		self._last_live_wet = wet

		for sound in _level_sounds(room):
			path = asset_paths.get(sound.asset_key)
			if path is None or not path.is_file():
				continue
			entity_id = self._world.spawn(
				str(path),
				sound.x,
				sound.y,
				sound.z,
				loop=sound.loop,
				gain=sound.gain * spatial_gain,
			)
			self._loops.append(
				LevelLoop(
					entity_id=entity_id,
					asset_key=sound.asset_key,
					base_x=sound.x,
					base_y=sound.y,
					base_z=sound.z,
					base_gain=sound.gain,
					last_x=sound.x,
					last_y=sound.y,
					last_z=sound.z,
					directivity=sound.directivity,
					directivity_axis=sound.directivity_axis,
					speaker_radius_m=sound.speaker_radius_m,
				)
			)

		menu_path = _menu_music_path()
		if menu_path is not None:
			try:
				self._menu_music = self._world.spawn_direct(
					str(menu_path),
					loop=True,
					gain=0.0,
					active=False,
				)
			except RuntimeError:
				self._menu_music = None

		credits_path = _credits_music_path("Non-Relations.mp3")
		if credits_path is not None:
			try:
				self._credits_music = self._world.spawn_direct(
					str(credits_path),
					loop=True,
					gain=0.0,
					active=False,
				)
			except RuntimeError:
				self._credits_music = None

		self.set_gameplay_sounds_active(False)
		self._init_repair_audio()

		try:
			self._world.start()
		except RuntimeError as exc:
			self._world = None
			self._menu_music = None
			self._credits_music = None
			self._loops.clear()
			self._reason = f"audio device unavailable: {exc}"

	@property
	def asset_paths(self) -> dict[str, Path]:
		"""Bundled loop assets keyed by logical name."""
		return self._asset_paths

	@property
	def level_loops(self) -> list[LevelLoop]:
		"""Real level-placed spatial loops (not health phantoms)."""
		return [loop for loop in self._loops if not loop.phantom]

	def all_spatial_sources(self) -> list[AcousticSource]:
		"""Return entity ids and positions for all spatial loops (level + phantoms)."""
		sources: list[AcousticSource] = []
		for loop in self._loops:
			# Prefer live world position when health FX has drifted the source.
			x, y, z = loop.base_x, loop.base_y, loop.base_z
			if self._world is not None and self._world.contains(loop.entity_id):
				spawned = self._world.get_entity(loop.entity_id)
				x, y, z = spawned.x, spawned.y, spawned.z
			sources.append(
				AcousticSource(
					source_id=loop.entity_id,
					position=(x, y, z),
					enabled=not self._repair_mode,
					velocity=(loop.vx, loop.vy, loop.vz),
					directivity=loop.directivity,
					directivity_axis=loop.directivity_axis,
					speaker_radius_m=loop.speaker_radius_m,
				)
			)
		return sources

	def apply_acoustic_links(self, links: dict[int, AcousticLink]) -> None:
		"""Apply raytraced obstruction, transmission, reflection taps, and Doppler velocity."""
		if self._world is None:
			return

		seen: set[int] = set()
		# Late-bus predelay = min first-reflection delay across active links.
		min_predelay = None
		# Live Sabine/Eyring RT60 from any link (door-coupled zone).
		live_rt60 = None
		for source_id, link in links.items():
			seen.add(source_id)
			self._acoustic_links[source_id] = link
			gain, lpf_hz = self._effective_obstruction(link)
			self._obstruction_gain[source_id] = gain
			if link.reverb_predelay_s > 0.0:
				if min_predelay is None or link.reverb_predelay_s < min_predelay:
					min_predelay = link.reverb_predelay_s
			if link.rt60_s > 0.0:
				if live_rt60 is None or link.rt60_s > live_rt60:
					live_rt60 = link.rt60_s
			loop = self._loop_by_id(source_id)
			if loop is None:
				continue
			# Object gain stays at authored level; dry-path gain carries occlusion.
			self._world.set_gain(source_id, self._effective_loop_gain(loop))
			self._set_dry_path_gain(source_id, gain)
			self._set_obstruction_lowpass(source_id, lpf_hz)
			self._set_reflection_taps(source_id, link)
			# Excess early energy beyond the 8-tap budget → late send (hybrid bridge).
			send = link.reverb_send
			combined = self._combined_early_taps(link)
			cluster = list(link.reflection_taps) + list(link.diffraction_extra)
			if len(cluster) > len(combined):
				kept_ids = {(round(t.delay_s, 4), round(t.gain, 3)) for t in combined}
				leftover = 0.0
				for tap in cluster:
					key = (round(tap.delay_s, 4), round(tap.gain, 3))
					if key not in kept_ids:
						leftover += tap.gain * tap.gain
				send = min(0.65, send + 0.12 * math.sqrt(max(0.0, leftover)))
			self._set_reverb_send(source_id, send)
			self._set_source_velocity(source_id, loop.vx, loop.vy, loop.vz)

		# Wire first-reflection → late bus predelay (Spherion LateReverb).
		if min_predelay is not None:
			setter = getattr(self._world, "set_late_reverb_predelay_s", None)
			if setter is not None:
				setter(min(0.25, max(0.0, min_predelay)))

		# Push live door-coupled RT60 onto the shared late bus when it changes.
		if live_rt60 is not None:
			self._push_live_rt60(live_rt60)

		for source_id in list(self._obstruction_gain):
			if source_id not in seen:
				del self._obstruction_gain[source_id]
				self._acoustic_links.pop(source_id, None)

	def clear_acoustic_obstruction(self) -> None:
		"""Reset obstruction multipliers, reflection taps, and open LPF."""
		self._obstruction_gain.clear()
		self._acoustic_links.clear()
		self._last_live_rt60_s = -1.0
		self._last_live_wet = -1.0
		if self._world is None:
			return
		for loop in self._loops:
			self._world.set_gain(loop.entity_id, self._effective_loop_gain(loop))
			self._set_dry_path_gain(loop.entity_id, 1.0)
			self._set_obstruction_lowpass(loop.entity_id, 20_000.0)
			self._set_reflection_taps_clear(loop.entity_id)
			self._set_reverb_send(loop.entity_id, 0.0)

	def acoustic_debug_lines(self) -> list[str]:
		"""Return short HUD lines describing each source path mode."""
		lines: list[str] = []
		# Header: live RT60 / predelay so door-open retune is visible while tuning.
		if self._last_live_rt60_s > 0.0:
			lines.append(
				f"RT60 {self._last_live_rt60_s:.2f}s  WET {self._last_live_wet:.2f}"
			)
		for loop in self._loops:
			link = self._acoustic_links.get(loop.entity_id)
			if link is None:
				continue
			label = loop.asset_key[:8].upper()
			lines.append(
				f"{label} {link.path_mode.upper()} "
				f"G{link.direct_gain:.2f}/{link.transmission_gain:.2f} "
				f"D{link.diffraction_gain:.2f}@{link.diffraction_delay_s*1000:.0f}ms "
				f"R{link.reflection_gain:.2f}x{len(link.reflection_taps)} "
				f"V{link.reverb_send:.2f} PD{link.reverb_predelay_s*1000:.0f}ms"
			)
		return lines[:7]

	def _push_live_rt60(self, rt60_s: float) -> None:
		"""Update Spherion late-reverb RT60 when door-coupled Sabine/Eyring changes."""
		if self._world is None:
			return
		wet = max(wet_level_from_rt60(rt60_s), 0.35)
		# Skip tiny revisions to avoid thrashing the Schroeder bus every frame.
		if (
			abs(rt60_s - self._last_live_rt60_s) < 0.02
			and abs(wet - self._last_live_wet) < 0.02
		):
			return
		setter = getattr(self._world, "set_late_reverb", None)
		if setter is not None:
			setter(rt60_s, wet)
			self._last_live_rt60_s = rt60_s
			self._last_live_wet = wet

	def _effective_obstruction(self, link: AcousticLink) -> tuple[float, float]:
		"""Dry-path gain from direct + transmission only (never diffraction).

		Diffraction is a separate delayed tap — ducking dry as diffraction
		collapses ITD and sounds like a volume duck (Tsingos / UTD mapping).
		"""
		gain = max(link.direct_gain, link.transmission_gain)
		candidates = (
			(link.direct_gain, link.direct_lpf_hz),
			(link.transmission_gain, link.transmission_lpf_hz),
		)
		loudest = max(candidates, key=lambda item: item[0])
		lpf_hz = loudest[1]
		if link.direct_gain > 0.05 and link.transmission_gain > 0.05:
			lpf_hz = min(link.direct_lpf_hz, link.transmission_lpf_hz)
		# When fully blocked with no transmission, open the dry LPF (silent dry).
		if gain < 1e-4:
			lpf_hz = 20_000.0
		return gain, lpf_hz

	def _combined_early_taps(self, link: AcousticLink) -> list[ReflectionTap]:
		"""Merge specular taps with UTD diffracted paths (excess delay).

		Diffraction is a parallel delayed wet path — never folded into dry gain.
		Primary diffraction always reserves a slot; secondary edges fill remaining
		slots so complex shadows stay soft. Cap matches Spherion kMaxReflectionTaps=8.
		"""
		# Match Spherion ``kMaxReflectionTaps``.
		max_taps = 8
		specular = list(link.reflection_taps)
		diff_taps: list[ReflectionTap] = []
		if link.diffraction_gain > 0.02 and link.diffraction_delay_s >= 0.0:
			diff_taps.append(
				ReflectionTap(
					gain=link.diffraction_gain,
					delay_s=min(0.35, link.diffraction_delay_s),
					lpf_hz=link.diffraction_lpf_hz,
					bounce_order=0,
					is_diffuse=True,
				)
			)
		for extra in link.diffraction_extra:
			if extra.gain > 0.02:
				diff_taps.append(extra)
		# Structure-borne: dull LF tap with plate-wave delay (table radio / impacts).
		if link.structure_borne_gain > 0.02:
			diff_taps.append(
				ReflectionTap(
					gain=min(0.35, link.structure_borne_gain),
					delay_s=min(0.12, max(0.0, link.structure_borne_delay_s)),
					lpf_hz=link.structure_borne_lpf_hz,
					bounce_order=0,
					arrival_dir=(0.0, -1.0, 0.0),
					band_energy=(1.0, 0.7, 0.35, 0.15, 0.05, 0.02),
					is_diffuse=True,
				)
			)

		# Keep loudest specular taps, leaving slots for diffraction / structure.
		reserve = min(len(diff_taps), 3)
		max_specular = max(1, max_taps - reserve)
		ordered = sorted(specular, key=lambda t: -t.gain)
		merged: list[ReflectionTap] = []
		for tap in ordered:
			duplicate = False
			for index, existing in enumerate(merged):
				if abs(existing.delay_s - tap.delay_s) <= 0.0015:
					g = (existing.gain * existing.gain + tap.gain * tap.gain) ** 0.5
					merged[index] = ReflectionTap(
						gain=min(1.2, g),
						delay_s=min(existing.delay_s, tap.delay_s),
						lpf_hz=max(existing.lpf_hz, tap.lpf_hz),
						bounce_order=min(existing.bounce_order, tap.bounce_order),
						arrival_dir=existing.arrival_dir,
						band_energy=existing.band_energy,
						is_diffuse=existing.is_diffuse or tap.is_diffuse,
					)
					duplicate = True
					break
			if not duplicate:
				merged.append(tap)
			if len(merged) >= max_specular:
				break

		for diff_tap in diff_taps:
			placed = False
			for index, existing in enumerate(merged):
				if abs(existing.delay_s - diff_tap.delay_s) <= 0.0015:
					g = (existing.gain * existing.gain + diff_tap.gain * diff_tap.gain) ** 0.5
					merged[index] = ReflectionTap(
						gain=min(1.2, g),
						delay_s=min(existing.delay_s, diff_tap.delay_s),
						lpf_hz=max(existing.lpf_hz, diff_tap.lpf_hz),
						bounce_order=0,
						arrival_dir=diff_tap.arrival_dir,
						band_energy=diff_tap.band_energy,
						is_diffuse=True,
					)
					placed = True
					break
			if not placed and len(merged) < max_taps:
				merged.append(diff_tap)

		merged.sort(key=lambda t: t.delay_s)
		return merged[:max_taps]

	def _loop_by_id(self, entity_id: int) -> LevelLoop | None:
		"""Return the LevelLoop for ``entity_id``, or None."""
		for loop in self._loops:
			if loop.entity_id == entity_id:
				return loop
		return None

	def _set_obstruction_lowpass(self, entity_id: int, cutoff_hz: float) -> None:
		"""Push obstruction LPF to Spherion when the API is available."""
		if self._world is None:
			return
		setter = getattr(self._world, "set_obstruction_lowpass", None)
		if setter is not None:
			setter(entity_id, cutoff_hz)

	def _set_dry_path_gain(self, entity_id: int, gain: float) -> None:
		"""Push dry-path gain (LOS/transmission/diffraction) without killing reflections."""
		if self._world is None:
			return
		setter = getattr(self._world, "set_dry_path_gain", None)
		if setter is not None:
			setter(entity_id, gain)

	def _set_reflection_tap(
		self,
		entity_id: int,
		gain: float,
		delay_s: float,
		lpf_hz: float,
	) -> None:
		"""Push a single early-reflection delay tap to Spherion when available."""
		if self._world is None:
			return
		setter = getattr(self._world, "set_reflection_tap", None)
		if setter is not None:
			setter(entity_id, gain, delay_s, lpf_hz)

	def _set_reflection_taps(self, entity_id: int, link: AcousticLink) -> None:
		"""Push specular + diffracted early taps with direction and band gains."""
		if self._world is None:
			return
		extended = getattr(self._world, "set_reflection_taps_extended", None)
		multi = getattr(self._world, "set_reflection_taps", None)
		taps = self._combined_early_taps(link)
		if extended is not None and taps:
			gains: list[float] = []
			delays: list[float] = []
			lpfs: list[float] = []
			azimuths: list[float] = []
			elevations: list[float] = []
			band_low: list[float] = []
			band_mid: list[float] = []
			band_high: list[float] = []
			for tap in taps:
				az, el = world_dir_to_listener_angles(
					tap.arrival_dir,
					self._listener_yaw_rad,
					self._listener_pitch_rad,
				)
				bl, bm, bh = band_energy_to_three_band_gains(tap.band_energy)
				gains.append(tap.gain)
				delays.append(tap.delay_s)
				lpfs.append(tap.lpf_hz)
				azimuths.append(az)
				elevations.append(el)
				band_low.append(bl)
				band_mid.append(bm)
				band_high.append(bh)
			extended(
				entity_id,
				gains,
				delays,
				lpfs,
				azimuths,
				elevations,
				band_low,
				band_mid,
				band_high,
			)
			return
		if multi is not None and taps:
			gains = [t.gain for t in taps]
			delays = [t.delay_s for t in taps]
			lpfs = [t.lpf_hz for t in taps]
			multi(entity_id, gains, delays, lpfs)
			return
		# Legacy single-tap API: prefer loudest combined tap.
		if taps:
			best = max(taps, key=lambda t: t.gain)
			self._set_reflection_tap(entity_id, best.gain, best.delay_s, best.lpf_hz)
			return
		self._set_reflection_tap(
			entity_id,
			link.reflection_gain,
			link.reflection_delay_s,
			link.reflection_lpf_hz,
		)

	def _set_reflection_taps_clear(self, entity_id: int) -> None:
		"""Disable all early-reflection taps for one entity."""
		if self._world is None:
			return
		multi = getattr(self._world, "set_reflection_taps", None)
		if multi is not None:
			multi(entity_id, [], [], [])
			return
		self._set_reflection_tap(entity_id, 0.0, 0.0, 20_000.0)

	def _set_reverb_send(self, entity_id: int, send: float) -> None:
		"""Push late-reverb wet send to Spherion when available."""
		if self._world is None:
			return
		setter = getattr(self._world, "set_reverb_send", None)
		if setter is not None:
			setter(entity_id, send)

	def _set_source_velocity(self, entity_id: int, vx: float, vy: float, vz: float) -> None:
		"""Push source velocity for Doppler when available."""
		if self._world is None:
			return
		setter = getattr(self._world, "set_velocity", None)
		if setter is not None:
			setter(entity_id, vx, vy, vz)

	def _effective_loop_gain(self, loop: LevelLoop) -> float:
		"""Base × spatial × runtime scale (dry-path occlusion is separate)."""
		return loop.base_gain * self._audio_settings.spatial_gain * loop.runtime_gain_scale

	@property
	def master_gain_setting(self) -> float:
		"""Configured master gain from audio settings."""
		return self._audio_settings.master_gain

	@property
	def spatial_gain_setting(self) -> float:
		"""Configured spatial loop gain multiplier."""
		return self._audio_settings.spatial_gain

	@property
	def active(self) -> bool:
		"""Return True when Spherion output is running."""
		return self._world is not None and self._world.is_running

	@property
	def status_message(self) -> str | None:
		"""Human-readable startup result, or None when audio is live."""
		if self.active:
			return None
		return self._reason or "audio inactive"

	def menu_music_active(self) -> bool:
		"""Return True while title music is fading or still audible."""
		return self._menu_fading_in or self._menu_fading_out

	def credits_music_active(self) -> bool:
		"""Return True while credits music is fading or still audible."""
		return self._credits_fading_in or self._credits_fading_out

	def _menu_music_peak_gain(self) -> float:
		"""Peak direct-menu-music gain from settings."""
		return max(0.0, self._audio_settings.music_gain)

	def _focus_gain_target(self) -> float:
		"""Target master multiplier from focus state and the quiet-unfocus toggle."""
		if self._quiet_unfocused_enabled and not self._focused:
			return UNFOCUSED_MASTER_GAIN
		return 1.0

	def _apply_master_gain(self) -> None:
		"""Push the current focus-faded master gain to Spherion."""
		if self._world is None:
			return
		self._world.set_master_gain(self._audio_settings.master_gain * self._focus_gain_mult)

	def set_focus_quiet(self, focused: bool, quiet_when_unfocused: bool) -> None:
		"""Track focus so ``update_focus_quiet`` can duck master output smoothly."""
		self._focused = focused
		self._quiet_unfocused_enabled = quiet_when_unfocused

	def update_focus_quiet(self, dt: float) -> None:
		"""Ramp master gain toward the focus duck target."""
		if self._world is None:
			return

		target = self._focus_gain_target()
		if abs(self._focus_gain_mult - target) < 1e-4:
			if self._focus_gain_mult != target:
				self._focus_gain_mult = target
				self._apply_master_gain()
			return

		if UNFOCUSED_FADE_SEC <= 0.0 or dt <= 0.0:
			self._focus_gain_mult = target
		else:
			step = (dt / UNFOCUSED_FADE_SEC) * (1.0 - UNFOCUSED_MASTER_GAIN)
			if self._focus_gain_mult < target:
				self._focus_gain_mult = min(target, self._focus_gain_mult + step)
			else:
				self._focus_gain_mult = max(target, self._focus_gain_mult - step)

		self._apply_master_gain()

	def apply_settings(self, audio: AudioSettings) -> None:
		"""Apply live audio toggles (gain, distance, HRTF reload)."""
		self._audio_settings = audio
		self._spatial_gain = audio.spatial_gain
		if self._world is None:
			return

		self._apply_master_gain()
		self._world.engine.set_max_audible_distance(audio.max_audible_distance_m)

		for loop in self.level_loops:
			self._world.set_gain(loop.entity_id, self._effective_loop_gain(loop))

		if self._menu_music is not None:
			self._menu_music.set_gain(self._menu_music_peak_gain() * self._menu_fade)
		if self._credits_music is not None:
			self._credits_music.set_gain(self._menu_music_peak_gain() * self._credits_fade)

		if audio.hrtf_enabled:
			path = resolve_hrtf_path(audio.hrtf_filename or DEFAULT_HRTF_FILENAME)
			if path is not None:
				path_str = str(path)
				if path_str != self._loaded_hrtf_path:
					self._world.load_hrtf(path_str)
					self._loaded_hrtf_path = path_str
		else:
			self._loaded_hrtf_path = None

		rt60_s, wet = _apply_room_acoustics(self._world.engine, self._room, audio)
		self._last_live_rt60_s = rt60_s
		self._last_live_wet = wet

	def set_gameplay_sounds_active(self, active: bool) -> None:
		"""Mute or restore in-level spatial loops (e.g. while on the main menu)."""
		if self._world is None:
			return
		for loop in self.level_loops:
			self._world.set_active(loop.entity_id, active)

	def set_loop_position(
		self,
		entity_id: int,
		x: float,
		y: float,
		z: float,
		*,
		dt: float = 0.0,
	) -> None:
		"""Move a spatial loop in world space (used for health drift FX).

		Estimates source velocity from successive positions and pushes it to
		Spherion for Doppler when ``dt`` is positive.
		"""
		if self._world is None:
			return
		loop = self._loop_by_id(entity_id)
		if loop is not None and dt > 1e-4:
			loop.vx = (x - loop.last_x) / dt
			loop.vy = (y - loop.last_y) / dt
			loop.vz = (z - loop.last_z) / dt
			loop.last_x, loop.last_y, loop.last_z = x, y, z
			self._set_source_velocity(entity_id, loop.vx, loop.vy, loop.vz)
		elif loop is not None:
			loop.vx = loop.vy = loop.vz = 0.0
			loop.last_x, loop.last_y, loop.last_z = x, y, z
			self._set_source_velocity(entity_id, 0.0, 0.0, 0.0)
		self._world.set_position(entity_id, x, y, z)

	def set_loop_gain(self, entity_id: int, gain: float) -> None:
		"""Set absolute gain on one spatial loop (stores scale; obstruction still applies)."""
		if self._world is None:
			return
		loop = self._loop_by_id(entity_id)
		if loop is None:
			self._world.set_gain(entity_id, gain)
			return
		denom = max(1e-6, loop.base_gain * self._audio_settings.spatial_gain)
		loop.runtime_gain_scale = max(0.0, gain / denom)
		self._world.set_gain(entity_id, self._effective_loop_gain(loop))

	def spawn_loop(
		self,
		asset_key: str,
		x: float,
		y: float,
		z: float,
		*,
		gain: float,
		phantom: bool = True,
	) -> int | None:
		"""Spawn an extra spatial loop (hallucinated sources)."""
		if self._world is None:
			return None
		path = self._asset_paths.get(asset_key)
		if path is None or not path.is_file():
			return None
		entity_id = self._world.spawn(
			str(path),
			x,
			y,
			z,
			loop=True,
			gain=gain * self._spatial_gain,
		)
		# Phantoms inherit asset facing/size so they beam like the real source.
		pat, axis, radius = _authored_speaker(asset_key)
		self._loops.append(
			LevelLoop(
				entity_id=entity_id,
				asset_key=asset_key,
				base_x=x,
				base_y=y,
				base_z=z,
				base_gain=gain,
				phantom=phantom,
				last_x=x,
				last_y=y,
				last_z=z,
				directivity=pat,
				directivity_axis=axis,
				speaker_radius_m=radius,
			)
		)
		return entity_id

	def despawn_loop(self, entity_id: int) -> None:
		"""Remove a spatial loop by entity id."""
		if self._world is None:
			return
		self._world.despawn(entity_id)
		self._loops = [loop for loop in self._loops if loop.entity_id != entity_id]

	def spawn_direct_ring(self):
		"""Create the direct stereo ringing source used by low-health FX."""
		if self._world is None:
			return None
		path = self._asset_paths.get("ring")
		if path is None or not path.is_file():
			return None
		try:
			return self._world.spawn_direct(str(path), loop=True, gain=0.0, active=False)
		except RuntimeError:
			return None

	def _init_repair_audio(self) -> None:
		"""Pre-spawn repair proximity bins and the clunk one-shot (no runtime file writes)."""
		if self._world is None:
			return
		try:
			bin_paths = ensure_repair_tone_bank()
			clunk_path = ensure_repair_clunk_wav()
		except OSError:
			return

		self._repair_bins = []
		for path in bin_paths:
			try:
				entity = self._world.spawn_direct(str(path), loop=True, gain=0.0, active=True)
			except RuntimeError:
				continue
			self._repair_bins.append(entity)

		try:
			self._repair_clunk = self._world.spawn_direct(
				str(clunk_path),
				loop=False,
				gain=0.0,
				active=False,
			)
		except RuntimeError:
			self._repair_clunk = None

	def begin_repair_mode(self) -> None:
		"""Mute world audio and start the blind repair proximity drone."""
		if self._world is None:
			return
		self._repair_mode = True
		for loop in self._loops:
			self._world.set_active(loop.entity_id, False)
		self.update_repair_proximity(0.0)

	def end_repair_mode(self) -> None:
		"""Stop repair tones and restore in-level spatial loops."""
		if self._world is None:
			return
		self._repair_mode = False
		for entity in self._repair_bins:
			entity.set_gain(0.0)
		if self._repair_clunk is not None:
			self._repair_clunk.set_gain(0.0)
			self._repair_clunk.set_active(False)
		for loop in self._loops:
			if loop.phantom:
				continue
			self._world.set_active(loop.entity_id, True)
			self._world.set_gain(loop.entity_id, self._effective_loop_gain(loop))

	def update_repair_proximity(self, proximity: float) -> None:
		"""Crossfade pre-baked bins — smooth pitch without respawning sources."""
		if not self._repair_mode or not self._repair_bins:
			return

		shaped = max(0.0, min(1.0, proximity)) ** _REPAIR_PROXIMITY_EXP
		scaled = shaped * (len(self._repair_bins) - 1)
		low = int(math.floor(scaled))
		high = min(low + 1, len(self._repair_bins) - 1)
		frac = scaled - low

		for index, entity in enumerate(self._repair_bins):
			if high == low and index == low:
				gain = _REPAIR_TONE_GAIN
			elif index == low:
				gain = (1.0 - frac) * _REPAIR_TONE_GAIN
			elif index == high:
				gain = frac * _REPAIR_TONE_GAIN
			else:
				gain = 0.0
			entity.set_gain(gain)

	def play_repair_clunk(self) -> None:
		"""Play the mechanical clunk for a successful invisible button press."""
		if self._repair_clunk is None:
			return
		self._repair_clunk.reset_playhead()
		self._repair_clunk.set_gain(_REPAIR_CLUNK_GAIN)
		self._repair_clunk.set_active(True)

	def on_credits_show(self, music_filename: str, *, start_seconds: float) -> None:
		"""Swap title music for the credits roll soundtrack."""
		if self._credits_music is None:
			credits_path = _credits_music_path(music_filename)
			if credits_path is not None and self._world is not None:
				try:
					self._credits_music = self._world.spawn_direct(
						str(credits_path),
						loop=True,
						gain=0.0,
						active=False,
					)
				except RuntimeError:
					self._credits_music = None

		if self._menu_music is not None:
			self._menu_fading_in = False
			self._menu_fading_out = False
			self._menu_fade = 0.0
			self._menu_music.set_gain(0.0)
			self._menu_music.set_active(False)

		if self._credits_music is None:
			return

		self.set_gameplay_sounds_active(False)
		self._credits_music.seek_to_seconds(start_seconds, self._sample_rate_hz)
		self._credits_music.set_active(True)
		self._credits_fade = 0.0
		self._credits_fading_in = True
		self._credits_fading_out = False
		self._credits_restore_menu = True
		self._credits_music.set_gain(0.0)

	def on_credits_hide(self) -> None:
		"""Fade credits music out and restore title music."""
		if self._credits_music is None:
			if self._credits_restore_menu:
				self._credits_restore_menu = False
				self.on_main_menu_show()
			return

		if self._credits_fading_in:
			pass
		elif self._credits_fade <= 0.0:
			self._credits_fade = 1.0
		self._credits_fading_in = False
		self._credits_fading_out = True

	def update_credits_music(self, dt: float) -> None:
		"""Advance credits music gain ramps."""
		if self._credits_music is None:
			return

		if self._credits_fading_in:
			if CREDITS_MUSIC_FADE_IN_SEC <= 0.0:
				self._credits_fade = 1.0
			else:
				self._credits_fade = min(1.0, self._credits_fade + (dt / CREDITS_MUSIC_FADE_IN_SEC))
			self._credits_music.set_gain(self._menu_music_peak_gain() * self._credits_fade)
			if self._credits_fade >= 1.0:
				self._credits_fading_in = False
			return

		if self._credits_fading_out:
			if CREDITS_MUSIC_FADE_OUT_SEC <= 0.0:
				self._credits_fade = 0.0
			else:
				self._credits_fade = max(0.0, self._credits_fade - (dt / CREDITS_MUSIC_FADE_OUT_SEC))
			self._credits_music.set_gain(self._menu_music_peak_gain() * self._credits_fade)
			if self._credits_fade <= 0.0:
				self._credits_fading_out = False
				self._credits_music.set_active(False)
				if self._credits_restore_menu:
					self._credits_restore_menu = False
					self.on_main_menu_show()

	def on_main_menu_show(self) -> None:
		"""Start main-menu music at 1:55 with a volume fade-in."""
		if self._credits_music is not None:
			self._credits_fading_in = False
			self._credits_fading_out = False
			self._credits_fade = 0.0
			self._credits_restore_menu = False
			self._credits_music.set_gain(0.0)
			self._credits_music.set_active(False)
		if self._menu_music is None:
			return
		self.set_gameplay_sounds_active(False)
		self._menu_music.seek_to_seconds(MENU_MUSIC_START_SEC, self._sample_rate_hz)
		self._menu_music.set_active(True)
		self._menu_fade = 0.0
		self._menu_fading_in = True
		self._menu_fading_out = False
		self._menu_music.set_gain(0.0)

	def on_main_menu_hide(self) -> None:
		"""Fade menu music out and enable in-level spatial audio."""
		if self._menu_music is None:
			self.set_gameplay_sounds_active(True)
			return
		if self._menu_fading_in:
			pass
		elif self._menu_fade <= 0.0:
			self._menu_fade = 1.0
		self._menu_fading_in = False
		self._menu_fading_out = True
		self.set_gameplay_sounds_active(True)

	def update_menu_music(self, dt: float) -> None:
		"""Advance main-menu music gain ramps."""
		if self._menu_music is None:
			return

		if self._menu_fading_in:
			if MENU_MUSIC_FADE_IN_SEC <= 0.0:
				self._menu_fade = 1.0
			else:
				self._menu_fade = min(1.0, self._menu_fade + (dt / MENU_MUSIC_FADE_IN_SEC))
			self._menu_music.set_gain(self._menu_music_peak_gain() * self._menu_fade)
			if self._menu_fade >= 1.0:
				self._menu_fading_in = False
			return

		if self._menu_fading_out:
			if MENU_MUSIC_FADE_OUT_SEC <= 0.0:
				self._menu_fade = 0.0
			else:
				self._menu_fade = max(0.0, self._menu_fade - (dt / MENU_MUSIC_FADE_OUT_SEC))
			self._menu_music.set_gain(self._menu_music_peak_gain() * self._menu_fade)
			if self._menu_fade <= 0.0:
				self._menu_fading_out = False
				self._menu_music.set_active(False)

	def sync_camera(
		self,
		camera: Camera,
		*,
		listener_velocity_mps: tuple[float, float, float] | None = None,
	) -> None:
		"""Mirror listener eye pose (and optional velocity) from the NULLSIGHT camera."""
		if self._world is None:
			return
		self._listener_yaw_rad = camera.yaw
		self._listener_pitch_rad = camera.pitch
		self._world.sync_camera(camera, velocity_mps=listener_velocity_mps)

	def shutdown(self) -> None:
		"""Stop device output and release the audio world."""
		if self._world is None:
			return
		engine = self._world.engine
		engine.clear_atmospheric_provider()
		engine.set_use_atmospheric_conditions(False)
		self._world.stop()
		self._world = None
		self._menu_music = None
		self._credits_music = None
		self._loops.clear()
		self._loaded_hrtf_path = None
		self._obstruction_gain.clear()
		self._acoustic_links.clear()
