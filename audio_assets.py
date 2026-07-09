"""Procedural placeholder loops for NULLSIGHT spatial audio."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

# Project-root assets folder (created on demand).
ASSETS_DIR = Path(__file__).resolve().parent / "assets"

# Bundled measured HRTF (48 kHz SOFA).
DEFAULT_HRTF_FILENAME = "subject_003_48k.sofa"

# Default loop length — one second at 48 kHz.
_DEFAULT_FRAMES = 48_000
# Scanner repair proximity bins — pre-baked so pitch never respawns mid-frame.
REPAIR_BIN_COUNT = 16
REPAIR_TONE_MIN_HZ = 104.0
REPAIR_TONE_MAX_HZ = 960.0


def _write_stereo_loop_wav(
	path: Path,
	*,
	frames: int,
	frequency_hz: float,
	sample_rate: int = 48_000,
	amplitude: float = 0.18,
) -> None:
	"""Write a short stereo sine loop suitable for Spherion decode tests."""
	path.parent.mkdir(parents=True, exist_ok=True)
	samples: list[float] = []
	for index in range(frames):
		phase = (2.0 * math.pi * frequency_hz * index) / sample_rate
		value = amplitude * math.sin(phase)
		samples.append(value)
		samples.append(value)

	with wave.open(str(path), "w") as wav_file:
		wav_file.setnchannels(2)
		wav_file.setsampwidth(2)
		wav_file.setframerate(sample_rate)
		packed = b"".join(
			struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32_767))
			for sample in samples
		)
		wav_file.writeframes(packed)


def ensure_bundled_audio_assets() -> dict[str, Path]:
	"""Create default beacon/hum loops; return logical name → path."""
	specs = {
		"beacon": {"frequency_hz": 880.0, "amplitude": 0.42},
		"hum": {"frequency_hz": 110.0, "amplitude": 0.34},
		"ring": {"frequency_hz": 3100.0, "amplitude": 0.10},
	}
	paths: dict[str, Path] = {}
	for name, spec in specs.items():
		path = ASSETS_DIR / f"{name}.wav"
		# Refresh procedural loops so amplitude tweaks apply without manual deletes.
		_write_stereo_loop_wav(
			path,
			frames=_DEFAULT_FRAMES,
			frequency_hz=spec["frequency_hz"],
			amplitude=spec["amplitude"],
		)
		paths[name] = path
	# Bundled level media (not procedurally regenerated).
	for media_name, filename in (
		("old_radio", "old-radio.wav"),
	):
		media_path = ASSETS_DIR / filename
		if media_path.is_file():
			paths[media_name] = media_path
	return paths


def _unpleasant_repair_sample(
	index: int,
	*,
	carrier_hz: float,
	sample_rate: int,
) -> float:
	"""One grating stereo sample — detuned partials and slow beating."""
	t = index / sample_rate
	phase = 2.0 * math.pi * carrier_hz * t
	value = math.sin(phase)
	value += 0.68 * math.sin(phase * 1.083)
	value += 0.38 * math.sin(phase * 2.27)
	value += 0.44 * math.sin(phase * 0.49)
	value += 0.22 * math.sin(phase * 3.91)
	# Slow amplitude wobble so the drone feels unstable.
	wobble = 0.78 + 0.22 * math.sin(2.0 * math.pi * 5.7 * t)
	return (value / 2.72) * 0.28 * wobble


def _write_unpleasant_repair_bin(
	path: Path,
	*,
	carrier_hz: float,
	sample_rate: int = 48_000,
	frames: int = _DEFAULT_FRAMES,
) -> None:
	"""Write one looping repair proximity bin with a harsh multi-partial timbre."""
	path.parent.mkdir(parents=True, exist_ok=True)
	samples: list[float] = []
	for index in range(frames):
		value = _unpleasant_repair_sample(index, carrier_hz=carrier_hz, sample_rate=sample_rate)
		clamped = max(-1.0, min(1.0, value))
		samples.append(clamped)
		samples.append(clamped)

	with wave.open(str(path), "w") as wav_file:
		wav_file.setnchannels(2)
		wav_file.setsampwidth(2)
		wav_file.setframerate(sample_rate)
		packed = b"".join(struct.pack("<h", int(sample * 32_767)) for sample in samples)
		wav_file.writeframes(packed)


def ensure_repair_tone_bank() -> list[Path]:
	"""Create pre-baked repair proximity bins (log-spaced, unpleasant timbre)."""
	paths: list[Path] = []
	if REPAIR_BIN_COUNT <= 1:
		path = ASSETS_DIR / "repair_bin_00.wav"
		_write_unpleasant_repair_bin(path, carrier_hz=REPAIR_TONE_MIN_HZ)
		return [path]

	ratio = REPAIR_TONE_MAX_HZ / REPAIR_TONE_MIN_HZ
	for bin_index in range(REPAIR_BIN_COUNT):
		t = bin_index / (REPAIR_BIN_COUNT - 1)
		carrier_hz = REPAIR_TONE_MIN_HZ * (ratio ** t)
		path = ASSETS_DIR / f"repair_bin_{bin_index:02d}.wav"
		if not path.is_file():
			_write_unpleasant_repair_bin(path, carrier_hz=carrier_hz)
		paths.append(path)
	return paths


def ensure_repair_clunk_wav() -> Path:
	"""Create a short low thud for successful repair button hits."""
	path = ASSETS_DIR / "repair_clunk.wav"
	sample_rate = 48_000
	duration_s = 0.34
	frames = int(duration_s * sample_rate)
	samples: list[float] = []
	phase = 0.0
	for index in range(frames):
		t = index / sample_rate
		attack = min(1.0, t * 1_200.0)
		decay = math.exp(-t * 11.5)
		envelope = attack * decay
		# Pitch-drop body thud — low fundamental, no snare noise.
		frequency_hz = 92.0 * math.exp(-t * 20.0) + 36.0
		phase += 2.0 * math.pi * frequency_hz / sample_rate
		thump = math.sin(phase) + 0.32 * math.sin(phase * 0.5)
		value = envelope * thump * 0.88
		clamped = max(-1.0, min(1.0, value))
		samples.append(clamped)
		samples.append(clamped)

	path.parent.mkdir(parents=True, exist_ok=True)
	with wave.open(str(path), "w") as wav_file:
		wav_file.setnchannels(2)
		wav_file.setsampwidth(2)
		wav_file.setframerate(sample_rate)
		packed = b"".join(struct.pack("<h", int(sample * 32_767)) for sample in samples)
		wav_file.writeframes(packed)
	return path


def resolve_hrtf_path(filename: str = DEFAULT_HRTF_FILENAME) -> Path | None:
	"""Return a bundled HRTF SOFA path when the file exists under ``assets/``."""
	if not filename:
		return None
	path = ASSETS_DIR / filename
	return path if path.is_file() else None
