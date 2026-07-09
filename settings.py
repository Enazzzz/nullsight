"""Runtime game settings toggled from the pause menu."""

from dataclasses import dataclass, field

from menu_layout import DEFAULT_MENU_SCALE


@dataclass
class OsdSettings:
	"""On-screen display lines — each stat toggled independently."""

	fps_avg: bool = False
	fps_min: bool = False
	fps_max: bool = False
	fps_cur: bool = False
	inp_ms: bool = False
	sim_ms: bool = False
	scene_ms: bool = False
	ui_ms: bool = False
	rast_ms: bool = False
	rec_ms: bool = False
	pre_ms: bool = False
	# Acoustic path-mode debug lines (ray obstruction HUD).
	acoustic_path: bool = False

	def any_enabled(self) -> bool:
		"""Return True when at least one OSD line should draw."""
		return (
			self.fps_avg
			or self.fps_min
			or self.fps_max
			or self.fps_cur
			or self.inp_ms
			or self.sim_ms
			or self.scene_ms
			or self.ui_ms
			or self.rast_ms
			or self.rec_ms
			or self.pre_ms
			or self.acoustic_path
		)


@dataclass
class PerfSettings:
	"""Per-frame quality and occlusion toggles (Graphics menu)."""

	# Fewer fade brightness buckets (4 instead of 16).
	coarse_fade: bool = False
	# Coarse buckets when many segments are visible.
	dense_coarse_fade: bool = False
	# Skip live-occlusion passes on some frames while moving.
	throttle_live_occlusion: bool = True
	# Reuse occlusion results across larger eye steps.
	wide_occlusion_cache: bool = True
	# Skip screen-interval clipping while moving.
	skip_screen_clip_moving: bool = False
	# One fade bucket per edge span while moving.
	simple_fade_moving: bool = True


@dataclass
class AudioSettings:
	"""Spherion spatial audio toggles."""

	enabled: bool = True
	hrtf_enabled: bool = True
	hrtf_filename: str = "subject_003_48k.sofa"
	order: int = 3
	block_size: int = 256
	master_gain: float = 1.0
	spatial_gain: float = 5.0
	music_gain: float = 0.75
	max_audible_distance_m: float = 80.0
	# Spherion v0.2 physics — applied at audio session init (EngineConfig).
	enable_hf_air_absorption: bool = True
	enable_doppler: bool = True
	enable_propagation_delay: bool = True
	enable_head_shadow: bool = True
	# Live outdoor air model (large levels only; small rooms force indoor).
	use_atmospheric_conditions: bool = True
	atmospheric_temperature_c: float = 20.0
	atmospheric_relative_humidity_percent: float = 50.0
	# Raytraced direct-path obstruction (gain duck + LPF through walls).
	acoustic_obstruction: bool = True


@dataclass
class GameSettings:
	"""Player preferences that apply for the current session."""

	osd: OsdSettings = field(default_factory=OsdSettings)
	perf: PerfSettings = field(default_factory=PerfSettings)
	audio: AudioSettings = field(default_factory=AudioSettings)
	vsync: bool = True
	# Overlap GDI present with next frame raster (guide §7 double-buffered present).
	double_buffered_present: bool = True
	# Title screen and pause/settings UI scale (0.75–2.0).
	menu_scale: float = DEFAULT_MENU_SCALE
	# Lower master volume while the game window is in the background.
	quiet_unfocused: bool = True
	# Limit the main loop to 10 FPS while unfocused.
	cap_fps_unfocused: bool = True
