"""Scanner integrity stages and scan wireframe distortion."""

from __future__ import annotations

import math
import time

from math3d import Vec3
from scan_palette import faded_packed
from screen_batch import ScreenLineCollector
from health_visual_fx import health_arch_intensity, health_noise_intensity

# Scanner integrity thresholds (ratio high → low).
_STAGE_PRISTINE = 0.90
_STAGE_GLITCH = 0.74
_STAGE_CRACK = 0.54
_STAGE_WOBBLE = 0.30

# Exponent for chaos curve — higher = calm until integrity collapses, then explodes.
_CHAOS_EXPONENT = 3.4
# Per-frame glitch pulse chance scales with chaos^2.
_GLITCH_PULSE_BASE = 0.08

# Phantom budgets scale with chaos exponentially (base + chaos * multiplier).
_PHANTOM_BASE = (0, 18, 45, 90, 160)
_PHANTOM_CHAOS_MULT = (0.0, 120.0, 280.0, 520.0, 900.0)
_SCRIBBLE_BASE = (0, 8, 22, 48, 90)
_SCRIBBLE_CHAOS_MULT = (0.0, 80.0, 200.0, 420.0, 750.0)


def scanner_distortion_stage(scanner_ratio: float) -> int:
	"""Map scanner integrity ratio to distortion stage 0 (clean) through 4 (broken)."""
	ratio = max(0.0, min(1.0, scanner_ratio))
	if ratio >= _STAGE_PRISTINE:
		return 0
	if ratio >= _STAGE_GLITCH:
		return 1
	if ratio >= _STAGE_CRACK:
		return 2
	if ratio >= _STAGE_WOBBLE:
		return 3
	return 4


def exp_chaos(scanner_ratio: float, *, exponent: float = _CHAOS_EXPONENT) -> float:
	"""Exponential 0–1 chaos from integrity (0 = pristine, 1 = dead scanner)."""
	ratio = max(0.0, min(1.0, scanner_ratio))
	return (1.0 - ratio) ** exponent


def _stage_severity(scanner_ratio: float, stage: int) -> float:
	"""0 at stage entry, 1 at stage floor — ramps FX within a stage."""
	ratio = max(0.0, min(1.0, scanner_ratio))
	thresholds = (1.0, _STAGE_PRISTINE, _STAGE_GLITCH, _STAGE_CRACK, _STAGE_WOBBLE, 0.0)
	hi = thresholds[stage]
	lo = thresholds[stage + 1]
	if hi <= lo:
		return 1.0
	linear = max(0.0, min(1.0, (hi - ratio) / (hi - lo)))
	# Exponential within stage so bottom of each band gets wild fast.
	return linear ** 1.8


def _mix_chaos(scanner_ratio: float, stage: int) -> float:
	"""Combined exponential + stage severity — master distortion scalar."""
	if stage <= 0:
		return 0.0
	severity = _stage_severity(scanner_ratio, stage)
	chaos = exp_chaos(scanner_ratio)
	return max(0.0, min(1.0, 1.0 - (1.0 - chaos) * (1.0 - severity * 0.65)))


def _hash01(*parts: float | int) -> float:
	"""Deterministic 0–1 hash."""
	value = 0
	for part in parts:
		value = (value * 1_316_021 + int(part * 10_007.0) + hash(part) % 997) & 0xFFFFFFFF
	return (value % 10_000) / 10_000.0


def _frame_salt(time_s: float) -> int:
	"""Fast-changing bucket so dropout and phantoms flicker every frame."""
	return int(time_s * 37.0) ^ int(time_s * 91.3) & 0xFFFF


def _glitch_pulse(time_s: float, chaos: float) -> bool:
	"""Random full-frame scramble burst."""
	if chaos <= 0.02:
		return False
	chance = _GLITCH_PULSE_BASE + chaos * chaos * 0.55
	return _hash01(_frame_salt(time_s), time_s * 17.3) < chance


def _rand_screen(
	width: int,
	height: int,
	*,
	seed: float,
	margin: int = 4,
) -> tuple[int, int]:
	"""Random pixel in the viewport."""
	return (
		margin + int(_hash01(seed, 0.11) * (width - margin * 2)),
		margin + int(_hash01(seed, 0.22) * (height - margin * 2)),
	)


def _wobble_offset(
	x: int,
	y: int,
	*,
	seed: int,
	time_s: float,
	amplitude: float,
	chaos: float,
) -> tuple[int, int]:
	"""Chaotic screen-space wobble — multiple incommensurate frequencies."""
	if amplitude <= 0.0:
		return x, y

	salt = _frame_salt(time_s)
	# Random amplitude jitter per call.
	amp = amplitude * (0.35 + _hash01(seed, salt, time_s) * 1.45)
	phase = time_s * (5.0 + _hash01(seed) * 11.0) + seed * 0.47 + salt * 0.03

	dx = (
		math.sin(phase * 2.9) * amp
		+ math.sin(phase * 7.1 + seed) * amp * 0.42
		+ math.sin(phase * 13.7 + salt) * amp * 0.22
	)
	dy = (
		math.cos(phase * 2.3) * amp * 0.88
		+ math.cos(phase * 5.9 + seed) * amp * 0.38
		+ math.cos(phase * 11.3 + salt) * amp * 0.19
	)

	if chaos > 0.35 and _hash01(seed, salt, time_s * 3.1) < chaos * 0.35:
		# Sudden teleport smear — endpoint jumps then wobbles.
		dx += (_hash01(seed, 1.7, salt) - 0.5) * amp * 2.8
		dy += (_hash01(seed, 2.9, salt) - 0.5) * amp * 2.8

	return int(round(x + dx)), int(round(y + dy))


def _should_drop_segment(
	start: Vec3,
	end: Vec3,
	*,
	chaos: float,
	time_s: float,
	pulse: bool,
) -> bool:
	"""Random per-frame segment dropout — exponential with integrity loss."""
	if chaos <= 0.01:
		return False

	salt = _frame_salt(time_s)
	key = (
		int(start[0] * 3.0),
		int(start[1] * 3.0),
		int(start[2] * 3.0),
		int(end[0] * 3.0),
		int(end[1] * 3.0),
		int(end[2] * 3.0),
	)
	roll = _hash01(*key, salt, int(time_s * 19.0))

	# Exponential dropout — at chaos=1 almost nothing survives.
	drop_chance = 1.0 - (1.0 - chaos) ** 2.2
	drop_chance = min(0.94, drop_chance * (1.35 if pulse else 1.0))

	# Flicker windows — whole swaths vanish for a frame.
	if _hash01(salt, int(time_s * 6.0)) < chaos * 0.25:
		drop_chance = min(0.98, drop_chance + 0.35)

	return roll < drop_chance


def segment_distortion_skipped(
	start: Vec3,
	end: Vec3,
	scanner_ratio: float,
	time_s: float,
) -> bool:
	"""Return True when a scanned segment should not draw this frame."""
	stage = scanner_distortion_stage(scanner_ratio)
	if stage <= 0:
		return False
	chaos = _mix_chaos(scanner_ratio, stage)
	pulse = _glitch_pulse(time_s, chaos)
	return _should_drop_segment(start, end, chaos=chaos, time_s=time_s, pulse=pulse)


def _attenuate_real_color(packed_color: int, chaos: float, *, pulse: bool) -> int:
	"""Crush truthful geometry as chaos rises — phantoms win."""
	if chaos <= 0.0:
		return packed_color
	scale = max(0.02, (1.0 - chaos) ** 2.6)
	if pulse:
		scale *= 0.45
	fade_guess = min(1.0, max(0.0, ((packed_color >> 8) & 0xFF) / 255.0))
	return faded_packed(fade_guess * scale)


def _fragment_line(
	x0: int,
	y0: int,
	x1: int,
	y1: int,
	color: int,
	*,
	chaos: float,
	line_index: int,
	time_s: float,
	width: int,
	height: int,
	pulse: bool,
) -> list[tuple[int, int, int, int, int]]:
	"""Shatter spans into random shards."""
	if chaos < 0.12:
		return [(x0, y0, x1, y1, color)]

	salt = _frame_salt(time_s)
	pieces = 2 + int(chaos * chaos * (8.0 + _hash01(line_index, salt) * 14.0))
	if pulse:
		pieces += int(4 + chaos * 10)

	shards: list[tuple[int, int, int, int, int]] = []
	for piece in range(pieces):
		if _hash01(line_index, piece, salt, time_s) < chaos * 0.42:
			continue

		t0 = _hash01(piece, line_index, 0.3) * 0.85
		span = 0.05 + _hash01(piece, line_index, 0.6) * (0.35 + chaos * 0.4)
		t1 = min(1.0, t0 + span)

		sx0 = int(round(x0 + (x1 - x0) * t0))
		sy0 = int(round(y0 + (y1 - y0) * t0))
		sx1 = int(round(x0 + (x1 - x0) * t1))
		sy1 = int(round(y0 + (y1 - y0) * t1))

		jitter = int(6.0 + chaos * chaos * (40.0 + _hash01(piece, salt) * 80.0))
		sx0 = max(0, min(width - 1, sx0 + int((_hash01(piece, 0.1, line_index, salt) - 0.5) * jitter * 2)))
		sy0 = max(0, min(height - 1, sy0 + int((_hash01(piece, 0.2, line_index, salt) - 0.5) * jitter * 2)))
		sx1 = max(0, min(width - 1, sx1 + int((_hash01(piece, 0.3, line_index, salt) - 0.5) * jitter * 2)))
		sy1 = max(0, min(height - 1, sy1 + int((_hash01(piece, 0.4, line_index, salt) - 0.5) * jitter * 2)))

		if chaos > 0.55 and _hash01(piece, line_index, time_s) < chaos * 0.22:
			sx0, sy0 = _rand_screen(width, height, seed=piece + line_index + time_s)
			sx1, sy1 = _rand_screen(width, height, seed=piece + line_index + time_s + 0.7)

		shard_color = color
		if _hash01(piece, line_index, time_s * 2.0, salt) < 0.35 + chaos * 0.4:
			shard_color = faded_packed(0.04 + _hash01(piece, time_s, salt) * (0.12 + chaos * 0.28))
		shards.append((sx0, sy0, sx1, sy1, shard_color))

	return shards if shards else [(x0, y0, x1, y1, color)]


def _distort_packed_line(
	x0: int,
	y0: int,
	x1: int,
	y1: int,
	packed_color: int,
	*,
	stage: int,
	chaos: float,
	scanner_ratio: float,
	time_s: float,
	line_index: int,
	width: int,
	height: int,
	pulse: bool,
) -> list[tuple[int, int, int, int, int]]:
	"""Return distorted screen lines for one collected segment."""
	packed_color = _attenuate_real_color(packed_color, chaos, pulse=pulse)

	# Exponential wobble — small chaos = barely visible, high chaos = screen-filling.
	wobble_amp = chaos * chaos * (12.0 + (width + height) * 0.22)
	if pulse:
		wobble_amp *= 1.6 + _hash01(line_index, time_s) * 0.9

	wx0, wy0 = _wobble_offset(
		x0, y0, seed=line_index * 3, time_s=time_s, amplitude=wobble_amp, chaos=chaos,
	)
	wx1, wy1 = _wobble_offset(
		x1, y1, seed=line_index * 3 + 1, time_s=time_s, amplitude=wobble_amp, chaos=chaos,
	)

	if chaos > 0.45 and _hash01(line_index, _frame_salt(time_s), time_s) < chaos * 0.38:
		# Full line hallucination replacement — random screen chord.
		wx0, wy0 = _rand_screen(width, height, seed=line_index + time_s * 1.1)
		wx1, wy1 = _rand_screen(width, height, seed=line_index + time_s * 2.3)

	if chaos > 0.65:
		break_amt = int(chaos * chaos * (30.0 + width * 0.15))
		if _hash01(line_index, time_s * 1.7) < 0.55 + chaos * 0.35:
			wx0 += int((_hash01(line_index, 0.7, time_s) - 0.5) * break_amt * 2)
			wy0 += int((_hash01(line_index, 0.8, time_s) - 0.5) * break_amt * 2)
		if _hash01(line_index, time_s * 2.1) < 0.55 + chaos * 0.35:
			wx1 += int((_hash01(line_index, 0.9, time_s) - 0.5) * break_amt * 2)
			wy1 += int((_hash01(line_index, 1.0, time_s) - 0.5) * break_amt * 2)

	lines: list[tuple[int, int, int, int, int]] = []
	for sx0, sy0, sx1, sy1, color in _fragment_line(
		wx0, wy0, wx1, wy1, packed_color,
		chaos=chaos, line_index=line_index,
		time_s=time_s, width=width, height=height, pulse=pulse,
	):
		lines.append((sx0, sy0, sx1, sy1, color))

	if chaos > 0.18:
		crack_count = 1 + int(chaos * chaos * (3.0 + _hash01(line_index, time_s) * 8.0))
		for crack in range(crack_count):
			shift = int(chaos * chaos * (20.0 + _hash01(crack, line_index, time_s) * 60.0))
			crack_color = faded_packed(0.08 + _hash01(crack, time_s) * (0.15 + chaos * 0.35))
			cx0 = max(0, min(width - 1, wx0 + int((_hash01(crack, 0.1) - 0.5) * shift * 2)))
			cy0 = max(0, min(height - 1, wy0 + int((_hash01(crack, 0.2) - 0.5) * shift * 2)))
			cx1 = max(0, min(width - 1, wx1 + int((_hash01(crack, 0.3) - 0.5) * shift * 2)))
			cy1 = max(0, min(height - 1, wy1 + int((_hash01(crack, 0.4) - 0.5) * shift * 2)))
			lines.append((cx0, cy0, cx1, cy1, crack_color))

	if chaos > 0.22:
		ghost_count = int(1 + chaos * chaos * (2.0 + _hash01(line_index, time_s) * 10.0))
		for ghost_index in range(ghost_count):
			offset = int(chaos * chaos * (25.0 + _hash01(ghost_index, line_index, time_s) * 120.0))
			angle = _hash01(ghost_index, line_index, time_s) * math.tau
			ox = int(math.cos(angle) * offset)
			oy = int(math.sin(angle) * offset)
			ghost = faded_packed(0.06 + _hash01(ghost_index, time_s) * (0.22 + chaos * 0.35))
			lines.append((
				max(0, min(width - 1, wx0 + ox)),
				max(0, min(height - 1, wy0 + oy)),
				max(0, min(width - 1, wx1 - ox // 2)),
				max(0, min(height - 1, wy1 - oy // 2)),
				ghost,
			))

	return lines


def _add_phantom_lines(
	collector: ScreenLineCollector,
	*,
	stage: int,
	chaos: float,
	time_s: float,
	width: int,
	height: int,
	pulse: bool,
) -> None:
	"""Random hallucinated lines — count scales exponentially with chaos."""
	salt = _frame_salt(time_s)
	jitter = 0.65 + _hash01(salt, time_s) * 0.9
	if pulse:
		jitter *= 1.8

	cap = int((_PHANTOM_BASE[stage] + _PHANTOM_CHAOS_MULT[stage] * chaos ** 2.3) * jitter)
	scribbles = int((_SCRIBBLE_BASE[stage] + _SCRIBBLE_CHAOS_MULT[stage] * chaos ** 2.3) * jitter)
	margin = 4

	for index in range(cap):
		seed = index * 1.7 + time_s * (13.0 + _hash01(index, salt) * 19.0)
		x0, y0 = _rand_screen(width, height, seed=seed)
		length = int(8.0 + _hash01(seed, 0.3) * (60.0 + chaos * chaos * 220.0))
		angle = _hash01(seed, 0.4) * math.tau + time_s * (2.0 + _hash01(index) * 6.0)
		x1 = max(margin, min(width - margin, x0 + int(math.cos(angle) * length)))
		y1 = max(margin, min(height - margin, y0 + int(math.sin(angle) * length)))
		fade = 0.06 + _hash01(seed, salt) * (0.12 + chaos * 0.38)
		collector.add_packed(x0, y0, x1, y1, faded_packed(fade))

		if _hash01(index, seed, time_s) < chaos * 0.35:
			# Extra random chord from nowhere.
			x2, y2 = _rand_screen(width, height, seed=seed + 3.3)
			collector.add_packed(x1, y1, x2, y2, faded_packed(fade * 0.7))

	for index in range(scribbles):
		seed = index + 911.0 + time_s * (9.0 + _hash01(index, salt) * 14.0)
		cx, cy = _rand_screen(width, height, seed=seed)
		radius = int(10.0 + _hash01(seed, 1.3) * (20.0 + chaos * chaos * 90.0))
		steps = 2 + int(2.0 + chaos * (4.0 + _hash01(seed) * 10.0))
		prev_x, prev_y = cx + radius, cy
		for step in range(steps):
			a = time_s * (4.0 + _hash01(step, seed) * 9.0) + step * (math.tau / steps)
			r = radius * (0.4 + _hash01(step, seed, time_s) * 1.1)
			px = max(margin, min(width - margin, cx + int(math.cos(a) * r)))
			py = max(margin, min(height - margin, cy + int(math.sin(a) * r * 0.8)))
			collector.add_packed(prev_x, prev_y, px, py, faded_packed(0.05 + _hash01(step, time_s) * (0.18 + chaos * 0.3)))
			prev_x, prev_y = px, py


def _add_false_architecture(
	collector: ScreenLineCollector,
	*,
	chaos: float,
	time_s: float,
	width: int,
	height: int,
	pulse: bool,
) -> None:
	"""Fake walls/floors at random angles — not aligned with truth."""
	if chaos < 0.15:
		return

	salt = _frame_salt(time_s)
	cap = int(chaos ** 2.6 * (6 + _hash01(salt, time_s) * 16) * (1.2 if pulse else 0.85))
	cap = max(0, min(cap, int(8 + chaos ** 2.2 * 55)))
	margin = 8

	for index in range(cap):
		seed = index + time_s * (5.0 + _hash01(index, salt) * 8.0)
		kind = _hash01(seed, 0.5)

		if kind < 0.33:
			y = margin + int(_hash01(seed, 0.6) * (height - margin * 2))
			x0 = margin + int(_hash01(seed, 0.7) * (width - margin * 2))
			x1 = margin + int(_hash01(seed, 0.8) * (width - margin * 2))
			wobble = int((_hash01(time_s, seed) - 0.5) * chaos * 40.0)
			collector.add_packed(x0, y + wobble, x1, y - wobble, faded_packed(0.08 + _hash01(seed) * chaos * 0.35))
		elif kind < 0.66:
			x = margin + int(_hash01(seed, 0.6) * (width - margin * 2))
			y0 = margin + int(_hash01(seed, 0.7) * (height - margin * 2))
			y1 = margin + int(_hash01(seed, 0.8) * (height - margin * 2))
			wobble = int((_hash01(time_s, seed) - 0.5) * chaos * 40.0)
			collector.add_packed(x + wobble, y0, x - wobble, y1, faded_packed(0.07 + _hash01(seed) * chaos * 0.32))
		else:
			x0, y0 = _rand_screen(width, height, seed=seed)
			x1, y1 = _rand_screen(width, height, seed=seed + 1.1)
			collector.add_packed(x0, y0, x1, y1, faded_packed(0.06 + _hash01(seed, time_s) * chaos * 0.28))


def _add_fake_boxes(
	collector: ScreenLineCollector,
	*,
	chaos: float,
	time_s: float,
	width: int,
	height: int,
	pulse: bool,
) -> None:
	"""Random screen-space box wireframes — false volumetric scan hits."""
	if chaos < 0.06:
		return

	salt = _frame_salt(time_s)
	cap = int(chaos ** 2.5 * (4 + _hash01(salt, time_s) * 12) * (1.15 if pulse else 1.0))
	cap = max(0, min(cap, int(6 + chaos ** 2.0 * 28)))
	margin = 12

	for index in range(cap):
		seed = index * 2.7 + time_s * (4.0 + _hash01(index, salt) * 9.0)
		cx = margin + int(_hash01(seed, 0.4) * (width - margin * 2))
		cy = margin + int(_hash01(seed, 0.5) * (height - margin * 2))
		half_w = int(14 + _hash01(seed, 0.6) * (36 + chaos ** 1.8 * 110))
		half_h = int(12 + _hash01(seed, 0.7) * (30 + chaos ** 1.8 * 90))
		fade = 0.08 + _hash01(seed, salt) * (0.09 + chaos ** 1.5 * 0.32)
		color = faded_packed(fade)

		x0 = max(margin, cx - half_w)
		y0 = max(margin, cy - half_h)
		x1 = min(width - margin, cx + half_w)
		y1 = min(height - margin, cy + half_h)

		collector.add_packed(x0, y0, x1, y0, color)
		collector.add_packed(x1, y0, x1, y1, color)
		collector.add_packed(x1, y1, x0, y1, color)
		collector.add_packed(x0, y1, x0, y0, color)

		depth_x = int((_hash01(seed, 0.81) - 0.25) * half_w * 1.1)
		depth_y = int((_hash01(seed, 0.92) - 0.35) * half_h * 0.85)
		bx0 = max(margin, x0 + depth_x)
		by0 = max(margin, y0 + depth_y)
		bx1 = min(width - margin, x1 + depth_x)
		by1 = min(height - margin, y1 + depth_y)

		collector.add_packed(bx0, by0, bx1, by0, color)
		collector.add_packed(bx1, by0, bx1, by1, color)
		collector.add_packed(bx1, by1, bx0, by1, color)
		collector.add_packed(bx0, by1, bx0, by0, color)
		collector.add_packed(x0, y0, bx0, by0, color)
		collector.add_packed(x1, y0, bx1, by0, color)
		collector.add_packed(x1, y1, bx1, by1, color)
		collector.add_packed(x0, y1, bx0, by1, color)


def apply_scan_distortion(
	collector: ScreenLineCollector,
	scanner_ratio: float,
	time_s: float,
	width: int,
	height: int,
	health_ratio: float = 1.0,
) -> None:
	"""Mutate collected scan lines — health gates noise; high health gets fake geometry."""
	health_arch = health_arch_intensity(health_ratio)
	health_noise = health_noise_intensity(health_ratio)

	stage = scanner_distortion_stage(scanner_ratio)
	scanner_chaos = _mix_chaos(scanner_ratio, stage) if stage > 0 else 0.0
	random_chaos = scanner_chaos * (0.25 + health_noise * 1.05)
	pulse = _glitch_pulse(time_s, max(scanner_chaos, health_arch, health_noise))

	original = list(collector.lines)
	count = len(original)
	collector.clear()

	if stage > 0 and count > 0 and random_chaos > 0.01:
		est_extra = 0
		if health_noise > 0.01:
			est_extra = int(
				(_PHANTOM_BASE[stage] + _PHANTOM_CHAOS_MULT[stage] * random_chaos ** 1.5)
				+ (_SCRIBBLE_BASE[stage] + _SCRIBBLE_CHAOS_MULT[stage] * random_chaos ** 1.5)
			)
		collector.reserve(count * (2 + int(random_chaos ** 1.4 * 6)) + est_extra)

		for index in range(count):
			x0, y0, x1, y1, packed_color = original[index]
			for line in _distort_packed_line(
				x0,
				y0,
				x1,
				y1,
				packed_color,
				stage=stage,
				chaos=random_chaos ** 1.15,
				scanner_ratio=scanner_ratio,
				time_s=time_s,
				line_index=index,
				width=width,
				height=height,
				pulse=pulse,
			):
				collector.add_packed(*line)

		if health_noise > 0.015:
			_add_phantom_lines(
				collector,
				stage=stage,
				chaos=random_chaos ** 1.55,
				time_s=time_s,
				width=width,
				height=height,
				pulse=pulse,
			)
	elif count > 0:
		for line in original:
			collector.add_packed(*line)

	if health_arch > 0.012:
		arch_chaos = health_arch ** 1.25
		_add_false_architecture(
			collector,
			chaos=arch_chaos,
			time_s=time_s,
			width=width,
			height=height,
			pulse=pulse,
		)
		_add_fake_boxes(
			collector,
			chaos=arch_chaos,
			time_s=time_s,
			width=width,
			height=height,
			pulse=pulse,
		)

def scan_distortion_state(
	scanner_ratio: float,
	time_s: float,
) -> tuple[float, bool]:
	"""Return combined chaos scalar and whether a glitch pulse is active."""
	stage = scanner_distortion_stage(scanner_ratio)
	if stage <= 0:
		return 0.0, False
	chaos = _mix_chaos(scanner_ratio, stage)
	return chaos, _glitch_pulse(time_s, chaos)


def distortion_clock() -> float:
	"""Wall clock for distortion animation when scan sim time is unavailable."""
	return time.perf_counter()
