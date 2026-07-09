# Wave Physics — Phase 2: Dynamic Room & Doors

**Status:** ⏳ In progress (begun 2026-07-08)  
**Parent:** [wave_physics_master.plan.md](wave_physics_master.plan.md)  
**Previous:** [wave_physics_phase1.plan.md](wave_physics_phase1.plan.md)  
**Next:** [wave_physics_phase3.plan.md](wave_physics_phase3.plan.md)  
**Authority:** `spherion_guide.md` for Spherion; this doc for NULLSIGHT `acoustics/` ownership

---

## Goal

Make obstruction **graded** (not binary): partial muffling through thin geometry, wall transmission with material presets, and first-order reflection metadata. Then wire **dynamic room acoustics** — live Sabine/Eyring RT60 on door open, double-leaf door transmission, reverb predelay, and per-occluder absorption.

---

## Features (from master matrix)

| # | Feature | Tier | Physics |
|---|---------|------|---------|
| 7 | Dynamic RT60 | B | Sabine/Eyring live |
| 8 | Double-leaf door TL | B | Mass-law + MAM + slit |
| 14 | Moving geometry retune | C | geometry_revision |
| 20 | Occluder absorption RT60 | E | Local α → A_eff |
| 21 | Reverb predelay | E | First reflection τ |

Plus graded-path foundation (transmission, partial obstruction, reflection metadata).

---

## Phase breakdown

### Phase 2a — Graded paths (DONE)

**Goal:** Replace binary obstruction with material-aware transmission and export reflection metadata.

| Task | File(s) | Status |
|------|---------|--------|
| `AcousticMaterial` presets | `acoustics/materials.py` | ✅ Done |
| `segment_blocking_occluders` | `occlusion.py` | ✅ Done |
| `trace_acoustic_path` | `acoustics/tracer.py` | ✅ Done |
| First-order reflection probe | `acoustics/reflection.py` | ✅ Done |
| Extended `AcousticLink` | `acoustics/types.py` | ✅ Done |
| Smooth transmission/reflection | `acoustics/system.py` | ✅ Done |
| `_effective_obstruction` bridge | `nullsight_audio.py` | ✅ Done |
| Unit tests | `verify_acoustic_trace.py` | ✅ Done |

**Not yet audible:** reflection *delay* (needs Spherion multi-tap; deferred to Phase 3). Reflection gain currently contributes only a subtle HF lift placeholder.

### Phase 2b — Dynamic room & doors (NEXT)

| Task | File(s) | Notes |
|------|---------|-------|
| Live Sabine/Eyring on door open | `acoustics/reverb_zones.py`, `acoustics/scene.py` | Volume + surface area revision |
| Double-leaf door transmission + MAM dip | `acoustics/materials.py`, `acoustics/portals.py` | Mass-law + slit gain |
| Reverb predelay = first reflection delay | `acoustics/tracer.py`, `reverb_zones.py` | τ from ISM probe |
| Occluder furniture local absorption | `acoustics/reverb_zones.py` | Local α → A_eff |
| Per-occluder material tags in level data | `room.py`, `level_builder.py` | Replace `guess_material()` heuristics |
| Portal gain for cracked door | `acoustics/scene.py`, `interactable_door.py` | Angle → transmission multiplier |
| Debug overlay: path_mode per source | `main.py` or dev HUD | Tune by ear |
| Source velocity → Spherion Doppler | `nullsight_audio.py` | Moving phantoms / future entities |

---

## Spherion vs NULLSIGHT ownership

| Spherion (keep) | NULLSIGHT `acoustics/` (own) |
|-----------------|------------------------------|
| Inverse-distance spreading | Geometric LOS / multi-bounce rays |
| ISO 9613 air absorption + atmosphere | Occluder scene (room solids + door panel) |
| Propagation delay lines | Obstruction / transmission / reflection *parameters* |
| Doppler resampling | Material presets, portal rules |
| HOA encode + HRTF convolution | Throttled retrace + smoothing |
| Per-object obstruction one-pole LPF | Path combiner → `set_gain` + `set_obstruction_lowpass_hz` |
| `spatial_bypass` direct stereo | Listener velocity from camera (already in `main.py`) |

**ISO 9613 / distance:** do not duplicate on game side. Obstruction LPF stacks *after* render block, *before* air HF filter. Keep obstruction cutoffs in 200–4000 Hz range.

---

## Spherion API (Phase 2)

| Feature | Where | Rebuild spherion? |
|---------|-------|-------------------|
| Obstruction LPF | Exists: `set_obstruction_lowpass_hz` | No |
| Listener velocity | Exists: `sync_listener_velocity` | No |
| Source velocity | Exists: `StereoObject.set_velocity` | No — wire from game |
| `set_late_reverb_predelay_s` | **New** | **Yes** |
| `set_source_directivity(cos_power)` | Optional engine-side | Maybe |

After native changes: `pip install -e C:\Robotics\newer-non-robotics\spherion`

---

## Architecture (Phase 2 trace flow)

```
main.py (20 Hz when acoustic_obstruction on)
  └─ AcousticSystem.snapshot / update
       └─ trace_acoustic_path (per source)
            ├─ segment_line_of_sight → direct
            ├─ segment_blocking_occluders → transmission / partial
            └─ trace_first_reflection → metadata
  └─ NullsightAudio.apply_acoustic_links
       └─ _effective_obstruction → set_gain + set_obstruction_lowpass_hz
  └─ NullsightAudio.sync_camera(velocity) → Spherion Doppler/delay
```

---

## Test plan

### Automated (`verify_acoustic_trace.py`)

- [x] Clear LOS same side of divider
- [x] Thick wall blocks direct path
- [x] Clear vs blocked `AcousticLink` gains
- [x] Thin wall partial/transmission audibility
- [x] `segment_blocking_occluders` ordering
- [x] Reflection metadata on clear path
- [x] Closed / open door LOS
- [x] Geometry revision on door swing
- [x] `AcousticSystem` snapshot + smooth
- [x] Boundary plane blocks
- [ ] Live RT60 revision on door open
- [ ] Reverb predelay matches first reflection

### Manual (in-game)

- [ ] Stand west of divider — radio clear, full bandwidth
- [ ] Move behind concrete pillar — muffled or silent (thick)
- [ ] Closed door — east sources heavily attenuated
- [ ] Door opening — smooth transition (smoothing tau 120 ms)
- [ ] Crack door slightly — partial bleed
- [ ] Large room outdoor — air absorption only, no false wall muffling
- [ ] Toggle RAY AUDIO off — immediate clear reset
- [ ] Door open — RT60 / reverb character changes audibly

---

## Tuning constants (Phase 2a)

| Constant | Value | Location |
|----------|-------|----------|
| Retrace interval | 50 ms | `acoustics/system.py` |
| Smooth tau | 120 ms | `acoustics/system.py` |
| Partial gain range | 0.15–0.40 | `acoustics/tracer.py` |
| Concrete transmission | ~0.08 gain, 350 Hz | `acoustics/materials.py` |
| Drywall transmission | ~0.38 gain, 950 Hz | `acoustics/materials.py` |
| Max reflection gain | 0.28 | `acoustics/reflection.py` |
| Speed of sound | 343 m/s | `acoustics/reflection.py` |

---

## Acceptance (Phase 2 complete)

- Graded muffling through thin walls and cracked doors (not binary)
- Double-leaf door shows MAM dip in transmission spectrum
- RT60 updates when door opens/closes (room volume + absorption change)
- Reverb predelay tracks first reflection delay
- Furniture/occluder absorption affects local RT60 estimate
