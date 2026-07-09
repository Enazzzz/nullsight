# NULLSIGHT Acoustic Physics — Phase 2+ Plan

**Status:** Phase 2 begun (2026-07-08)  
**Supersedes:** Nothing — complements `raytraced_audio_mvp` (v1 shipped)  
**Authority:** `spherion_guide.md` for Spherion; this doc for NULLSIGHT `acoustics/` ownership

---

## Executive summary

NULLSIGHT v1 raytraced audio proves the pipeline: geometric direct-path LOS against full room solids, per-source gain duck + obstruction LPF via Spherion, 20 Hz throttled retrace, door dynamics, and settings toggle. It sounds *binary* — sources are either clear or silent.

Phase 2 makes obstruction **graded**: partial muffling through thin geometry, **wall transmission** with material presets, and **first-order reflection metadata** (image source across room boundaries). The Spherion bridge centralizes how direct + transmission paths merge into `set_gain` / `set_obstruction_lowpass_hz`.

Phase 3–4 add audible reflection delay taps, diffraction approximations, portal-aware paths, and optional hybrid IR late reverb — within CPU budgets of a wireframe first-person game (~20 Hz game-side trace, block-rate Spherion physics).

**Shipped this session (Phase 2a):**

| Item | Files |
|------|-------|
| Material table + thickness inference | `acoustics/materials.py` |
| Extended `AcousticLink` (transmission + reflection fields) | `acoustics/types.py` |
| Partial obstruction + transmission + reflection trace | `acoustics/tracer.py`, `acoustics/reflection.py` |
| Segment blocker enumeration | `occlusion.py` → `segment_blocking_occluders` |
| Smoothed multi-path links | `acoustics/system.py` |
| Centralized obstruction merge | `nullsight_audio.py` → `_effective_obstruction` |
| Tests | `verify_acoustic_trace.py` |

**Not yet audible:** reflection *delay* (needs Spherion multi-tap or game-side send bus). Reflection gain currently contributes only a subtle HF lift placeholder.

---

## Physics reference (condensed)

### What v1 modeled

| Phenomenon | v1 behavior |
|------------|-------------|
| Direct path | Binary LOS ray (AABB + boundary planes) |
| Obstruction | `direct_gain` 0 or 1; LPF 18 kHz or 400 Hz |
| Distance | Spherion inverse-distance (1/r pressure) |
| Air absorption | Spherion ISO 9613 broadband + HF one-pole (fixed indoor atmosphere unless outdoor toggle) |
| HRTF / HOA | Spherion SN3D + KEMAR SOFA |
| Propagation delay | Spherion optional (default off) |
| Doppler | Spherion optional (default off); listener velocity wired from camera |
| Room acoustics | None |

### What real acoustics needs (priority order for CPU wireframe game)

1. **Graded obstruction** — not binary; muffling scales with material, thickness, grazing angle, and proximity to surfaces. *(Phase 2a: partial + transmission)*
2. **Early reflections (1–3 bounces)** — image-source or ray bounce; delay = excess path length / c. Dominates indoor “roominess” before reverb tail. *(Phase 2b metadata; Phase 3 audible tap)*
3. **Late reverb / energy decay** — statistical tail (RT60) or precomputed/hybrid IR. Steam Audio uses hybrid: geometric early + convolution late. *(Phase 4)*
4. **Diffraction around edges** — sound bends past door frames and corners; critical for “I hear them around the corner.” UTD-style or portal graph. *(Phase 3)*
5. **Portals / openings** — explicit acoustic connections between cells (doorways, windows). NULLSIGHT door panel dynamics are a start; need portal gain when door cracked. *(Phase 3)*
6. **Transmission through walls** — mass-law-ish attenuation per material; multi-layer multiply. *(Phase 2a)*
7. **Ground / ceiling reflection** — first-order specular on large planes. *(Phase 2a reflection metadata)*
8. **Air absorption along path** — Spherion already applies vs listener distance; game-side should not duplicate. Obstruction LPF is *extra* muffling, not air loss.
9. **Doppler + propagation delay** — Spherion owns; enable per biome (outdoor artillery vs indoor FPS).

### Spherion owns vs NULLSIGHT owns

| Spherion (keep) | NULLSIGHT `acoustics/` (own) |
|-----------------|------------------------------|
| Inverse-distance spreading | Geometric LOS / multi-bounce rays |
| ISO 9613 air absorption + atmosphere | Occluder scene (room solids + door panel) |
| Propagation delay lines | Obstruction / transmission / reflection *parameters* |
| Doppler resampling | Material presets, portal rules |
| HOA encode + HRTF convolution | Throttled retrace + smoothing |
| Per-object obstruction one-pole LPF | Path combiner → `set_gain` + `set_obstruction_lowpass_hz` |
| `spatial_bypass` direct stereo | Listener velocity from camera (already in `main.py`) |

**Rule:** `nullsight_audio.py` is the **only** NULLSIGHT module that imports `spherion`.

### Industry patterns

| System | Pattern | NULLSIGHT analogue |
|--------|---------|-------------------|
| **Steam Audio** | Hybrid: geometric paths + baked/probe IR | Phase 4: optional small IR send per room zone |
| **Geometric acoustics** | Image sources, ray clusters, 20–60 Hz update | Phase 2–3: 20 Hz trace, 1–2 bounces |
| **Meta XR Audio** | Simplified portals + diffraction | Phase 3: doorway portal graph |
| **FMOD / Wwise** | Game-driven occlusion API + designer reverb | Spherion dry bus + game `AcousticLink` |
| **Hybrid IR + ray** | Early rays → tap delays; late → convolution | Phase 3 tap + Phase 4 bus IR |

### ISO 9613 / distance (Spherion — do not duplicate)

- Pressure gain ∝ 1/r beyond `min_distance_m` (mode 1).
- Broadband loss: `dB/m × distance` at 1 kHz from effective atmosphere.
- HF one-pole anchored at 4 kHz when `enable_hf_air_absorption=True`.
- Obstruction LPF in Spherion runs **after** `render_block`, **before** air HF filter — both stack. Keep obstruction cutoffs in 200–4000 Hz range; air absorption handles distance dulling.

---

## Phase breakdown

### Phase 2a — Graded paths (IN PROGRESS)

**Goal:** Replace binary obstruction with material-aware transmission and export reflection metadata.

| Task | File(s) | Status |
|------|---------|--------|
| `AcousticMaterial` presets | `acoustics/materials.py` | Done |
| `segment_blocking_occluders` | `occlusion.py` | Done |
| `trace_acoustic_path` | `acoustics/tracer.py` | Done |
| First-order reflection probe | `acoustics/reflection.py` | Done |
| Extended `AcousticLink` | `acoustics/types.py` | Done |
| Smooth transmission/reflection | `acoustics/system.py` | Done |
| `_effective_obstruction` bridge | `nullsight_audio.py` | Done |
| Unit tests | `verify_acoustic_trace.py` | Done |

**Phase 2b — Remaining (next session)**

| Task | File(s) | Notes |
|------|---------|-------|
| Per-occluder material tags in level data | `room.py`, `level_builder.py` | Replace `guess_material()` heuristics |
| Portal gain for cracked door | `acoustics/scene.py`, `interactable_door.py` | Angle → transmission multiplier |
| Debug overlay: path_mode per source | `main.py` or dev HUD | Tune by ear |
| Source velocity → Spherion Doppler | `nullsight_audio.py` | Moving phantoms / future entities |

### Phase 3 — Audible reflections + diffraction

**Goal:** Hear early reflections with correct delay; approximate corner diffraction.

| Task | File(s) | Spherion change? |
|------|---------|------------------|
| **Reflection delay tap** per `StereoObject` | `spherion/src/stereo_object.cpp`, `engine.cpp` | **Yes** — 1-tap feed-forward delay + gain |
| Wire `reflection_gain/delay/lpf` | `nullsight_audio.py`, `audio_world.py` | Python binding |
| 2nd-order reflection (optional) | `acoustics/reflection.py` | Game only |
| Edge diffraction (UTD-lite) | `acoustics/diffraction.py` (new) | Game only — extra gain path |
| Acoustic portals graph | `acoustics/portals.py` (new) | Game only |
| Cracked-door low-pass curve | `acoustics/scene.py` | Game only |

**Spherion API sketch (Phase 3):**

```python
world.set_reflection_tap(entity_id, gain, delay_s, lpf_hz)  # 0 gain = off
```

Implementation: small ring buffer per object (reuse fractional delay infra), mixed parallel to dry path before obstruction filter.

### Phase 4 — Late reverb hybrid

**Goal:** Indoor spaces stop sounding anechoic without full wave simulation.

| Task | File(s) | Notes |
|------|---------|-------|
| Zone RT60 / preset table | `acoustics/reverb_zones.py` | Per `RoomData.name` |
| Send bus + Schroeder or IR | Spherion engine or external | Small stereo convolver on wet send |
| Energy coupling from ray hits | `acoustics/tracer.py` | Reflection energy → reverb send level |
| Outdoor vs indoor auto | `nullsight_audio.py` | Disable reverb on `large` outdoor |

---

## Spherion API extensions vs game-side only

| Feature | Where | Rebuild spherion? |
|---------|-------|-------------------|
| Obstruction LPF | Exists: `set_obstruction_lowpass_hz` | No |
| Listener velocity | Exists: `sync_listener_velocity` | No |
| Source velocity | Exists: `StereoObject.set_velocity` | No — wire from game |
| Reflection delay tap | **New** | **Yes** |
| Multi-tap early cluster (3 taps) | **New** (optional) | **Yes** |
| Wet reverb send bus | **New** (optional) | **Yes** |
| Materials / rays / portals | `acoustics/*` | No |
| Path merge logic | `nullsight_audio.py` | No |

After native changes: `pip install -e C:\Robotics\newer-non-robotics\spherion`

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

### Manual (in-game)

- [ ] Stand west of divider — radio clear, full bandwidth
- [ ] Move behind concrete pillar — muffled or silent (thick)
- [ ] Closed door — east sources heavily attenuated
- [ ] Door opening — smooth transition (smoothing tau 120 ms)
- [ ] Crack door slightly (Phase 2b) — partial bleed
- [ ] Large room outdoor — air absorption only, no false wall muffling
- [ ] Toggle RAY AUDIO off — immediate clear reset

### Future (`verify_acoustic_reflection.py`)

- Image-source delay matches path length excess ±1 ms
- Diffraction adds gain when LOS blocked but portal open

---

## Architecture diagram

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

## References

- `spherion_guide.md` — Acoustic physics reference, atmospheric API, NULLSIGHT integration patterns
- ISO 9613-1:1993 — air absorption (implemented in Spherion)
- Steam Audio documentation — hybrid geometric + IR philosophy
- Savioja & Svensson, “Overview of geometrical room acoustic modeling techniques” — image source / ray tracing taxonomy
