"""Tooltip copy and FPS impact ratings for pause-menu settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Impact = Literal["LOW", "MED", "HIGH"]


@dataclass(frozen=True)
class MenuTooltip:
	"""Description lines plus FPS impact at full quality."""

	lines: tuple[str, ...]
	impact: Impact


def tooltip_body_lines(tip: MenuTooltip) -> tuple[str, ...]:
	"""Return description plus a final PERF IMPACT line."""
	impact_line = {
		"LOW": "PERF IMPACT LOW",
		"MED": "PERF IMPACT MED",
		"HIGH": "PERF IMPACT HIGH",
	}[tip.impact]
	return (*tip.lines, impact_line)


# FPS impact = cost at full quality (toggle OFF for perf opts, ON for overlays).
MENU_TOOLTIPS: dict[str, MenuTooltip] = {
	"toggle_vsync": MenuTooltip(
		(
			"CAP FRAME RATE TO MONITOR",
			"CAN LOCK TO HALF REFRESH",
			"IF FRAMES ARE TOO SLOW",
		),
		"MED",
	),
	"toggle_double_buffer": MenuTooltip(
		(
			"OVERLAP GDI PRESENT WITH",
			"NEXT FRAME ON CPU BACKEND",
			"OFF FORCES SYNC MAIN BLIT",
		),
		"MED",
	),
	"quiet_unfocused": MenuTooltip(
		(
			"FADE MASTER VOLUME TO 20%",
			"WHEN WINDOW LOSES FOCUS",
		),
		"LOW",
	),
	"cap_fps_unfocused": MenuTooltip(
		(
			"LIMIT MAIN LOOP TO 10 FPS",
			"WHILE WINDOW IS UNFOCUSED",
		),
		"LOW",
	),
	"osd": MenuTooltip(
		(
			"FPS AND PHASE TIMING LINES",
			"EACH ENABLED LINE ADDS UI",
		),
		"LOW",
	),
	"graphics": MenuTooltip(
		(
			"FADE AND LIVE OCCLUSION",
			"QUALITY VS SPEED TOGGLES",
		),
		"MED",
	),
	"fps_cur": MenuTooltip(("INSTANT FRAMES PER SECOND",), "LOW"),
	"fps_avg": MenuTooltip(("ROLLING 6S AVERAGE FPS",), "LOW"),
	"fps_min": MenuTooltip(("LOWEST FPS IN LAST 6S",), "LOW"),
	"fps_max": MenuTooltip(("HIGHEST FPS IN LAST 6S",), "LOW"),
	"inp_ms": MenuTooltip(("INPUT AND MENU PHASE MS",), "LOW"),
	"sim_ms": MenuTooltip(("CAMERA AND SCAN UPDATE MS",), "LOW"),
	"scene_ms": MenuTooltip(("WIREFRAME SCENE PHASE MS",), "LOW"),
	"ui_ms": MenuTooltip(("OSD AND MENU DRAW MS",), "LOW"),
	"rast_ms": MenuTooltip(("CPU RASTER PHASE MS",), "LOW"),
	"rec_ms": MenuTooltip(("COMMAND QUEUE RECORD MS",), "LOW"),
	"pre_ms": MenuTooltip(("PRESENT AND VSYNC WAIT MS",), "LOW"),
	"acoustic_path": MenuTooltip(
		(
			"SHOW RAY PATH MODE PER SOURCE",
			"CLEAR PARTIAL TRANSMISSION",
			"DIFFRACTION LABELS",
		),
		"LOW",
	),
	"menu_scale": MenuTooltip(
		(
			"TITLE AND PAUSE MENU UI SIZE",
			"0.75 SMALLER  2.0 LARGER",
		),
		"LOW",
	),
	"coarse_fade": MenuTooltip(
		(
			"ON USES 4 FADE BRIGHTNESS STEPS",
			"OFF USES 16 SMOOTHER FADE",
		),
		"HIGH",
	),
	"dense_coarse_fade": MenuTooltip(
		(
			"ON COARSENS FADE WHEN MANY",
			"SEGMENTS ARE ON SCREEN",
		),
		"MED",
	),
	"throttle_live_occlusion": MenuTooltip(
		(
			"ON SKIPS SOME LIVE OCCLUSION",
			"UPDATES WHILE YOU MOVE",
		),
		"HIGH",
	),
	"wide_occlusion_cache": MenuTooltip(
		(
			"ON REUSES OCCLUSION ACROSS",
			"LARGER EYE MOVEMENTS",
		),
		"MED",
	),
	"skip_screen_clip_moving": MenuTooltip(
		(
			"ON SKIPS SCREEN CLIP PASS",
			"WHILE YOU ARE MOVING",
		),
		"MED",
	),
	"simple_fade_moving": MenuTooltip(
		(
			"ON ONE FADE BUCKET PER EDGE",
			"SPAN WHILE MOVING",
		),
		"HIGH",
	),
	"audio": MenuTooltip(
		(
			"spatial audio and hrtf",
			"use headphones for 3D...",
			"wait where is my capslock?"
		),
		"LOW",
	),
	"enabled": MenuTooltip(
		(
			"MASTER SPATIAL AUDIO SWITCH",
			"RESTART REQUIRED TO APPLY",
		),
		"LOW",
	),
	"hrtf_enabled": MenuTooltip(
		(
			"BUNDLED SUBJECT 003 HRTF",
			"TURN ON RELOADS LIVE",
			"FULL OFF NEEDS RESTART",
		),
		"MED",
	),
	"acoustic_obstruction": MenuTooltip(
		(
			"RAYTRACE WALLS FOR SOUND",
			"DUCKS AND MUFFLES WHEN BLOCKED",
			"USES FULL LEVEL GEOMETRY",
		),
		"MED",
	),
	"enable_doppler": MenuTooltip(
		(
			"PITCH SHIFT FROM LISTENER SPEED",
			"SPRINT TO HEAR THE EFFECT",
			"RESTART REQUIRED TO APPLY",
		),
		"MED",
	),
	"enable_hf_air_absorption": MenuTooltip(
		(
			"ISO HF AIR LOSS VS DISTANCE",
			"SUBTLE MUFFLE AT RANGE",
			"RESTART REQUIRED TO APPLY",
		),
		"MED",
	),
	"enable_propagation_delay": MenuTooltip(
		(
			"DISTANCE-BASED SOUND TRAVEL",
			"DELAY LINES CAPPED AT 50 MS",
			"RESTART REQUIRED TO APPLY",
		),
		"HIGH",
	),
	"enable_head_shadow": MenuTooltip(
		(
			"HF HEAD SHADOW BEYOND HRTF",
			"WOODWORTH SPHERICAL-HEAD ILD",
			"RESTART REQUIRED TO APPLY",
		),
		"MED",
	),
	"use_atmospheric_conditions": MenuTooltip(
		(
			"LIVE OUTDOOR AIR ON LARGE LEVEL",
			"SMALL ROOMS STAY INDOOR",
			"TOGGLES LIVE IN SESSION",
		),
		"MED",
	),
	"spatial_gain": MenuTooltip(
		(
			"AMBISONIC SOURCE VOLUME",
			"DRAG SLIDER TO ADJUST",
		),
		"LOW",
	),
	"master_gain": MenuTooltip(
		(
			"OVERALL ENGINE OUTPUT",
			"AFFECTS ALL AUDIO BUSES",
		),
		"LOW",
	),
	"music_gain": MenuTooltip(
		(
			"TITLE SCREEN MUSIC LEVEL",
			"DOES NOT AFFECT SPATIAL",
		),
		"LOW",
	),
}