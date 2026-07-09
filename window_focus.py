"""Detect whether the game window is the foreground Win32 window."""

from __future__ import annotations

import ctypes
import sys

# Win32 API bindings — only used on Windows.
if sys.platform == "win32":
	_user32 = ctypes.windll.user32
else:
	_user32 = None


def is_game_window_focused(window_title: str) -> bool:
	"""Return True when the titled window owns the foreground (always True off Windows)."""
	if _user32 is None:
		return True

	hwnd = _user32.FindWindowW(None, window_title)
	if not hwnd:
		return True

	return _user32.GetForegroundWindow() == hwnd
