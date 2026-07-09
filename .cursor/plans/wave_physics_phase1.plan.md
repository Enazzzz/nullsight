# Wave Physics — Phase 1: High-ROI Wave Physics

**Status:** ✅ Complete (2026-07-08)  
**Parent:** [wave_physics_master.plan.md](wave_physics_master.plan.md)  
**Next:** [wave_physics_phase2.plan.md](wave_physics_phase2.plan.md)

---

## Goal

Audible directional early field, source beaming, spectral taps, diffuse scatter, and Tier A features (propagation delay, Doppler, outdoor ISO 9613 air, head shadow).

Replace binary “clear or silent” obstruction with a foundation for graded paths in Phase 2.

---

## Features (from master matrix)

| # | Feature | Tier | Physics |
|---|---------|------|---------|
| 1 | Propagation delay | A | τ=d/c |
| 2 | Doppler | A | f_r/f_e ratio |
| 3 | Outdoor ISO 9613 air | A | α(f,T,RH) |
| 4 | Directional early reflections | B | Arrival az/el → pan+HRTF |
| 5 | Source directivity | B | G(θ) cardioid/horn |
| 6 | Diffuse scatter | B | (1−α)·s Lambert |
| 9 | Per-band taps | B | 6-band → 3-band split |
| 12 | Listener head shadow | C | Woodworth ILD |
| 19 | Material variety | E | Concrete presets |

---

## Implementation

| File | Changes |
|------|---------|
| `acoustics/types.py` | `ReflectionTap` + arrival_dir, band_energy, is_diffuse; `AcousticSource` directivity |
| `acoustics/directivity.py` | **NEW** — cardioid, horn, listener angles |
| `acoustics/materials.py` | ISO 9613-1 full α(f); concrete variants; `_FORCE_CONCRETE=False` |
| `acoustics/reflection.py` | Arrival vectors, diffuse taps, per-band energy |
| `acoustics/tracer.py` | Source directivity on dry path |
| `acoustics/system.py` | Smooth extended tap fields |
| `nullsight_audio.py` | `set_reflection_taps_extended`, radio cardioid |
| `settings.py` | Enable doppler + propagation delay defaults |
| `spherion/*` | Tap azimuth, 3-band gains, head shadow on dry+taps |
| `verify_acoustic_trace.py` | Tests for directivity, bands, scatter, materials |

### Spherion API (shipped)

```python
set_reflection_taps_extended(
	entity_id,
	gains, delays_s, lpfs_hz,
	azimuths_rad, elevations_rad,
	band_low, band_mid, band_high,
)
```

- Backward compatible: `set_reflection_taps(g,d,l)` still works (az=0, bands=1)
- Dry path: `head_shadow_ild_db` applied post-HOA when `enable_head_shadow=True` in config

---

## Acceptance

### How to hear

- Stand beside radio: louder on-axis, duller behind cabinet (cardioid)
- Face wall: first reflection from side (tap pans away from source)
- Open door outdoors: HF rolloff at distance (ISO 9613)
- Move fast: pitch shift (Doppler toggle)
- Blocked path: delayed diffuse wash vs sharp specular ping

### Automated tests

| Feature | Test |
|---------|------|
| Propagation delay | Spherion unit |
| Doppler | velocity set |
| ISO 9613 | `test_iso9613_monotonic` |
| Directional taps | `test_tap_arrival_azimuth` |
| Directivity | `test_cardioid_gain` |
| Diffuse scatter | `test_diffuse_tap_energy` |
| Per-band | `test_band_energy_splits` |
| Materials | `test_material_variants` |

### Manual listen

| Feature | Check |
|---------|-------|
| Propagation delay | Clap at 50 m — late arrival |
| Doppler | Drive past beacon — sweep |
| ISO 9613 | Outdoor HF dull at range |
| Directional taps | Reflection pans correctly |
| Directivity | Radio off-axis quieter |
| Diffuse scatter | Soft wash vs mirror ping |
| Per-band | Brick wall dulls HF reflection |
| Materials | Metal vs concrete timbre |
