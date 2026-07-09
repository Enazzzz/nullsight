"""Load and save NULLSIGHT player settings under the Windows AppData folder."""

from __future__ import annotations

import json
import os
from dataclasses import fields
from pathlib import Path

from menu_layout import MENU_SCALE_MAX, MENU_SCALE_MIN
from settings import AudioSettings, GameSettings, OsdSettings, PerfSettings

_SETTINGS_VERSION = 8
_APP_FOLDER = "NULLSIGHT"
_FILE_NAME = "settings.json"


def settings_file_path() -> Path:
	"""Return ``%LOCALAPPDATA%\\NULLSIGHT\\settings.json`` (or platform fallback)."""
	local_app_data = os.environ.get("LOCALAPPDATA")
	if local_app_data:
		root = Path(local_app_data)
	else:
		root = Path.home() / "AppData" / "Local"
	return root / _APP_FOLDER / _FILE_NAME


def _osd_to_dict(osd: OsdSettings) -> dict[str, bool]:
	"""Serialize OSD toggles."""
	return {field.name: bool(getattr(osd, field.name)) for field in fields(OsdSettings)}


def _perf_to_dict(perf: PerfSettings) -> dict[str, bool]:
	"""Serialize graphics perf toggles."""
	return {field.name: bool(getattr(perf, field.name)) for field in fields(PerfSettings)}


def _audio_to_dict(audio: AudioSettings) -> dict:
	"""Serialize spatial audio settings."""
	return {
		"enabled": bool(audio.enabled),
		"hrtf_enabled": bool(audio.hrtf_enabled),
		"hrtf_filename": str(audio.hrtf_filename),
		"order": int(audio.order),
		"block_size": int(audio.block_size),
		"master_gain": float(audio.master_gain),
		"spatial_gain": float(audio.spatial_gain),
		"music_gain": float(audio.music_gain),
		"max_audible_distance_m": float(audio.max_audible_distance_m),
		"enable_hf_air_absorption": bool(audio.enable_hf_air_absorption),
		"enable_doppler": bool(audio.enable_doppler),
		"enable_propagation_delay": bool(audio.enable_propagation_delay),
		"use_atmospheric_conditions": bool(audio.use_atmospheric_conditions),
		"atmospheric_temperature_c": float(audio.atmospheric_temperature_c),
		"atmospheric_relative_humidity_percent": float(audio.atmospheric_relative_humidity_percent),
		"acoustic_obstruction": bool(audio.acoustic_obstruction),
	}


def _osd_from_dict(data: dict | None) -> OsdSettings:
	"""Build OSD settings, ignoring unknown keys."""
	osd = OsdSettings()
	if not data:
		return osd
	for field in fields(OsdSettings):
		if field.name in data:
			setattr(osd, field.name, bool(data[field.name]))
	# Migrate old combined "game_ms" toggle to the split phases.
	if data.get("game_ms"):
		osd.inp_ms = True
		osd.sim_ms = True
	return osd


def _perf_from_dict(data: dict, legacy_tier: str | None) -> PerfSettings:
	"""Build perf settings from stored JSON or legacy performance preset."""
	perf = PerfSettings()
	perf_data = data.get("perf")
	if isinstance(perf_data, dict):
		for field in fields(PerfSettings):
			if field.name in perf_data:
				setattr(perf, field.name, bool(perf_data[field.name]))
		return perf

	tier = legacy_tier or "balanced"
	if tier == "low":
		perf.coarse_fade = True
		perf.dense_coarse_fade = True
		perf.throttle_live_occlusion = True
		perf.wide_occlusion_cache = True
		perf.skip_screen_clip_moving = True
		perf.simple_fade_moving = True
	elif tier == "high":
		perf.throttle_live_occlusion = False
		perf.wide_occlusion_cache = False
		perf.simple_fade_moving = False
	return perf


def _audio_from_dict(data: dict | None) -> AudioSettings:
	"""Build audio settings, ignoring unknown keys."""
	audio = AudioSettings()
	if not data:
		return audio
	if "enabled" in data:
		audio.enabled = bool(data["enabled"])
	if "hrtf_enabled" in data:
		audio.hrtf_enabled = bool(data["hrtf_enabled"])
	if "hrtf_filename" in data:
		audio.hrtf_filename = str(data["hrtf_filename"])
	if "order" in data:
		audio.order = int(data["order"])
	if "block_size" in data:
		audio.block_size = int(data["block_size"])
	if "master_gain" in data:
		audio.master_gain = float(data["master_gain"])
	if "spatial_gain" in data:
		audio.spatial_gain = float(data["spatial_gain"])
	if "music_gain" in data:
		audio.music_gain = float(data["music_gain"])
	if "max_audible_distance_m" in data:
		audio.max_audible_distance_m = float(data["max_audible_distance_m"])
	if "enable_hf_air_absorption" in data:
		audio.enable_hf_air_absorption = bool(data["enable_hf_air_absorption"])
	if "enable_doppler" in data:
		audio.enable_doppler = bool(data["enable_doppler"])
	if "enable_propagation_delay" in data:
		audio.enable_propagation_delay = bool(data["enable_propagation_delay"])
	if "use_atmospheric_conditions" in data:
		audio.use_atmospheric_conditions = bool(data["use_atmospheric_conditions"])
	if "atmospheric_temperature_c" in data:
		audio.atmospheric_temperature_c = float(data["atmospheric_temperature_c"])
	if "atmospheric_relative_humidity_percent" in data:
		audio.atmospheric_relative_humidity_percent = float(
			data["atmospheric_relative_humidity_percent"]
		)
	if "acoustic_obstruction" in data:
		audio.acoustic_obstruction = bool(data["acoustic_obstruction"])
	return audio


def settings_to_dict(settings: GameSettings) -> dict:
	"""Convert runtime settings to a JSON-serializable mapping."""
	return {
		"version": _SETTINGS_VERSION,
		"vsync": settings.vsync,
		"double_buffered_present": settings.double_buffered_present,
		"menu_scale": float(settings.menu_scale),
		"quiet_unfocused": bool(settings.quiet_unfocused),
		"cap_fps_unfocused": bool(settings.cap_fps_unfocused),
		"perf": _perf_to_dict(settings.perf),
		"osd": _osd_to_dict(settings.osd),
		"audio": _audio_to_dict(settings.audio),
	}


def settings_from_dict(data: dict) -> GameSettings:
	"""Parse stored JSON into ``GameSettings``, falling back to defaults."""
	defaults = GameSettings()
	if not data:
		return defaults

	osd = _osd_from_dict(data.get("osd"))
	legacy_tier = data.get("performance")
	if isinstance(legacy_tier, str):
		legacy_tier = legacy_tier if legacy_tier in ("low", "balanced", "high") else "balanced"
	else:
		legacy_tier = None

	perf = _perf_from_dict(data, legacy_tier)
	audio = _audio_from_dict(data.get("audio"))
	menu_scale = float(data.get("menu_scale", defaults.menu_scale))
	menu_scale = max(MENU_SCALE_MIN, min(MENU_SCALE_MAX, menu_scale))
	settings = GameSettings(
		osd=osd,
		perf=perf,
		audio=audio,
		vsync=bool(data.get("vsync", defaults.vsync)),
		double_buffered_present=bool(
			data.get("double_buffered_present", defaults.double_buffered_present)
		),
		menu_scale=menu_scale,
		quiet_unfocused=bool(data.get("quiet_unfocused", defaults.quiet_unfocused)),
		cap_fps_unfocused=bool(data.get("cap_fps_unfocused", defaults.cap_fps_unfocused)),
	)
	# Async present must stay on with vsync — sync GDI was locking many panels at ~82 FPS.
	if settings.vsync and not settings.double_buffered_present:
		settings.double_buffered_present = True
	return settings


def load_settings() -> GameSettings:
	"""Read settings from disk, or return defaults when missing or invalid."""
	path = settings_file_path()
	try:
		raw = path.read_text(encoding="utf-8")
		data = json.loads(raw)
	except FileNotFoundError:
		return GameSettings()
	except (OSError, json.JSONDecodeError, TypeError):
		return GameSettings()

	if not isinstance(data, dict):
		return GameSettings()
	return settings_from_dict(data)


def save_settings(settings: GameSettings) -> None:
	"""Write settings atomically to the AppData JSON file."""
	path = settings_file_path()
	path.parent.mkdir(parents=True, exist_ok=True)

	payload = json.dumps(settings_to_dict(settings), indent="\t")
	tmp_path = path.with_suffix(".json.tmp")
	tmp_path.write_text(payload + "\n", encoding="utf-8")
	tmp_path.replace(path)
