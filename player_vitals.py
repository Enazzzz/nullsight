"""Player health, stamina, and scanner integrity."""

from __future__ import annotations

# Sprint speed boost when shift is held and stamina remains.
SPRINT_SPEED_MULTIPLIER = 1.75
# Stamina drain only while sprinting; regen whenever not sprinting.
STAMINA_DRAIN_SPRINT_PER_SEC = 28.0
STAMINA_REGEN_PER_SEC = 22.0

# Scanner does not regenerate — use the R-key repair minigame.
SCANNER_MIN_TO_SCAN = 1.0
# No scan wireframe output below this integrity ratio.
SCANNER_MIN_VISUAL_RATIO = 0.25

# Passive health regen when damage systems are added.
HEALTH_REGEN_PER_SEC = 0.0


class PlayerVitals:
	"""Runtime pools for the gameplay HUD."""

	def __init__(
		self,
		health: float = 100.0,
		stamina: float = 100.0,
		scanner: float = 100.0,
		max_health: float = 100.0,
		max_stamina: float = 100.0,
		max_scanner: float = 100.0,
	) -> None:
		self.max_health = max_health
		self.max_stamina = max_stamina
		self.max_scanner = max_scanner
		self.health = health
		self.stamina = stamina
		self.scanner = scanner
		self._health_damage_rate = 0.0
		self._scanner_damage_rate = 0.0

	def reset(self) -> None:
		"""Restore all pools to full (new run or return to title)."""
		self.health = self.max_health
		self.stamina = self.max_stamina
		self.scanner = self.max_scanner
		self._health_damage_rate = 0.0
		self._scanner_damage_rate = 0.0

	def update(self, dt: float, *, sprinting: bool) -> None:
		"""Regenerate pools; stamina drains only while sprinting."""
		if dt <= 0.0:
			return

		if sprinting and self.stamina > 0.0:
			self.stamina = max(0.0, self.stamina - STAMINA_DRAIN_SPRINT_PER_SEC * dt)
		else:
			self.stamina = min(self.max_stamina, self.stamina + STAMINA_REGEN_PER_SEC * dt)

		if HEALTH_REGEN_PER_SEC > 0.0 and self.health < self.max_health:
			self.health = min(self.max_health, self.health + HEALTH_REGEN_PER_SEC * dt)

		# Decay burst trackers used for ringing / scan glitch spikes.
		decay = 4.5 * dt
		self._health_damage_rate = max(0.0, self._health_damage_rate - decay)
		self._scanner_damage_rate = max(0.0, self._scanner_damage_rate - decay)

	def sprint_speed_multiplier(self, *, sprinting: bool) -> float:
		"""Return locomotion scale for sprint (1.0 = normal walk)."""
		if not sprinting or self.stamina <= 0.0:
			return 1.0
		return SPRINT_SPEED_MULTIPLIER

	def can_scan(self) -> bool:
		"""Return True when scanner integrity allows a pulse."""
		return self.scanner >= SCANNER_MIN_TO_SCAN

	def apply_damage(self, amount: float) -> None:
		"""Reduce player health (hazards, combat, etc.)."""
		if amount <= 0.0:
			return
		self.health = max(0.0, self.health - amount)
		# Burst tracker for tinnitus — scales recent hit size, decays in update().
		self._health_damage_rate = min(320.0, self._health_damage_rate + amount * 22.0)

	def apply_scanner_damage(self, amount: float) -> None:
		"""Reduce scanner integrity (environmental damage, overload, etc.)."""
		if amount <= 0.0:
			return
		self.scanner = max(0.0, self.scanner - amount)
		self._scanner_damage_rate = min(240.0, self._scanner_damage_rate + amount * 18.0)

	def repair_scanner(self, amount: float) -> None:
		"""Restore scanner integrity from the manual repair minigame."""
		if amount <= 0.0:
			return
		self.scanner = min(self.max_scanner, self.scanner + amount)

	@property
	def health_damage_rate(self) -> float:
		"""Recent health loss burst (HP/s equivalent) for auditory ringing."""
		return self._health_damage_rate

	@property
	def scanner_damage_rate(self) -> float:
		"""Recent scanner damage burst for extra visual glitch intensity."""
		return self._scanner_damage_rate

	@property
	def health_ratio(self) -> float:
		"""Normalized health for HUD fill."""
		return 0.0 if self.max_health <= 0.0 else self.health / self.max_health

	@property
	def stamina_ratio(self) -> float:
		"""Normalized stamina for HUD fill."""
		return 0.0 if self.max_stamina <= 0.0 else self.stamina / self.max_stamina

	@property
	def scanner_ratio(self) -> float:
		"""Normalized scanner integrity for HUD fill."""
		return 0.0 if self.max_scanner <= 0.0 else self.scanner / self.max_scanner

	def needs_scanner_repair(self) -> bool:
		"""Return True when scanner integrity is below maximum."""
		return self.scanner < self.max_scanner - 1e-4

	def scanner_has_visual_output(self) -> bool:
		"""Return False when integrity is too low for any scan picture."""
		return self.scanner_ratio >= SCANNER_MIN_VISUAL_RATIO
