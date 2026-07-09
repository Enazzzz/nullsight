"""UTD + BTM edge diffraction with real path delay.

Physics summary:
- Diffraction is a separate PATH, not a dry-source duck.
- UTD (Kouyoumjian–Pathak / Tsingos): apex on vertical edge, shadow-zone only.
- BTM (Biot–Tolstoy–Medwin / Svensson 1999): sample secondary sources along a
  finite vertical edge, sum 1/r delayed contributions — preferred when edge
  length is known; UTD remains the fallback.
- Excess delay δ = (s'+s) − |S−L|; DiffractionPath always carries delay_s > 0
  when a valid shadow path exists.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from acoustics.materials import SPEED_OF_SOUND_MPS
from math3d import Vec3, length, sub
from occlusion import Aabb, segment_line_of_sight

# Thin vertical slabs (door frames / wall ends) always candidate as knife-edges.
_THIN_EDGE_MAX_M = 0.40
# Thick walls still contribute exterior vertical corners (n = 1.5).
_INCLUDE_THICK_CORNERS = True
# Midband reference for |D| scaling (Hz).
_REF_FREQ_HZ = 1000.0
# Pressure gain clamp for a single diffracted path (relative to 1/r_direct).
_MAX_DIFF_GAIN = 0.28
_MIN_DIFF_GAIN = 0.02
# LPF range: deep shadow → near grazing.
_LPF_MIN_HZ = 350.0
_LPF_MAX_HZ = 4_500.0
# Skip absurdly long diffracted paths.
_MAX_PATH_M = 80.0
_MIN_LEG_M = 0.05
# BTM secondary-source samples along a finite vertical edge (Svensson).
_BTM_SAMPLES = 7
# Prefer BTM when edge length exceeds this (m).
_BTM_MIN_EDGE_M = 0.8


@dataclass(frozen=True)
class DiffractionPath:
	"""One validated diffracted arrival around a vertical edge."""

	gain: float
	# Excess delay vs direct: ((s'+s) − |S−L|) / c — matches ReflectionTap.
	delay_s: float
	lpf_hz: float
	# Absolute diffracted path length (m).
	path_length_m: float
	# Excess path vs straight-line |S−L| (m).
	excess_path_m: float
	# Apex point on the edge (world).
	apex: Vec3
	# Diffraction angle past the incident shadow boundary (radians).
	shadow_angle_rad: float
	# "btm" when Svensson finite-edge integral used; else "utd".
	model: str = "utd"


def _aabb_vertical_edges(bounds: Aabb) -> list[tuple[Vec3, Vec3]]:
	"""Return four vertical edge segments (bottom→top) of an AABB."""
	box_min, box_max = bounds
	corners_xz = (
		(box_min[0], box_min[2]),
		(box_min[0], box_max[2]),
		(box_max[0], box_min[2]),
		(box_max[0], box_max[2]),
	)
	edges: list[tuple[Vec3, Vec3]] = []
	for x, z in corners_xz:
		bottom: Vec3 = (x, box_min[1], z)
		top: Vec3 = (x, box_max[1], z)
		edges.append((bottom, top))
	return edges


def _apex_on_vertical_edge(eye: Vec3, source: Vec3, bottom: Vec3, top: Vec3) -> Vec3:
	"""Fermat apex: clamp the shortest-path Y onto the finite vertical edge."""
	# For a vertical edge, the 3D shortest path uses the same Y as the
	# unfolded 2D geodesic; clamp between source/listener and edge extents.
	y_lo = min(bottom[1], top[1])
	y_hi = max(bottom[1], top[1])
	# Linear blend of eye/source heights weighted by horizontal distances.
	ex, ez = eye[0] - bottom[0], eye[2] - bottom[2]
	sx, sz = source[0] - bottom[0], source[2] - bottom[2]
	d_eye = math.hypot(ex, ez)
	d_src = math.hypot(sx, sz)
	if d_eye + d_src < 1e-9:
		y = 0.5 * (eye[1] + source[1])
	else:
		y = (eye[1] * d_src + source[1] * d_eye) / (d_eye + d_src)
	y = max(y_lo, min(y_hi, y))
	return (bottom[0], y, bottom[2])


def _wedge_n(bounds: Aabb) -> float:
	"""Exterior wedge parameter n (free-space angle = n·π).

	Thin slab / door frame → knife-edge n=2; thick exterior corner → n=1.5.
	"""
	box_min, box_max = bounds
	thickness = min(box_max[0] - box_min[0], box_max[2] - box_min[2])
	if thickness <= _THIN_EDGE_MAX_M:
		return 2.0
	return 1.5


def _horizontal_angle(from_pt: Vec3, to_pt: Vec3) -> float:
	"""Azimuth of to−from on the XZ plane in (−π, π]."""
	return math.atan2(to_pt[2] - from_pt[2], to_pt[0] - from_pt[0])


def _angle_delta(a: float, b: float) -> float:
	"""Smallest signed angle from a to b in (−π, π]."""
	d = b - a
	while d > math.pi:
		d -= 2.0 * math.pi
	while d <= -math.pi:
		d += 2.0 * math.pi
	return d


def _shadow_angle_rad(eye: Vec3, source: Vec3, apex: Vec3, bounds: Aabb) -> float | None:
	"""Return diffraction angle past the ISB, or None if not in the shadow zone.

	Requires positive excess path (Fermat wrap around the edge). Knife-edge
	(n≈2) accepts any wrap; thick exterior corners (n≈1.5) need bend ≳ π.
	"""
	s_prime = length(sub(source, apex))
	s = length(sub(eye, apex))
	direct = length(sub(source, eye))
	if s_prime + s <= direct + 1e-4:
		return None

	ang_s = _horizontal_angle(apex, source)
	ang_l = _horizontal_angle(apex, eye)
	bend = abs(_angle_delta(ang_s, ang_l))
	n = _wedge_n(bounds)
	if n >= 1.99:
		# Knife-edge: any path that wraps around with excess length is shadow.
		theta_d = max(0.05, min(math.pi, bend * 0.5))
	else:
		theta_d = max(0.0, bend - math.pi)
		if theta_d < 0.02:
			# Near grazing / lit — skip (GO dominates).
			return None
	return theta_d


def _fresnel_transition(x: float) -> float:
	"""UTD-style transition magnitude F(X) ≈ 1 for large X; soft near 0.

	Uses the common magnitude approximation |F(X)| ≈ √(πX) / (1 + √(πX))
	so the coefficient stays finite at the shadow boundary.
	"""
	x = max(x, 0.0)
	if x < 1e-8:
		return 0.0
	root = math.sqrt(math.pi * x)
	return root / (1.0 + root)


def _utd_pressure_gain(
	s_prime: float,
	s: float,
	excess_m: float,
	theta_d: float,
	n: float,
	direct_len: float,
) -> float:
	"""Approximate hard-wall UTD pressure gain relative to direct 1/r.

	Combines spherical spreading A(s',s), 1/√k midband factor, and Fresnel
	falloff from excess path / shadow angle (Tsingos game mapping).
	"""
	path = s_prime + s
	if path < _MIN_LEG_M or direct_len < 1e-4:
		return 0.0

	# Spherical → cylindrical → spherical spreading (UTD A factor).
	spreading = math.sqrt(max(s_prime, 1e-6) / (max(s, 1e-6) * path))

	# Midband wavenumber factor |D| ∝ 1/√k.
	k = 2.0 * math.pi * _REF_FREQ_HZ / SPEED_OF_SOUND_MPS
	# Distance parameter L = s' s / (s'+s).
	L = (s_prime * s) / path
	# Fresnel argument from excess path (knife-edge / path-difference form).
	# N ≈ f·δ/c ; X ≈ 2π N for transition.
	N = _REF_FREQ_HZ * max(excess_m, 0.0) / SPEED_OF_SOUND_MPS
	X = max(2.0 * math.pi * N, k * L * (theta_d / max(n * math.pi, 0.1)) ** 2)
	F = _fresnel_transition(X)

	# Soften deeper into shadow: extra 1/(1+α θ_d).
	shadow_fade = 1.0 / (1.0 + 1.35 * theta_d)
	# Normalize so near-ISB (small θ_d, small δ) ≈ half GO jump continuity.
	# Relative to direct pressure ~1/direct_len: diffracted ~ spreading · |D|.
	d_mag = (0.55 / math.sqrt(max(k, 1.0))) * (0.35 + 0.65 * F) * shadow_fade
	# Wedge factor: knife-edge slightly louder than thick corner.
	d_mag *= 1.0 if n >= 1.99 else 0.85

	rel = (direct_len * spreading) * d_mag
	# Portal / proximity boost applied by caller.
	return max(0.0, min(_MAX_DIFF_GAIN, rel))


def _lpf_from_shadow(excess_m: float, theta_d: float) -> float:
	"""HF rolls off with excess path and diffraction angle into the shadow."""
	# Deep shadow → low cutoff; near grazing → higher.
	t = math.exp(-2.8 * max(excess_m, 0.0) - 1.1 * theta_d)
	return _LPF_MIN_HZ + (_LPF_MAX_HZ - _LPF_MIN_HZ) * t


def _candidate_bounds(blockers: list[Aabb], all_occluders: list[Aabb] | None) -> list[Aabb]:
	"""Prefer LOS blockers; also scan nearby thin slabs from the full set."""
	seen: list[Aabb] = []
	for bounds in blockers:
		seen.append(bounds)
	if all_occluders:
		for bounds in all_occluders:
			if bounds in seen:
				continue
			box_min, box_max = bounds
			thickness = min(box_max[0] - box_min[0], box_max[2] - box_min[2])
			if thickness <= _THIN_EDGE_MAX_M:
				seen.append(bounds)
	return seen


def _edge_length_m(bottom: Vec3, top: Vec3) -> float:
	"""Length of a vertical edge segment."""
	return abs(top[1] - bottom[1])


def _btm_edge_path(
	eye: Vec3,
	source: Vec3,
	bottom: Vec3,
	top: Vec3,
	bounds: Aabb,
	*,
	direct_len: float,
	occluders: list[Aabb],
	boundaries: list,
	portal_gain: float,
) -> DiffractionPath | None:
	"""Biot–Tolstoy–Medwin finite-edge integral (Svensson-style sample sum).

	Samples secondary sources along the vertical edge; each contributes with
	1/r spreading and delay (s'+s)/c. Shadow-zone only. Returns None if the
	edge is not in shadow or samples fail visibility.
	"""
	edge_len = _edge_length_m(bottom, top)
	if edge_len < _BTM_MIN_EDGE_M:
		return None

	# Fermat apex for shadow test / representative delay.
	apex = _apex_on_vertical_edge(eye, source, bottom, top)
	theta_d = _shadow_angle_rad(eye, source, apex, bounds)
	if theta_d is None:
		return None

	occ = [b for b in occluders if b is not bounds and b != bounds]
	n_samp = _BTM_SAMPLES
	pressure_sum = 0.0
	delay_weight = 0.0
	path_weight = 0.0
	best_apex = apex
	best_path = 0.0

	for i in range(n_samp):
		t = (i + 0.5) / float(n_samp)
		y = bottom[1] + t * (top[1] - bottom[1])
		sample: Vec3 = (bottom[0], y, bottom[2])
		if not segment_line_of_sight(source, sample, occ, boundaries):
			continue
		if not segment_line_of_sight(sample, eye, occ, boundaries):
			continue
		s_prime = length(sub(source, sample))
		s = length(sub(eye, sample))
		if s_prime < _MIN_LEG_M or s < _MIN_LEG_M:
			continue
		path_len = s_prime + s
		if path_len > _MAX_PATH_M:
			continue
		excess = path_len - direct_len
		if excess < 0.0:
			continue
		# Edge element weight · spherical spreading (BTM secondary source).
		ds = edge_len / float(n_samp)
		amp = ds / max(s_prime * s, 1e-6)
		# Soften with shadow angle (deeper shadow → quieter).
		amp *= 1.0 / (1.0 + 1.2 * theta_d)
		pressure_sum += amp
		delay_weight += amp * (excess / SPEED_OF_SOUND_MPS)
		path_weight += amp * path_len
		if path_len > best_path:
			best_path = path_len
			best_apex = sample

	if pressure_sum < 1e-9:
		return None

	# Normalize relative to direct 1/r and clamp.
	rel = (direct_len * pressure_sum) * 0.08
	gain = max(0.0, min(_MAX_DIFF_GAIN, rel))
	if portal_gain > 0.0:
		gain = max(gain, min(_MAX_DIFF_GAIN, portal_gain * 0.55 * (direct_len / max(best_path, 1.0))))
	if gain < _MIN_DIFF_GAIN:
		return None

	mean_delay = delay_weight / pressure_sum
	mean_path = path_weight / pressure_sum
	excess = mean_path - direct_len
	lpf = _lpf_from_shadow(max(0.0, excess), theta_d)
	if portal_gain > 0.0:
		lpf = min(_LPF_MAX_HZ, lpf + portal_gain * 1_800.0)

	return DiffractionPath(
		gain=gain,
		delay_s=min(0.35, max(1e-4, mean_delay)),
		lpf_hz=lpf,
		path_length_m=mean_path,
		excess_path_m=max(0.0, excess),
		apex=best_apex,
		shadow_angle_rad=theta_d,
		model="btm",
	)


# Max diffracted edges summed into the early field (complex occluders).
_MAX_DIFF_PATHS = 3


def estimate_diffraction_paths(
	eye: Vec3,
	source: Vec3,
	blockers: list[Aabb],
	*,
	occluders: list[Aabb] | None = None,
	boundaries: list | None = None,
	portal_gain: float = 0.0,
	direct_blocked: bool = True,
	prefer_btm: bool = True,
	max_paths: int = _MAX_DIFF_PATHS,
) -> list[DiffractionPath]:
	"""Trace up to ``max_paths`` diffracted arrivals when direct LOS is blocked.

	Prefers BTM finite-edge integral when edge length is known; falls back to
	UTD. Empty in the lit region. Delay is always excess (s'+s−|S−L|)/c.
	Multiple edges soften complex shadows vs a single best-path pick.
	"""
	if not direct_blocked:
		return []
	if not blockers and portal_gain <= 0.0 and not occluders:
		return []

	direct_len = length(sub(source, eye))
	if direct_len < 0.25:
		return []

	candidates = _candidate_bounds(blockers, occluders)
	scored: list[tuple[float, DiffractionPath]] = []
	occ_all = occluders if occluders is not None else blockers
	bnd = boundaries if boundaries is not None else []

	for bounds in candidates:
		box_min, box_max = bounds
		thickness = min(box_max[0] - box_min[0], box_max[2] - box_min[2])
		if thickness > _THIN_EDGE_MAX_M and not _INCLUDE_THICK_CORNERS and portal_gain <= 0.0:
			continue

		n = _wedge_n(bounds)
		for bottom, top in _aabb_vertical_edges(bounds):
			# Prefer BTM when the finite edge is long enough.
			if prefer_btm and _edge_length_m(bottom, top) >= _BTM_MIN_EDGE_M:
				btm = _btm_edge_path(
					eye,
					source,
					bottom,
					top,
					bounds,
					direct_len=direct_len,
					occluders=list(occ_all),
					boundaries=bnd,
					portal_gain=portal_gain,
				)
				if btm is not None:
					score = btm.gain / (1.0 + 0.15 * btm.excess_path_m)
					scored.append((score, btm))
					continue

			apex = _apex_on_vertical_edge(eye, source, bottom, top)
			theta_d = _shadow_angle_rad(eye, source, apex, bounds)
			if theta_d is None:
				continue

			s_prime = length(sub(source, apex))
			s = length(sub(eye, apex))
			if s_prime < _MIN_LEG_M or s < _MIN_LEG_M:
				continue
			path_len = s_prime + s
			if path_len > _MAX_PATH_M:
				continue
			excess = path_len - direct_len
			if excess < 0.0:
				continue

			# Visibility of both diffracted legs. Exclude the diffracting solid.
			occ = [b for b in occ_all if b is not bounds and b != bounds]
			if not segment_line_of_sight(source, apex, occ, bnd):
				continue
			if not segment_line_of_sight(apex, eye, occ, bnd):
				continue

			gain = _utd_pressure_gain(s_prime, s, excess, theta_d, n, direct_len)
			if portal_gain > 0.0:
				gain = max(gain, min(_MAX_DIFF_GAIN, portal_gain * 0.55 * (direct_len / path_len)))
			if gain < _MIN_DIFF_GAIN:
				continue

			lpf = _lpf_from_shadow(excess, theta_d)
			if portal_gain > 0.0:
				lpf = min(_LPF_MAX_HZ, lpf + portal_gain * 1_800.0)

			delay_s = excess / SPEED_OF_SOUND_MPS
			score = gain / (1.0 + 0.15 * excess)
			scored.append(
				(
					score,
					DiffractionPath(
						gain=gain,
						delay_s=min(0.35, max(1e-4, delay_s)),
						lpf_hz=lpf,
						path_length_m=path_len,
						excess_path_m=excess,
						apex=apex,
						shadow_angle_rad=theta_d,
						model="utd",
					),
				)
			)

	if not scored:
		return []
	# Keep distinct apexes (avoid near-duplicate corners of the same slab).
	scored.sort(key=lambda item: -item[0])
	kept: list[DiffractionPath] = []
	for _score, path in scored:
		if len(kept) >= max(1, max_paths):
			break
		too_close = False
		for existing in kept:
			dx = path.apex[0] - existing.apex[0]
			dz = path.apex[2] - existing.apex[2]
			if dx * dx + dz * dz < 0.35 * 0.35:
				too_close = True
				break
		if not too_close:
			kept.append(path)
	return kept


def estimate_diffraction_path(
	eye: Vec3,
	source: Vec3,
	blockers: list[Aabb],
	*,
	occluders: list[Aabb] | None = None,
	boundaries: list | None = None,
	portal_gain: float = 0.0,
	direct_blocked: bool = True,
	prefer_btm: bool = True,
) -> DiffractionPath | None:
	"""Trace the best diffracted path when direct LOS is blocked (compat)."""
	paths = estimate_diffraction_paths(
		eye,
		source,
		blockers,
		occluders=occluders,
		boundaries=boundaries,
		portal_gain=portal_gain,
		direct_blocked=direct_blocked,
		prefer_btm=prefer_btm,
		max_paths=1,
	)
	return paths[0] if paths else None


def estimate_edge_diffraction(
	eye: Vec3,
	source: Vec3,
	blockers: list[Aabb],
	*,
	portal_gain: float = 0.0,
) -> tuple[float, float]:
	"""Compatibility: return (gain, lpf_hz) for the best diffracted path.

	Assumes the caller already verified LOS is blocked.
	"""
	path = estimate_diffraction_path(
		eye,
		source,
		blockers,
		portal_gain=portal_gain,
		direct_blocked=True,
	)
	if path is None:
		return 0.0, 1_400.0
	return path.gain, path.lpf_hz
