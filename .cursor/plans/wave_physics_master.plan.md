# Wave Physics Master Plan — NULLSIGHT Acoustics

**Role:** Overview and shared reference. Per-phase scope, tasks, and acceptance live in the linked phase plans below.

---

## Executive summary

NULLSIGHT uses a **hybrid geometric-acoustics pipeline** (Allen–Berkley ISM + AABB specular + UTD diffraction on the game side; Spherion HOA/HRTF + delay taps + Schroeder late bus on the audio side). This backlog maps 21 requested features to physically grounded models, cites standards and literature, and phases implementation so each step is testable at 20 Hz trace rate on wireframe FPS.

**Design principle:** occlusion is **path-native** — no fake volume ducks. Energy partitions follow conservation at surfaces: absorption α, specular (1−α)(1−s), diffuse scatter (1−α)·s (ISO 17497-1 scattering coefficient s).

**CPU budget (20 Hz trace, ~4 sources, small room):** ISM order-3 + AABB order-2 ≈ 200–400 path validations/frame; UTD edges ≈ 16–32; diffuse companion taps ≈ +30%; acceptable on main thread at 20 Hz (~2–5 ms). Spherion audio thread: 4 taps × 3-band split + head shadow ≈ +15% per object vs single LPF tap.

---

## Phase plans

| Phase | Plan | Goal | Status |
|-------|------|------|--------|
| 1 | [wave_physics_phase1.plan.md](wave_physics_phase1.plan.md) | High-ROI wave physics (directional early field, directivity, spectral taps) | ✅ Complete |
| 2 | [wave_physics_phase2.plan.md](wave_physics_phase2.plan.md) | Graded paths, dynamic room, doors, live RT60 | ⏳ In progress |
| 3 | [wave_physics_phase3.plan.md](wave_physics_phase3.plan.md) | Portal graph, flutter echoes, audible diffraction | ⏳ Planned |
| 4 | [wave_physics_phase4.plan.md](wave_physics_phase4.plan.md) | Heavy hybrid (IR, modes, BTM, late rays) | ⏳ Planned |
| 5 | [wave_physics_phase5.plan.md](wave_physics_phase5.plan.md) | Scene polish, material library, QA | ⏳ Planned |

---

## Physics reference (formulas & citations)

### Phenomena vs model class

| Phenomenon | Model | Valid when | NULLSIGHT approach |
|---|---|---|---|
| Direct path / spreading | Geometric | All f | Spherion 1/r + optional τ=d/c |
| Early specular reflections | Image source (ISM) | λ ≪ surface size, f ≳ Schroeder | `reflection.py` ISM 1–3 |
| Diffuse scatter | Energy + Lambert | Same GA regime | Secondary taps, s from ISO 17497 |
| Edge diffraction | UTD (high-f) / BTM (exact wedge) | Shadow zones | UTD-lite now; BTM Phase 4 |
| Air absorption (outdoor) | ISO 9613-1 | Propagation in atmosphere | Spherion + `materials.iso9613` |
| Wall transmission | Mass law + double-leaf MAM | f > f_msm | `materials.transmission_coefficient` |
| Late reverb | Sabine / Eyring (statistical) | Modal overlap, diffuse field | `reverb_zones.py` + Schroeder |
| Room modes (LF) | Wave equation eigenmodes | f < f_schroeder | Phase 4 modal bank |
| Convolution IR | Linear acoustics | Full bandwidth | Phase 4 hybrid per zone |
| Head shadow / ITD | Spherical head (Woodworth/Kuhn) | f ≳ 200 Hz | Spherion `acoustics.cpp` |

### Key formulas

**Propagation delay:** τ = d / c, c ≈ 331.3 + 0.606·T(°C) m/s

**Doppler:** f_r / f_e = (c + v_l,‖) / (c − v_s,‖) (radial along source→listener)

**ISO 9613-1 atmospheric absorption (pure tone):**  
α(f,T,p_a,h) in dB/m; SPL loss over distance s: ΔL = α·s  
Full formula with O₂/N₂ relaxation frequencies f_rO, f_rN (Annex B, ISO 9613-1:1993)

**Mass law (random incidence):** TL ≈ 20·log₁₀(m''·f) − 48 dB (NRC CBD-239)

**Double-leaf MAM resonance:**  
f_msm = (1/2π)·√(s'_g / (ρ_s1·ρ_s2/(ρ_s1+ρ_s2)))  
Below f_msm: coupled single leaf; above: decoupled + higher STL (Bradley; ISO 10140)

**Specular reflection energy:** R_E = (1−α)(1−s); pressure R_p = √R_E (Kuttruff; Vorländer)

**Diffuse scatter energy:** D_E = (1−α)·s (ISO 17497-1: s = 1 − E_specular/E_total)

**Sabine RT60:** T60 = 0.161·V / (A + 4mV), A = Σ α_i S_i (ISO 3382-2 Annex A)

**Eyring RT60:** T60 = 0.161·V / (−S·ln(1−ᾱ) + 4mV) when ᾱ ≳ 0.2

**Schroeder frequency (modal overlap):** f_s ≈ 2000·√(T60/V) Hz

**Room mode (rectangular):** f_{m,n,l} = (c/2)·√((m/L_x)²+(n/L_y)²+(l/L_z)²)

**UTD diffraction coefficient:** D(φ, φ₀, n, k, s', s) — Kouyoumjian & Pathak 1974; Tsingos SIGGRAPH 2001

**BTM finite edge:** secondary sources along edge integral (Svensson et al. 1999) — Phase 4 upgrade

**Woodworth ITD:** τ ≈ (a/c)(θ + sin θ), a = head radius

**Head-shadow ILD:** frequency-dependent, max ~20 dB at 1 kHz (Kuhn / spherical head)

---

## What professional systems do (hybrid pipelines)

### Steam Audio (Valve)
- Embree/RadeonRays scene; simulation: occlusion, reflections, pathing
- **Hybrid reverb:** convolution early IR + parametric late tail (`HybridReverbEstimator`)
- Real-time geometry updates (doors)
- Energy field → IR reconstruction; overlap-save FFT convolution

### ODEON (commercial GA)
- **Transition order TO:** ISM + early scattering below TO; ray-radiosity above
- Vector-based scattering: weighted specular + diffuse from s coefficient
- Late secondary sources with visibility gather
- Energy domain (squared pressure IR)

### Wayverb / Savioja–Svensson
- Image source for early; diffuse via radiosity / scattering coefficients
- Frequency-dependent materials in bands

### Meta XR Audio
- Hybrid geometric + HRTF; simplified statistical late reverb for mobile CPU

### Implication for NULLSIGHT
Real-time wireframe game: **full wave equation (FDTD/BEM) is infeasible**. Physically correct hybrid:
1. **Geometric early** (ISM + scatter + UTD) → directional delay taps
2. **Statistical late** (Sabine/Eyring RT60) → Schroeder bus
3. **Optional IR** per zone for late tail (Phase 4)
4. **Modal correction** below f_schroeder only (Phase 4)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ NULLSIGHT (Python) — 20 Hz AcousticSystem                 │
│  scene.py → occluders, door, geometry_revision              │
│  tracer.py → LOS, transmission, diffraction, reflections    │
│  materials.py → α, s, mass-law, ISO 9613 bands              │
│  directivity.py → source G(θ), arrival directions           │
│  reverb_zones.py → Sabine/Eyring RT60, predelay             │
└──────────────────────────┬──────────────────────────────────┘
                           │ AcousticLink per source
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ nullsight_audio.py (ONLY spherion import)                   │
│  dry_path_gain, obstruction LPF, reflection_taps_extended   │
│  reverb_send, velocity, camera sync                         │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Spherion (C++) — 48 kHz audio thread                        │
│  Doppler, propagation delay, ISO 9613 HF air                │
│  HOA/HRTF encode (dry), 4× directional multiband tap mix    │
│  Head shadow ILD on dry + taps, Schroeder late bus          │
└─────────────────────────────────────────────────────────────┘
```

**Ownership rule:** `nullsight_audio.py` is the **only** NULLSIGHT module that imports `spherion`. Spherion owns inverse-distance, ISO 9613 air, propagation delay, Doppler, HOA/HRTF, and obstruction one-pole LPF. NULLSIGHT `acoustics/` owns geometric LOS, materials, path combiner parameters, and throttled retrace.

---

## Feature matrix (21 items)

| # | Feature | Tier | Phase | Physics | Primary files |
|---|---------|------|-------|---------|---------------|
| 1 | Propagation delay | A | 1 | τ=d/c | settings, Spherion |
| 2 | Doppler | A | 1 | f_r/f_e ratio | settings, nullsight_audio |
| 3 | Outdoor ISO 9613 air | A | 1 | α(f,T,RH) | materials, Spherion atmospheric |
| 4 | Directional early reflections | B | 1 | Arrival az/el → pan+HRTF | reflection, Spherion taps |
| 5 | Source directivity | B | 1 | G(θ) cardioid/horn | directivity, tracer |
| 6 | Diffuse scatter | B | 1 | (1−α)·s Lambert | reflection |
| 7 | Dynamic RT60 | B | 2 | Sabine/Eyring live | reverb_zones, scene |
| 8 | Double-leaf door TL | B | 2 | Mass-law + MAM + slit | materials, portals |
| 9 | Per-band taps | B | 1 | 6-band → 3-band split | reflection, Spherion |
| 10 | Portal graph | C | 3 | Room adjacency graph | acoustics/portals_graph.py |
| 11 | Flutter echoes | C | 3 | Parallel wall ISM pair | reflection |
| 12 | Listener head shadow | C | 1 | Woodworth ILD | Spherion engine |
| 13 | Structure-borne hook | C | 3 | Interface only | acoustics/structure.py |
| 14 | Moving geometry retune | C | 2 | geometry_revision | scene (exists) |
| 15 | Convolution IR / zone | D | 4 | Hybrid early+IR late | Spherion IR + zones |
| 16 | Room modes LF | D | 4 | Eigenmode bank | acoustics/modes.py |
| 17 | Full BTM diffraction | D | 4 | Edge integral | diffraction.py |
| 18 | Stochastic late rays | D | 4 | Path tracing energy | acoustics/radiosity.py |
| 19 | Material variety | E | 1 | Concrete presets | materials |
| 20 | Occluder absorption RT60 | E | 2 | Local α → A_eff | reverb_zones |
| 21 | Reverb predelay | E | 2 | First reflection τ | tracer, reverb_zones |

---

## Spherion API roadmap (cross-phase)

| Phase | API | Status |
|-------|-----|--------|
| 1 | `set_reflection_taps_extended(gains, delays_s, lpfs_hz, azimuths_rad, elevations_rad, band_low, band_mid, band_high)` | ✅ Implemented |
| 1 | `head_shadow_ild_db` on dry path when `enable_head_shadow=True` | ✅ Implemented |
| 2 | `set_late_reverb_predelay_s` | ⏳ Planned |
| 2 | `set_source_directivity(cos_power)` (optional engine-side) | ⏳ Planned |
| 3 | `set_reflection_tap` / multi-tap early cluster | ⏳ Planned |
| 4 | `set_zone_ir(path)` per room zone | ⏳ Planned |

Backward compatible: `set_reflection_taps(g,d,l)` still works (az=0, bands=1).

---

## References

1. ISO 9613-1:1993 — Atmospheric absorption  
2. ISO 3382-2:2008 — RT60 measurement & prediction (Sabine/Eyring)  
3. ISO 17497-1:2004 — Scattering coefficient  
4. Allen & Berkley 1979 — Image method  
5. Kuttruff — Room Acoustics  
6. Vorländer — Auralization  
7. Kouyoumjian & Pathak 1974 — UTD  
8. Tsingos et al. 2001 — Beam tracing diffraction  
9. Svensson et al. 1999 — BTM  
10. Valve Steam Audio docs — Hybrid reverb  
11. ODEON manual — ISM + ray radiosity hybrid  
12. Bradley — Double-leaf STL / MAM resonance  

Also see `spherion_guide.md` for Spherion integration patterns.

---

## Status tracker

| Phase | Status | Session |
|-------|--------|---------|
| Plan | ✅ Complete | 2026-07-08 |
| Phase 1 | ✅ Complete | 2026-07-08 |
| Phase 2 | ⏳ In progress | 2026-07-08 |
| Phase 3 | ⏳ Planned | — |
| Phase 4 | ⏳ Planned | — |
| Phase 5 | ⏳ Planned | — |
