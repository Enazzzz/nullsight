"""Health-driven auditory hallucinations — ringing, drift, and phantom sources."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from camera import Camera
from nullsight_audio import NullsightAudio

# Health ratio thresholds for auditory stages (high → low).
_STAGE_CLEAR = 0.78
_STAGE_UNEASE = 0.58
_STAGE_DRIFT = 0.38
_STAGE_BREAK = 0.18

# Tinnitus only on very rapid health loss (burst tracker equivalent HP/s).
_RING_DAMAGE_RATE_TRIGGER = 78.0
_RING_DAMAGE_RATE_SEVERE = 115.0

# Ear safety — direct stereo ring stays quiet; never pierce even on severe hits.
_RING_GAIN_SOFT = 0.045
_RING_GAIN_SEVERE = 0.075
_RING_ABS_PEAK = 0.085
_RING_RELEASE_PER_SEC = 5.5

# Max concurrent phantom spatial loops.
_MAX_PHANTOMS = 4


def health_audio_stage(health_ratio: float) -> int:
	"""Map player health ratio to auditory hallucination stage 0–4."""
	ratio = max(0.0, min(1.0, health_ratio))
	if ratio >= _STAGE_CLEAR:
		return 0
	if ratio >= _STAGE_UNEASE:
		return 1
	if ratio >= _STAGE_DRIFT:
		return 2
	if ratio >= _STAGE_BREAK:
		return 3
	return 4


def _stage_severity(health_ratio: float, stage: int) -> float:
	"""Ramp effect strength within the active health stage."""
	ratio = max(0.0, min(1.0, health_ratio))
	thresholds = (1.0, _STAGE_CLEAR, _STAGE_UNEASE, _STAGE_DRIFT, _STAGE_BREAK, 0.0)
	hi = thresholds[stage]
	lo = thresholds[stage + 1]
	if hi <= lo:
		return 1.0
	return max(0.0, min(1.0, (hi - ratio) / (hi - lo)))


def _hash01(seed: float) -> float:
	"""Deterministic 0–1 scalar."""
	value = int(seed * 12_989.0) & 0xFFFFFFFF
	return ((value * 16_807) & 0xFFFFFFFF) / 0xFFFFFFFF


def _safe_ring_gain(damage_rate: float) -> float:
	"""Map burst damage rate to a hearing-safe ring level (0 when not spiking)."""
	if damage_rate < _RING_DAMAGE_RATE_TRIGGER:
		return 0.0
	if damage_rate >= _RING_DAMAGE_RATE_SEVERE:
		t = min(1.0, (damage_rate - _RING_DAMAGE_RATE_SEVERE) / 80.0)
		gain = _RING_GAIN_SOFT + (_RING_GAIN_SEVERE - _RING_GAIN_SOFT) * t
	else:
		t = (damage_rate - _RING_DAMAGE_RATE_TRIGGER) / (
			_RING_DAMAGE_RATE_SEVERE - _RING_DAMAGE_RATE_TRIGGER
		)
		gain = _RING_GAIN_SOFT * t
	return min(_RING_ABS_PEAK, gain)


@dataclass
class _PhantomSound:
	"""One spawned hallucination loop."""

	entity_id: int
	ttl: float
	base_gain: float


@dataclass
class HealthAudioFx:
	"""Drives spatial audio hallucinations from player health and damage bursts."""

	_time: float = 0.0
	_ring_gain: float = 0.0
	_ring_entity: object | None = None
	_phantoms: list[_PhantomSound] = field(default_factory=list)
	_spawn_cooldown: float = 2.0

	def reset(self, audio: NullsightAudio | None = None) -> None:
		"""Clear hallucinations and silence tinnitus (title screen / new run)."""
		if audio is not None:
			self._clear_phantoms(audio)
			self._silence_ringing()
		else:
			self._phantoms.clear()
			self._ring_gain = 0.0
		self._spawn_cooldown = 2.0

	def on_pause(self, audio: NullsightAudio) -> None:
		"""Freeze health FX — silence ring and restore true source positions."""
		self._silence_ringing()
		for loop in audio.level_loops:
			audio.set_loop_position(loop.entity_id, loop.base_x, loop.base_y, loop.base_z)

	def _ensure_ring(self, audio: NullsightAudio) -> None:
		"""Lazy-create the direct stereo ringing source."""
		if self._ring_entity is not None:
			return
		self._ring_entity = audio.spawn_direct_ring()

	def _silence_ringing(self) -> None:
		"""Immediately mute the tinnitus layer."""
		self._ring_gain = 0.0
		if self._ring_entity is not None:
			self._ring_entity.set_gain(0.0)
			self._ring_entity.set_active(False)

	def _clear_phantoms(self, audio: NullsightAudio) -> None:
		"""Despawn every hallucinated spatial loop."""
		for phantom in self._phantoms:
			audio.despawn_loop(phantom.entity_id)
		self._phantoms.clear()

	def update(
		self,
		audio: NullsightAudio,
		camera: Camera,
		*,
		dt: float,
		health_ratio: float,
		damage_rate: float,
		room_bounds: tuple[float, float, float, float],
	) -> None:
		"""Advance ringing, source drift, and phantom spatial loops."""
		if not audio.active or dt <= 0.0:
			return

		self._time += dt
		stage = health_audio_stage(health_ratio)
		severity = _stage_severity(health_ratio, stage) if stage > 0 else 0.0

		self._update_ringing(audio, dt, damage_rate=damage_rate)
		self._update_source_drift(audio, camera, stage=stage, severity=severity, dt=dt)
		self._update_phantoms(audio, camera, dt, stage=stage, severity=severity, room_bounds=room_bounds)

	def _update_ringing(
		self,
		audio: NullsightAudio,
		dt: float,
		*,
		damage_rate: float,
	) -> None:
		"""Brief, quiet tinnitus only during super-rapid health loss spikes."""
		self._ensure_ring(audio)
		if self._ring_entity is None:
			return

		target = _safe_ring_gain(damage_rate)

		if target > self._ring_gain:
			self._ring_gain = min(_RING_ABS_PEAK, self._ring_gain + dt * 10.0 * target)
		else:
			self._ring_gain = max(target, self._ring_gain - dt * _RING_RELEASE_PER_SEC)

		self._ring_gain = min(_RING_ABS_PEAK, self._ring_gain)

		if self._ring_gain <= 0.001:
			self._silence_ringing()
			return

		# Gentle modulation — avoids a harsh steady tone.
		pulse = 0.88 + 0.12 * math.sin(self._time * 9.0)
		gain = min(_RING_ABS_PEAK, self._ring_gain * pulse * audio.master_gain_setting)
		self._ring_entity.set_gain(gain)
		self._ring_entity.set_active(True)

	def _update_source_drift(
		self,
		audio: NullsightAudio,
		camera: Camera,
		*,
		stage: int,
		severity: float,
		dt: float = 0.0,
	) -> None:
		"""Make real room sources appear to wander when health is low."""
		if stage < 2:
			for loop in audio.level_loops:
				audio.set_loop_position(
					loop.entity_id,
					loop.base_x,
					loop.base_y,
					loop.base_z,
					dt=dt,
				)
			return

		eye = camera.eye_position()
		max_drift = 0.0
		if stage == 2:
			max_drift = 0.35 + severity * 1.1
		elif stage == 3:
			max_drift = 1.0 + severity * 2.8
		else:
			max_drift = 2.2 + severity * 5.5

		for index, loop in enumerate(audio.level_loops):
			seed = index * 17 + 3
			phase = self._time * (0.45 + index * 0.12 + stage * 0.18)
			dx = math.sin(phase * 1.7 + seed) * max_drift
			dz = math.cos(phase * 1.3 + seed * 0.5) * max_drift
			dy = math.sin(phase * 2.1) * max_drift * 0.12

			if stage >= 4:
				to_src_x = loop.base_x - eye[0]
				to_src_z = loop.base_z - eye[2]
				dist = max(1.0, (to_src_x * to_src_x + to_src_z * to_src_z) ** 0.5)
				orbit = severity * 1.8
				dx += (-to_src_z / dist) * orbit * math.sin(self._time * 0.9 + seed)
				dz += (to_src_x / dist) * orbit * math.cos(self._time * 0.9 + seed)

			audio.set_loop_position(
				loop.entity_id,
				loop.base_x + dx,
				loop.base_y + dy,
				loop.base_z + dz,
				dt=dt,
			)

	def _update_phantoms(
		self,
		audio: NullsightAudio,
		camera: Camera,
		dt: float,
		*,
		stage: int,
		severity: float,
		room_bounds: tuple[float, float, float, float],
	) -> None:
		"""Spawn short-lived false beacon/hum loops the player may chase."""
		min_x, max_x, min_z, max_z = room_bounds
		eye = camera.eye_position()

		remaining: list[_PhantomSound] = []
		for phantom in self._phantoms:
			phantom.ttl -= dt
			if phantom.ttl <= 0.0:
				audio.despawn_loop(phantom.entity_id)
				continue
			fade = min(1.0, phantom.ttl / 0.6)
			audio.set_loop_gain(phantom.entity_id, phantom.base_gain * fade * audio.spatial_gain_setting)
			remaining.append(phantom)
		self._phantoms = remaining

		if stage < 1:
			return

		cap = min(_MAX_PHANTOMS, stage)
		if len(self._phantoms) >= cap:
			return

		self._spawn_cooldown -= dt
		if self._spawn_cooldown > 0.0:
			return

		angle = _hash01(self._time * 2.3) * math.tau
		distance = 4.0 + _hash01(self._time * 3.1) * (8.0 + stage * 3.0)
		px = eye[0] + math.sin(angle) * distance
		pz = eye[2] + math.cos(angle) * distance
		px = max(min_x + 1.0, min(max_x - 1.0, px))
		pz = max(min_z + 1.0, min(max_z - 1.0, pz))
		py = 1.0 + _hash01(self._time) * 1.4

		asset_key = "beacon" if _hash01(self._time * 0.7) > 0.45 else "hum"
		gain = (0.08 + severity * 0.14) * (0.75 if asset_key == "hum" else 1.0)
		entity_id = audio.spawn_loop(asset_key, px, py, pz, gain=gain, phantom=True)
		if entity_id is None:
			return

		self._phantoms.append(_PhantomSound(entity_id=entity_id, ttl=2.5 + severity * 3.5, base_gain=gain))
		self._spawn_cooldown = 5.5 - stage * 0.9 - severity * 2.4

	def shutdown(self, audio: NullsightAudio) -> None:
		"""Remove ringing and phantom entities before audio teardown."""
		self._clear_phantoms(audio)
		self._silence_ringing()
		self._ring_entity = None
