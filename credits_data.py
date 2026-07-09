"""Load rolling credits content from ``credits.json``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_CREDITS_PATH = Path(__file__).resolve().parent / "credits.json"

# Fallback when the JSON file is missing or invalid.
_DEFAULT_ENTRIES: tuple[dict[str, object], ...] = (
	{"type": "title", "text": "NULLSIGHT"},
	{"type": "gap", "lines": 2},
	{"type": "heading", "text": "CREDITS"},
	{"type": "name", "text": "Edit credits.json to customize"},
)


@dataclass(frozen=True)
class CreditsMusic:
	"""Credits-roll soundtrack settings."""

	file: str
	start_seconds: float
	title_appear_seconds: float | None = None


@dataclass(frozen=True)
class CreditsEntry:
	"""One line or spacer in the roll."""

	entry_type: str
	text: str = ""
	gap_lines: int = 1


@dataclass(frozen=True)
class CreditsData:
	"""Parsed credits configuration."""

	music: CreditsMusic
	scroll_speed_px_per_sec: float
	scroll_delay_seconds: float
	entries: tuple[CreditsEntry, ...]


def _parse_entry(raw: dict[str, object]) -> CreditsEntry | None:
	"""Normalize one JSON entry object."""
	entry_type = str(raw.get("type", "name")).strip().lower()
	if entry_type == "gap":
		gap_lines = int(raw.get("lines", 1))
		return CreditsEntry(entry_type="gap", gap_lines=max(1, gap_lines))
	text = str(raw.get("text", "")).strip()
	if not text and entry_type != "gap":
		return None
	return CreditsEntry(entry_type=entry_type, text=text)


def scroll_delay_for_title_sync(
	*,
	height: int,
	scroll_speed_px_per_sec: float,
	music_start_seconds: float,
	title_appear_seconds: float,
	lead_in_screen_heights: float = 1.15,
) -> float:
	"""Compute pre-scroll delay so the first line enters when the track hits ``title_appear_seconds``."""
	title_enter_scroll = height * (lead_in_screen_heights - 1.0)
	scroll_travel_time = title_enter_scroll / scroll_speed_px_per_sec
	music_elapsed = title_appear_seconds - music_start_seconds
	return max(0.0, music_elapsed - scroll_travel_time)


def load_credits_data(path: Path | None = None) -> CreditsData:
	"""Read credits content from disk, falling back to built-in defaults."""
	credits_path = path or _CREDITS_PATH
	music = CreditsMusic(file="Non-Relations.mp3", start_seconds=14.0, title_appear_seconds=19.0)
	scroll_speed = 40.0
	scroll_delay_seconds = 0.0
	entries: list[CreditsEntry] = []

	if credits_path.is_file():
		try:
			payload = json.loads(credits_path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError):
			payload = {}
	else:
		payload = {}

	if isinstance(payload, dict):
		music_raw = payload.get("music", {})
		if isinstance(music_raw, dict):
			title_appear_raw = music_raw.get("title_appear_seconds")
			title_appear_seconds = (
				float(title_appear_raw)
				if title_appear_raw is not None
				else music.title_appear_seconds
			)
			music = CreditsMusic(
				file=str(music_raw.get("file", music.file)),
				start_seconds=float(music_raw.get("start_seconds", music.start_seconds)),
				title_appear_seconds=title_appear_seconds,
			)
		scroll_speed = float(payload.get("scroll_speed_px_per_sec", scroll_speed))
		scroll_delay_seconds = float(
			payload.get("scroll_delay_seconds", payload.get("show_delay_seconds", scroll_delay_seconds))
		)
		raw_entries = payload.get("entries", ())
		if isinstance(raw_entries, list) and raw_entries:
			for item in raw_entries:
				if isinstance(item, dict):
					parsed = _parse_entry(item)
					if parsed is not None:
						entries.append(parsed)

	if not entries:
		for item in _DEFAULT_ENTRIES:
			parsed = _parse_entry(item)
			if parsed is not None:
				entries.append(parsed)

	return CreditsData(
		music=music,
		scroll_speed_px_per_sec=max(8.0, scroll_speed),
		scroll_delay_seconds=max(0.0, scroll_delay_seconds),
		entries=tuple(entries),
	)


def credits_music_path(data: CreditsData) -> Path:
	"""Resolve the bundled credits music file under ``assets/``."""
	return Path(__file__).resolve().parent / "assets" / data.music.file
