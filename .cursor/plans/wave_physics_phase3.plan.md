# Wave Physics — Phase 3: Graph & Flutter

**Status:** ⏳ Planned  
**Parent:** [wave_physics_master.plan.md](wave_physics_master.plan.md)  
**Previous:** [wave_physics_phase2.plan.md](wave_physics_phase2.plan.md)  
**Next:** [wave_physics_phase4.plan.md](wave_physics_phase4.plan.md)

---

## Goal

Hear early reflections with correct delay; approximate corner diffraction; connect rooms via an acoustic portal graph; detect flutter echoes on parallel walls. Within CPU budget: ~20 Hz game-side trace, block-rate Spherion physics.

---

## Features (from master matrix)

| # | Feature | Tier | Physics |
|---|---------|------|---------|
| 10 | Portal graph | C | Room adjacency graph |
| 11 | Flutter echoes | C | Parallel wall ISM pair |
| 13 | Structure-borne hook | C | Interface only |

Plus audible reflection delay taps and UTD-lite diffraction (from Phase 2 metadata → audible).

---

## Implementation

| Task | File(s) | Spherion change? |
|------|---------|------------------|
| **Reflection delay tap** per `StereoObject` | `spherion/src/stereo_object.cpp`, `engine.cpp` | **Yes** — 1-tap feed-forward delay + gain |
| Wire `reflection_gain/delay/lpf` | `nullsight_audio.py`, `audio_world.py` | Python binding |
| Multi-tap early cluster (up to 4) | `spherion/*`, `nullsight_audio.py` | **Yes** (optional) |
| 2nd-order reflection | `acoustics/reflection.py` | Game only |
| Edge diffraction (UTD-lite) | `acoustics/diffraction.py` | Game only — extra gain path |
| Acoustic portal graph | `acoustics/portals_graph.py`, `acoustics/portals.py` | Game only |
| Cracked-door low-pass curve | `acoustics/scene.py` | Game only |
| Flutter echo detection | `acoustics/reflection.py` | Parallel walls → periodic tap train |
| Structure-borne coupling hook | `acoustics/structure.py` | Interface only — no full FEA |

### Spherion API sketch

```python
world.set_reflection_tap(entity_id, gain, delay_s, lpf_hz)  # 0 gain = off
```

Implementation: small ring buffer per object (reuse fractional delay infra), mixed parallel to dry path before obstruction filter.

---

## Industry patterns

| System | Pattern | NULLSIGHT analogue |
|--------|---------|-------------------|
| **Geometric acoustics** | Image sources, ray clusters, 20–60 Hz update | 20 Hz trace, 1–2 bounces |
| **Meta XR Audio** | Simplified portals + diffraction | Doorway portal graph |
| **Hybrid IR + ray** | Early rays → tap delays; late → convolution | Phase 3 tap + Phase 4 bus IR |

---

## Test plan

### Automated (`verify_acoustic_reflection.py` — new)

- Image-source delay matches path length excess ±1 ms
- Diffraction adds gain when LOS blocked but portal open
- Portal graph finds shortest acoustic path across rooms
- Flutter detection emits periodic tap train on parallel-wall rooms

### Manual listen

- Face wall: hear delayed first reflection (not just metadata)
- Stand around corner: hear source via diffraction path
- Open doorway between rooms: sound bleeds through portal graph
- Long narrow room: flutter echo train audible

---

## Acceptance (Phase 3 complete)

- Early reflections are **audible** with correct delay and directional pan
- Corner diffraction provides “around the corner” bleed when LOS blocked
- Portal graph routes sound through doorways/windows with correct gain
- Flutter echoes detected in parallel-wall geometry
- Structure-borne hook API documented and callable (no full simulation required)
