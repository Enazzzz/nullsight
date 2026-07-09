"""AcousticSystem — snapshot, throttled tracing, and smoothed obstruction links."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from acoustics.scene import AcousticScene
from acoustics.tracer import trace_acoustic_path
from acoustics.types import AcousticLink, AcousticSource, AcousticWorldSnapshot, ReflectionTap
from camera import Camera
from math3d import Vec3
from room import RoomData

if TYPE_CHECKING:
	from interactable_door import InteractableDoor

# Retrace interval (~20 Hz).
_TRACE_INTERVAL_SEC = 0.05
# Exponential smoothing time constant (seconds).
_SMOOTH_TAU_SEC = 0.12


def _exp_smooth(current: float, target: float, dt: float, tau: float) -> float:
	"""Approach ``target`` with a first-order exponential filter."""
	if tau <= 1e-6 or dt <= 0.0:
		return target
	alpha = 1.0 - math.exp(-dt / tau)
	return current + (target - current) * alpha


class AcousticSystem:
	"""Owns scene tracking, ray scheduling, and smoothed ``AcousticLink`` output."""

	def __init__(self) -> None:
		self._scene = AcousticScene()
		self._trace_accum = 0.0
		self._last_geometry_revision = -1
		self._last_source_ids: frozenset[int] = frozenset()
		self._target_links: dict[int, AcousticLink] = {}
		self._smoothed_gain: dict[int, float] = {}
		self._smoothed_lpf: dict[int, float] = {}
		self._smoothed_trans_gain: dict[int, float] = {}
		self._smoothed_trans_lpf: dict[int, float] = {}
		self._smoothed_refl_gain: dict[int, float] = {}
		self._smoothed_refl_delay: dict[int, float] = {}
		self._smoothed_refl_lpf: dict[int, float] = {}
		# Per-source list of smoothed specular taps (gain, delay, lpf, order, extras).
		self._smoothed_taps: dict[int, list[tuple[float, float, float, int, Vec3, tuple[float, ...], bool]]] = {}
		self._smoothed_diff_gain: dict[int, float] = {}
		self._smoothed_diff_delay: dict[int, float] = {}
		self._smoothed_diff_lpf: dict[int, float] = {}
		self._smoothed_reverb: dict[int, float] = {}
		self._last_links: dict[int, AcousticLink] = {}
		# Last geometry-derived RT60 pushed to the late bus (via audio bridge).
		self._last_rt60_s: float = 0.0

	@property
	def geometry_revision(self) -> int:
		"""Current acoustic geometry revision from the scene tracker."""
		return self._scene.geometry_revision

	@property
	def last_links(self) -> dict[int, AcousticLink]:
		"""Most recent smoothed links (for debug HUD)."""
		return self._last_links

	def notify_level_loaded(self, room: RoomData) -> None:
		"""Call when a level is loaded or reloaded."""
		self._scene.notify_level_loaded(room)

	def snapshot(
		self,
		room: RoomData,
		door: InteractableDoor | None,
		sources: list[AcousticSource],
		camera: Camera,
	) -> AcousticWorldSnapshot:
		"""Build an immutable world snapshot for one acoustics update."""
		self._scene.notify_level_loaded(room)
		occluders, materials = self._scene.build_occluders(room, door)
		portal_gain, portal_lpf = self._scene.portal_state(door)
		return AcousticWorldSnapshot(
			listener_eye=camera.eye_position(),
			occluders=occluders,
			boundaries=self._scene.boundaries(room),
			sources=list(sources),
			geometry_revision=self._scene.geometry_revision,
			occluder_materials=materials,
			portal_gain=portal_gain,
			portal_lpf_hz=portal_lpf,
			door_open_frac=self._scene.door_open_frac(door),
			room_name=room.name,
			shell_material=getattr(room, "shell_material", "concrete") or "concrete",
			listener_yaw_rad=camera.yaw,
			listener_pitch_rad=camera.pitch,
		)

	def update(self, snapshot: AcousticWorldSnapshot, dt: float) -> dict[int, AcousticLink]:
		"""Retrace when needed, smooth parameters, return links keyed by source id."""
		source_ids = frozenset(source.source_id for source in snapshot.sources)
		force_trace = (
			snapshot.geometry_revision != self._last_geometry_revision
			or source_ids != self._last_source_ids
		)

		self._trace_accum += max(0.0, dt)
		should_trace = force_trace or self._trace_accum >= _TRACE_INTERVAL_SEC
		if should_trace:
			self._trace_accum = 0.0
			self._last_geometry_revision = snapshot.geometry_revision
			self._last_source_ids = source_ids
			self._retrace(snapshot)

		links = self._smooth_links(dt)
		self._last_links = links
		return links

	def clear(self) -> None:
		"""Drop cached links (e.g. when returning to the title screen)."""
		self._target_links.clear()
		self._smoothed_gain.clear()
		self._smoothed_lpf.clear()
		self._smoothed_trans_gain.clear()
		self._smoothed_trans_lpf.clear()
		self._smoothed_refl_gain.clear()
		self._smoothed_refl_delay.clear()
		self._smoothed_refl_lpf.clear()
		self._smoothed_taps.clear()
		self._smoothed_diff_gain.clear()
		self._smoothed_diff_delay.clear()
		self._smoothed_diff_lpf.clear()
		self._smoothed_reverb.clear()
		self._last_links.clear()
		self._last_source_ids = frozenset()
		self._trace_accum = 0.0
		self._last_rt60_s = 0.0

	def _retrace(self, snapshot: AcousticWorldSnapshot) -> None:
		"""Run acoustic path rays for every source in the snapshot."""
		next_targets: dict[int, AcousticLink] = {}
		for source in snapshot.sources:
			link = trace_acoustic_path(
				snapshot.listener_eye,
				source,
				snapshot.occluders,
				snapshot.boundaries,
				occluder_materials=snapshot.occluder_materials,
				portal_gain=snapshot.portal_gain,
				portal_lpf_hz=snapshot.portal_lpf_hz,
				room_name=snapshot.room_name,
				door_open_frac=snapshot.door_open_frac,
				shell_material=snapshot.shell_material,
			)
			next_targets[source.source_id] = link
		self._target_links = next_targets

		alive = set(next_targets)
		for key in list(self._smoothed_gain):
			if key not in alive:
				del self._smoothed_gain[key]
				self._smoothed_lpf.pop(key, None)
				self._smoothed_trans_gain.pop(key, None)
				self._smoothed_trans_lpf.pop(key, None)
				self._smoothed_refl_gain.pop(key, None)
				self._smoothed_refl_delay.pop(key, None)
				self._smoothed_refl_lpf.pop(key, None)
				self._smoothed_taps.pop(key, None)
				self._smoothed_diff_gain.pop(key, None)
				self._smoothed_diff_delay.pop(key, None)
				self._smoothed_diff_lpf.pop(key, None)
				self._smoothed_reverb.pop(key, None)

	def _smooth_tap_list(
		self,
		source_id: int,
		target_taps: tuple[ReflectionTap, ...],
		dt: float,
	) -> tuple[ReflectionTap, ...]:
		"""Smooth each specular tap slot toward the latest validated cluster."""
		prev = self._smoothed_taps.get(source_id, [])
		smoothed: list[tuple[float, float, float, int, Vec3, tuple[float, ...], bool]] = []
		used_prev: set[int] = set()
		for tap in target_taps:
			best_i = -1
			best_dt = 1e9
			for index, prev_row in enumerate(prev):
				pg, pd, pl, po, _ad, _be, _df = prev_row
				if index in used_prev:
					continue
				dd = abs(pd - tap.delay_s)
				if dd < best_dt and dd < 0.008:
					best_dt = dd
					best_i = index
			if best_i >= 0:
				pg, pd, pl, po, ad, be, df = prev[best_i]
				used_prev.add(best_i)
				g = _exp_smooth(pg, tap.gain, dt, _SMOOTH_TAU_SEC)
				d = _exp_smooth(pd, tap.delay_s, dt, _SMOOTH_TAU_SEC)
				l = _exp_smooth(pl, tap.lpf_hz, dt, _SMOOTH_TAU_SEC)
				smoothed.append((g, d, l, tap.bounce_order, tap.arrival_dir, tap.band_energy, tap.is_diffuse))
			else:
				smoothed.append(
					(tap.gain, tap.delay_s, tap.lpf_hz, tap.bounce_order, tap.arrival_dir, tap.band_energy, tap.is_diffuse)
				)
		self._smoothed_taps[source_id] = smoothed
		return tuple(
			ReflectionTap(
				gain=g,
				delay_s=d,
				lpf_hz=l,
				bounce_order=o,
				arrival_dir=ad,
				band_energy=be,  # type: ignore[arg-type]
				is_diffuse=df,
			)
			for g, d, l, o, ad, be, df in smoothed
		)

	def _smooth_links(self, dt: float) -> dict[int, AcousticLink]:
		"""Exponentially approach target parameters for each source."""
		result: dict[int, AcousticLink] = {}
		for source_id, target in self._target_links.items():
			gain = self._smoothed_gain.get(source_id, target.direct_gain)
			lpf = self._smoothed_lpf.get(source_id, target.direct_lpf_hz)
			trans_gain = self._smoothed_trans_gain.get(source_id, target.transmission_gain)
			trans_lpf = self._smoothed_trans_lpf.get(source_id, target.transmission_lpf_hz)
			refl_gain = self._smoothed_refl_gain.get(source_id, target.reflection_gain)
			refl_delay = self._smoothed_refl_delay.get(source_id, target.reflection_delay_s)
			refl_lpf = self._smoothed_refl_lpf.get(source_id, target.reflection_lpf_hz)
			diff_gain = self._smoothed_diff_gain.get(source_id, target.diffraction_gain)
			diff_delay = self._smoothed_diff_delay.get(source_id, target.diffraction_delay_s)
			diff_lpf = self._smoothed_diff_lpf.get(source_id, target.diffraction_lpf_hz)
			reverb = self._smoothed_reverb.get(source_id, target.reverb_send)

			gain = _exp_smooth(gain, target.direct_gain, dt, _SMOOTH_TAU_SEC)
			lpf = _exp_smooth(lpf, target.direct_lpf_hz, dt, _SMOOTH_TAU_SEC)
			trans_gain = _exp_smooth(trans_gain, target.transmission_gain, dt, _SMOOTH_TAU_SEC)
			trans_lpf = _exp_smooth(trans_lpf, target.transmission_lpf_hz, dt, _SMOOTH_TAU_SEC)
			refl_gain = _exp_smooth(refl_gain, target.reflection_gain, dt, _SMOOTH_TAU_SEC)
			refl_delay = _exp_smooth(refl_delay, target.reflection_delay_s, dt, _SMOOTH_TAU_SEC)
			refl_lpf = _exp_smooth(refl_lpf, target.reflection_lpf_hz, dt, _SMOOTH_TAU_SEC)
			diff_gain = _exp_smooth(diff_gain, target.diffraction_gain, dt, _SMOOTH_TAU_SEC)
			diff_delay = _exp_smooth(diff_delay, target.diffraction_delay_s, dt, _SMOOTH_TAU_SEC)
			diff_lpf = _exp_smooth(diff_lpf, target.diffraction_lpf_hz, dt, _SMOOTH_TAU_SEC)
			reverb = _exp_smooth(reverb, target.reverb_send, dt, _SMOOTH_TAU_SEC)
			taps = self._smooth_tap_list(source_id, target.reflection_taps, dt)

			self._smoothed_gain[source_id] = gain
			self._smoothed_lpf[source_id] = lpf
			self._smoothed_trans_gain[source_id] = trans_gain
			self._smoothed_trans_lpf[source_id] = trans_lpf
			self._smoothed_refl_gain[source_id] = refl_gain
			self._smoothed_refl_delay[source_id] = refl_delay
			self._smoothed_refl_lpf[source_id] = refl_lpf
			self._smoothed_diff_gain[source_id] = diff_gain
			self._smoothed_diff_delay[source_id] = diff_delay
			self._smoothed_diff_lpf[source_id] = diff_lpf
			self._smoothed_reverb[source_id] = reverb
			self._last_rt60_s = target.rt60_s

			result[source_id] = AcousticLink(
				source_id=source_id,
				direct_gain=gain,
				direct_lpf_hz=lpf,
				transmission_gain=trans_gain,
				transmission_lpf_hz=trans_lpf,
				reflection_gain=refl_gain,
				reflection_delay_s=refl_delay,
				reflection_lpf_hz=refl_lpf,
				reflection_taps=taps,
				diffraction_gain=diff_gain,
				diffraction_delay_s=diff_delay,
				diffraction_lpf_hz=diff_lpf,
				path_mode=target.path_mode,
				reverb_send=reverb,
				rt60_s=target.rt60_s,
				reverb_predelay_s=target.reverb_predelay_s,
				portal_graph_gain=target.portal_graph_gain,
				structure_borne_gain=target.structure_borne_gain,
				structure_borne_lpf_hz=target.structure_borne_lpf_hz,
				structure_borne_delay_s=target.structure_borne_delay_s,
				diffraction_extra=target.diffraction_extra,
			)
		return result
