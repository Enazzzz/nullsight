"""Physical acoustic material presets — octave-band absorption and mass-law TL.

References:
- BRE / NDCS absorption tables (painted / unpainted concrete)
- ISO 354 absorption coefficient α (energy)
- Pressure reflection R_p = √(1 − α) for specular GA (Kuttruff / Vorländer)
- Mass-law transmission loss: TL ≈ 20·log10(m''·f) − 48 (NRC CBD-239)
- ISO 9613-2 air attenuation (band centers) for excess-path loss
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from occlusion import Aabb

# Standard octave-band centers used throughout the acoustics stack (Hz).
OCTAVE_HZ: tuple[int, ...] = (125, 250, 500, 1000, 2000, 4000)
BAND_COUNT = len(OCTAVE_HZ)

# Speed of sound at 20 °C (m/s).
SPEED_OF_SOUND_MPS = 343.0

# Dense poured concrete bulk density (kg/m³) for surface mass m'' = ρ · thickness.
_CONCRETE_DENSITY_KG_M3 = 2300.0

# Thickness above which airborne transmission is treated as negligible (m).
_THICK_WALL_MIN_M = 0.15

# When True, every resolve/guess returns bunker concrete (NULLSIGHT look).
_FORCE_CONCRETE = False


@dataclass(frozen=True)
class AcousticMaterial:
	"""Octave-band material for geometric acoustics."""

	name: str
	# Energy absorption α per octave band (0–1).
	alpha: tuple[float, ...]
	# Scattering coefficient s — fraction of reflected energy that is diffuse.
	scatter: tuple[float, ...]
	# Bulk density for mass-law TL (kg/m³); 0 = non-mass (use legacy τ).
	density_kg_m3: float = _CONCRETE_DENSITY_KG_M3

	def reflection_energy(self, band: int) -> float:
		"""Specular energy reflection coefficient R_E = (1−α)(1−s) for one band."""
		a = self.alpha[band]
		s = self.scatter[band]
		return max(0.0, (1.0 - a) * (1.0 - s))

	def diffuse_reflection_energy(self, band: int) -> float:
		"""Diffuse scattered energy D_E = (1−α)·s per ISO 17497-1."""
		a = self.alpha[band]
		s = self.scatter[band]
		return max(0.0, (1.0 - a) * s)

	def reflection_pressure(self, band: int) -> float:
		"""Pressure amplitude reflection R_p = √R_E."""
		return math.sqrt(self.reflection_energy(band))

	def mean_reflection_pressure(self) -> float:
		"""Broadband pressure reflection (equal-weight band average)."""
		return sum(self.reflection_pressure(i) for i in range(BAND_COUNT)) / BAND_COUNT

	def mean_alpha(self) -> float:
		"""Mean absorption across octave bands."""
		return sum(self.alpha) / BAND_COUNT


# Painted / sealed bunker concrete (BRE-style low α) — dense, reflective, thick-wall TL.
MATERIAL_CONCRETE = AcousticMaterial(
	name="concrete",
	alpha=(0.01, 0.01, 0.015, 0.02, 0.025, 0.03),
	scatter=(0.02, 0.03, 0.04, 0.06, 0.10, 0.14),
	density_kg_m3=_CONCRETE_DENSITY_KG_M3,
)

# Rough / form-liner concrete — higher HF scatter (ISO 17497 rough surfaces).
MATERIAL_CONCRETE_ROUGH = AcousticMaterial(
	name="concrete_rough",
	alpha=(0.02, 0.03, 0.04, 0.05, 0.06, 0.08),
	scatter=(0.08, 0.12, 0.18, 0.22, 0.28, 0.32),
	density_kg_m3=_CONCRETE_DENSITY_KG_M3,
)

# Painted sealed concrete — lower scatter, slightly higher HF absorption.
MATERIAL_CONCRETE_PAINTED = AcousticMaterial(
	name="concrete_painted",
	alpha=(0.02, 0.02, 0.03, 0.03, 0.04, 0.05),
	scatter=(0.01, 0.02, 0.03, 0.05, 0.08, 0.10),
	density_kg_m3=_CONCRETE_DENSITY_KG_M3,
)

# Thin metal door panel — high reflection, moderate scatter.
MATERIAL_METAL_DOOR = AcousticMaterial(
	name="metal_door",
	alpha=(0.04, 0.03, 0.03, 0.03, 0.02, 0.02),
	scatter=(0.05, 0.06, 0.08, 0.10, 0.12, 0.15),
	density_kg_m3=7800.0,
)

# Kept for authoring / future non-force mode (physically distinct).
MATERIAL_DRYWALL = AcousticMaterial(
	name="drywall",
	alpha=(0.29, 0.10, 0.05, 0.04, 0.07, 0.09),
	scatter=(0.10, 0.12, 0.15, 0.18, 0.22, 0.25),
	density_kg_m3=650.0,
)
MATERIAL_WOOD = AcousticMaterial(
	name="wood",
	alpha=(0.15, 0.11, 0.10, 0.07, 0.06, 0.07),
	scatter=(0.08, 0.10, 0.12, 0.15, 0.18, 0.22),
	density_kg_m3=700.0,
)
MATERIAL_GLASS = AcousticMaterial(
	name="glass",
	alpha=(0.18, 0.06, 0.04, 0.03, 0.02, 0.02),
	scatter=(0.01, 0.01, 0.02, 0.02, 0.03, 0.05),
	density_kg_m3=2500.0,
)
MATERIAL_METAL = AcousticMaterial(
	name="metal",
	alpha=(0.05, 0.04, 0.03, 0.03, 0.02, 0.02),
	scatter=(0.02, 0.03, 0.04, 0.05, 0.08, 0.12),
	density_kg_m3=7800.0,
)

# BRE / NDCS-style soft finishes (Phase 5 material library).
MATERIAL_CARPET = AcousticMaterial(
	name="carpet",
	alpha=(0.08, 0.24, 0.57, 0.69, 0.71, 0.73),
	scatter=(0.15, 0.20, 0.25, 0.30, 0.35, 0.40),
	density_kg_m3=200.0,
)
MATERIAL_CURTAIN = AcousticMaterial(
	name="curtain",
	alpha=(0.07, 0.31, 0.49, 0.75, 0.70, 0.60),
	scatter=(0.20, 0.25, 0.30, 0.35, 0.40, 0.45),
	density_kg_m3=150.0,
)
MATERIAL_PLASTER = AcousticMaterial(
	name="plaster",
	alpha=(0.02, 0.02, 0.03, 0.04, 0.05, 0.05),
	scatter=(0.02, 0.03, 0.04, 0.05, 0.08, 0.10),
	density_kg_m3=1200.0,
)
MATERIAL_BRICK = AcousticMaterial(
	name="brick",
	alpha=(0.03, 0.03, 0.03, 0.04, 0.05, 0.07),
	scatter=(0.05, 0.08, 0.10, 0.12, 0.15, 0.18),
	density_kg_m3=1900.0,
)

# Air-gap stiffness for double-leaf MAM (N/m³): s'_g = ρ₀ c² / d_gap.
_AIR_DENSITY_KG_M3 = 1.21
# Typical hollow metal door air cavity (m).
_DEFAULT_DOOR_AIR_GAP_M = 0.05
# Leaf surface densities for a hollow metal door (kg/m²) when thickness is panel total.
_DEFAULT_DOOR_LEAF_MS_KG_M2 = 12.0

_DEFAULT_MATERIAL = MATERIAL_CONCRETE

_MATERIALS_BY_NAME: dict[str, AcousticMaterial] = {
	MATERIAL_CONCRETE.name: MATERIAL_CONCRETE,
	"concrete_rough": MATERIAL_CONCRETE_ROUGH,
	"concrete_painted": MATERIAL_CONCRETE_PAINTED,
	"metal_door": MATERIAL_METAL_DOOR,
	MATERIAL_DRYWALL.name: MATERIAL_DRYWALL,
	MATERIAL_WOOD.name: MATERIAL_WOOD,
	MATERIAL_GLASS.name: MATERIAL_GLASS,
	MATERIAL_METAL.name: MATERIAL_METAL,
	MATERIAL_CARPET.name: MATERIAL_CARPET,
	MATERIAL_CURTAIN.name: MATERIAL_CURTAIN,
	MATERIAL_PLASTER.name: MATERIAL_PLASTER,
	MATERIAL_BRICK.name: MATERIAL_BRICK,
}

# ISO 9613-2 Table 2 — atmospheric attenuation a [dB/km] at ~20 °C / 70 % RH.
_AIR_ABS_DB_PER_KM: tuple[float, ...] = (0.3, 1.1, 2.8, 5.0, 9.0, 22.9)


def material_by_name(name: str | None) -> AcousticMaterial:
	"""Look up a material preset by name; unknown names fall back to concrete."""
	if _FORCE_CONCRETE:
		return MATERIAL_CONCRETE
	if not name:
		return _DEFAULT_MATERIAL
	return _MATERIALS_BY_NAME.get(name.lower().strip(), _DEFAULT_MATERIAL)


def _aabb_extent(bounds: Aabb) -> tuple[float, float, float]:
	"""Return axis lengths of an AABB."""
	box_min, box_max = bounds
	return (
		box_max[0] - box_min[0],
		box_max[1] - box_min[1],
		box_max[2] - box_min[2],
	)


def aabb_thickness_m(bounds: Aabb) -> float:
	"""Smallest axis extent — proxy for wall thickness along the blocking normal."""
	ex, ey, ez = _aabb_extent(bounds)
	return min(ex, ey, ez)


def guess_material(bounds: Aabb) -> AcousticMaterial:
	"""Infer a material preset from occluder proportions (fallback when untagged)."""
	if _FORCE_CONCRETE:
		return MATERIAL_CONCRETE
	thickness = aabb_thickness_m(bounds)
	ex, ey, ez = _aabb_extent(bounds)
	tall = ey > 2.0
	slab = ez < 0.25 or ex < 0.25

	if thickness <= 0.18 and tall:
		return MATERIAL_DRYWALL
	if thickness <= 0.18 and slab:
		return MATERIAL_GLASS
	if thickness >= 0.55:
		return MATERIAL_CONCRETE
	if thickness <= 0.12:
		return MATERIAL_WOOD
	return _DEFAULT_MATERIAL


def resolve_material(bounds: Aabb, material_name: str | None = None) -> AcousticMaterial:
	"""Prefer an authored material name; otherwise guess from AABB proportions."""
	if _FORCE_CONCRETE:
		return MATERIAL_CONCRETE
	if material_name:
		return material_by_name(material_name)
	return guess_material(bounds)


def mass_law_stl_db(surface_density_kg_m2: float, freq_hz: float) -> float:
	"""Random-incidence mass-law sound transmission loss in dB (NRC CBD-239)."""
	ms = max(surface_density_kg_m2, 1.0)
	f = max(freq_hz, 20.0)
	return 20.0 * math.log10(ms * f) - 48.0


def air_gap_stiffness(gap_m: float) -> float:
	"""Air-cavity stiffness s'_g = ρ₀ c² / d (N/m³) for double-leaf MAM."""
	d = max(gap_m, 0.005)
	return (_AIR_DENSITY_KG_M3 * SPEED_OF_SOUND_MPS * SPEED_OF_SOUND_MPS) / d


def mam_resonance_hz(
	surface_density_1: float,
	surface_density_2: float,
	*,
	gap_m: float = _DEFAULT_DOOR_AIR_GAP_M,
) -> float:
	"""Mass-air-mass resonance f_msm = (1/2π)·√(s'_g / μ) with μ = ρ1ρ2/(ρ1+ρ2).

	Bradley / ISO 10140 double-leaf model. Below f_msm the leaves act as one
	coupled mass; above, STL rises faster than single-leaf mass law.
	"""
	ms1 = max(surface_density_1, 0.5)
	ms2 = max(surface_density_2, 0.5)
	mu = (ms1 * ms2) / (ms1 + ms2)
	s_g = air_gap_stiffness(gap_m)
	return (1.0 / (2.0 * math.pi)) * math.sqrt(s_g / mu)


def double_leaf_stl_db(
	surface_density_1: float,
	surface_density_2: float,
	freq_hz: float,
	*,
	gap_m: float = _DEFAULT_DOOR_AIR_GAP_M,
) -> float:
	"""Double-leaf STL with MAM resonance dip (Bradley / ISO 10140).

	f ≪ f_msm: coupled single-leaf mass law on (ms1+ms2).
	f ≈ f_msm: transmission peak — STL dips ~12 dB (mass-air-mass resonance).
	f ≫ f_msm: decoupled leaves; STL rises faster than single-leaf (+6 dB/oct-ish).
	"""
	f = max(freq_hz, 20.0)
	ms1 = max(surface_density_1, 0.5)
	ms2 = max(surface_density_2, 0.5)
	f_msm = mam_resonance_hz(ms1, ms2, gap_m=gap_m)
	coupled = mass_law_stl_db(ms1 + ms2, f)

	# Lorentzian dip around f_msm — cavity resonance opens a transmission window.
	# Narrow half-width keeps far-below frequencies near coupled mass law.
	half_width = max(6.0, 0.18 * f_msm)
	dip_shape = 1.0 / (1.0 + ((f - f_msm) / half_width) ** 2)
	dip_db = 12.0 * dip_shape

	# Decoupled leaf sum (valid well above f_msm).
	leaf1 = mass_law_stl_db(ms1, f)
	leaf2 = mass_law_stl_db(ms2, f)
	ratio = f / max(f_msm, 1.0)
	boost = 6.0 * math.log10(max(ratio, 1.0))
	decoupled = leaf1 + leaf2 + boost

	# Blend coupled → decoupled above resonance; keep coupled as the dip baseline
	# so the MAM notch is measured against the sealed-panel curve (not leaf1+leaf2).
	if f <= f_msm:
		baseline = coupled
	else:
		# Fully decoupled by ~3·f_msm.
		w = min(1.0, (ratio - 1.0) / 2.0)
		baseline = coupled * (1.0 - w) + max(coupled, decoupled) * w

	return max(0.0, baseline - dip_db)


def transmission_coefficient(
	material: AcousticMaterial,
	thickness_m: float,
	*,
	double_leaf: bool = False,
	air_gap_m: float = _DEFAULT_DOOR_AIR_GAP_M,
) -> tuple[float, float]:
	"""Return (pressure_gain, lpf_hz) for airborne transmission through a solid.

	Thick concrete (≳150 mm) has TL ≳ 45 dB → τ ≈ 0 (inaudible). Thin partitions
	use mass-law; ``metal_door`` / ``double_leaf=True`` use MAM double-leaf STL.
	"""
	thickness = max(thickness_m, 0.02)
	# Thick bunker walls: no airborne path — use portals / diffraction instead.
	if thickness >= _THICK_WALL_MIN_M and material.density_kg_m3 >= 1800.0 and not double_leaf:
		return 0.0, 200.0

	use_mam = double_leaf or material.name == "metal_door"
	if use_mam:
		# Hollow door: two thin leaves with air gap (not solid steel slab).
		ms_leaf = _DEFAULT_DOOR_LEAF_MS_KG_M2
		if material.density_kg_m3 > 0.0 and thickness < 0.12:
			# Split panel mass into two equal leaves when thickness is known.
			ms_leaf = max(4.0, 0.5 * material.density_kg_m3 * thickness)
		stl_bands = [
			double_leaf_stl_db(ms_leaf, ms_leaf, float(f), gap_m=air_gap_m) for f in OCTAVE_HZ
		]
	else:
		ms = material.density_kg_m3 * thickness
		stl_bands = [mass_law_stl_db(ms, float(f)) for f in OCTAVE_HZ]

	tau_bands = [10.0 ** (-stl / 10.0) for stl in stl_bands]
	mid_tau = sum(tau_bands[2:5]) / 3.0
	pressure_gain = math.sqrt(max(mid_tau, 0.0))
	stl_1k = stl_bands[3]
	lpf_hz = max(180.0, min(4_000.0, 8_000.0 * (10.0 ** (-stl_1k / 40.0))))
	return min(0.35, pressure_gain), lpf_hz


def transmission_through_solid(
	material: AcousticMaterial,
	thickness_m: float,
	*,
	double_leaf: bool = False,
	air_gap_m: float = _DEFAULT_DOOR_AIR_GAP_M,
) -> tuple[float, float]:
	"""Compatibility alias — mass-law / MAM transmission through one occluder."""
	return transmission_coefficient(
		material,
		thickness_m,
		double_leaf=double_leaf,
		air_gap_m=air_gap_m,
	)


def iso9613_alpha_db_per_m(
	freq_hz: float,
	*,
	temperature_c: float = 20.0,
	relative_humidity_percent: float = 50.0,
	pressure_pa: float = 101_325.0,
) -> float:
	"""ISO 9613-1:1993 atmospheric absorption coefficient α in dB/m.

	Uses the full O₂/N₂ relaxation model (Annex B) — not a linear distance fudge.
	"""
	f = max(freq_hz, 50.0)
	t_k = temperature_c + 273.15
	t0 = 293.15
	pr = 101_325.0
	pa = max(pressure_pa, 1.0)

	# Saturation pressure (Buck-style, valid over game temperature range).
	psat = 10.0 ** (
		-6.8346 * (t0 / t_k) ** 1.261 + 4.6151
	)
	h = relative_humidity_percent * (psat / pa) * 100.0 / 100.0

	# Oxygen / nitrogen relaxation frequencies (Hz).
	fr_o = (pa / pr) * (24.0 + 4.04e4 * h * (0.02 + h) / (0.391 + h))
	fr_n = (pa / pr) * math.sqrt(t_k / t0) * (
		9.0 + 280.0 * h * math.exp(-4.17 * ((t_k / t0) ** (-1.0 / 3.0) - 1.0))
	)

	f2 = f * f
	term_visc = 1.84e-11 * (pr / pa) * math.sqrt(t0 / t_k)
	term_relax = (t_k / t0) ** -2.5 * (
		0.01275 * math.exp(-2239.1 / t_k) / (fr_o + f2 / fr_o)
		+ 0.1068 * math.exp(-3352.0 / t_k) / (fr_n + f2 / fr_n)
	)
	return 8.686 * f2 * (term_visc + term_relax)


def air_absorption_db(
	path_length_m: float,
	*,
	temperature_c: float = 20.0,
	relative_humidity_percent: float = 50.0,
) -> tuple[float, ...]:
	"""Per-octave-band atmospheric absorption in dB for ``path_length_m``.

	Uses ISO 9613-1 at each band centre (distinct from geometric 1/r spreading).
	"""
	d = max(path_length_m, 0.0)
	return tuple(
		iso9613_alpha_db_per_m(
			float(f),
			temperature_c=temperature_c,
			relative_humidity_percent=relative_humidity_percent,
		)
		* d
		for f in OCTAVE_HZ
	)


def air_absorption_db_legacy(path_length_m: float) -> tuple[float, ...]:
	"""ISO 9613-2 Table 2 fallback at ~20 °C / 70 % RH (dB per path)."""
	d_km = max(path_length_m, 0.0) / 1000.0
	return tuple(a * d_km for a in _AIR_ABS_DB_PER_KM)


def apply_air_absorption_energy(
	energy: list[float],
	path_length_m: float,
	*,
	temperature_c: float = 20.0,
	relative_humidity_percent: float = 50.0,
) -> list[float]:
	"""Multiply per-band energy by 10^(−ΔL/10) using ISO 9613-1."""
	losses = air_absorption_db(
		path_length_m,
		temperature_c=temperature_c,
		relative_humidity_percent=relative_humidity_percent,
	)
	return [e * (10.0 ** (-loss / 10.0)) for e, loss in zip(energy, losses)]


def band_energy_to_three_band_gains(
	energy: tuple[float, ...] | list[float],
) -> tuple[float, float, float]:
	"""Map 6 octave-band energies to low/mid/high pressure gains for Spherion.

	Low = 125+250 Hz, mid = 500+1000 Hz, high = 2000+4000 Hz.
	"""
	e = list(energy)
	if len(e) < BAND_COUNT:
		e = e + [0.0] * (BAND_COUNT - len(e))
	low_e = max(e[0], 0.0) + max(e[1], 0.0)
	mid_e = max(e[2], 0.0) + max(e[3], 0.0)
	high_e = max(e[4], 0.0) + max(e[5], 0.0)
	total = low_e + mid_e + high_e
	if total <= 1e-12:
		return 1.0, 1.0, 1.0
	# Pressure gains ∝ √(band energy share).
	return (
		math.sqrt(low_e / total) * math.sqrt(3.0),
		math.sqrt(mid_e / total) * math.sqrt(3.0),
		math.sqrt(high_e / total) * math.sqrt(3.0),
	)


def apply_surface_reflection(energy: list[float], material: AcousticMaterial) -> list[float]:
	"""Apply one specular bounce: E ← E · R_E per band."""
	return [e * material.reflection_energy(i) for i, e in enumerate(energy)]


def apply_diffuse_surface_reflection(energy: list[float], material: AcousticMaterial) -> list[float]:
	"""Apply one diffuse bounce: E ← E · D_E per band (ISO 17497 scatter fraction)."""
	return [e * material.diffuse_reflection_energy(i) for i, e in enumerate(energy)]


def energy_to_pressure_gain(energy: list[float]) -> float:
	"""Convert 6-band energy to a broadband pressure gain (√ΣE / √N)."""
	total = sum(max(e, 0.0) for e in energy)
	return math.sqrt(total / BAND_COUNT)


def energy_to_lpf_hz(energy: list[float]) -> float:
	"""Map HF energy loss to a one-pole cutoff for Spherion."""
	if not energy or sum(energy) <= 1e-12:
		return 400.0
	# Energy-weighted geometric mean of band centers.
	log_sum = 0.0
	w_sum = 0.0
	for e, f in zip(energy, OCTAVE_HZ):
		w = max(e, 0.0)
		if w <= 0.0:
			continue
		log_sum += w * math.log(float(f))
		w_sum += w
	if w_sum <= 0.0:
		return 400.0
	return max(300.0, min(18_000.0, math.exp(log_sum / w_sum) * 2.5))
