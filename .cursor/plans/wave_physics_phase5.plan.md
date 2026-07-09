# Wave Physics — Phase 5: Scene Polish

**Status:** ⏳ Planned  
**Parent:** [wave_physics_master.plan.md](wave_physics_master.plan.md)  
**Previous:** [wave_physics_phase4.plan.md](wave_physics_phase4.plan.md)

---

## Goal

Production-ready acoustic content: full material library, ODEON-style transition order tuning, and a manual QA checklist integrated into project docs.

---

## Scope

| Task | File(s) | Notes |
|------|---------|-------|
| Full material library | `acoustics/materials.py`, level data | Wood, glass, carpet, metal, brick, etc. with α, s, mass-law |
| Material tags in all level geometry | `room.py`, `level_builder.py` | No `guess_material()` heuristics remaining |
| ODEON-style transition order tuning | `acoustics/reflection.py`, `acoustics/radiosity.py` | ISM order vs ray-radiosity crossover by room size |
| Per-room acoustic presets | `acoustics/reverb_zones.py` | Designer-friendly named presets |
| Debug HUD: acoustic paths overlay | `main.py` | path_mode, tap gains, RT60 per zone |
| Manual QA checklist | `guide.md` | Listen tests for every feature in matrix |
| Performance profiling doc | `guide.md` or `.cursor/plans/` | 20 Hz trace ms budget, Spherion block cost |

---

## Transition order (ODEON-style)

ODEON uses a **transition order (TO)** where image sources dominate below TO and ray-radiosity takes over above. For NULLSIGHT:

| Room size | Suggested TO | Early model | Late model |
|-----------|-------------|-------------|------------|
| Small (< 50 m³) | 3–4 | ISM order 3 | Schroeder + modal |
| Medium (50–200 m³) | 2–3 | ISM order 2 + scatter | Schroeder + optional IR |
| Large / outdoor | 1–2 | ISM order 1 or direct only | Air absorption only |

Tune TO by ear against reference clips; document final values in `guide.md`.

---

## Manual QA checklist (draft)

### Tier A — Propagation

- [ ] Propagation delay audible at 50 m+
- [ ] Doppler sweep when passing moving source
- [ ] Outdoor HF dulling at range (ISO 9613)

### Tier B — Early field

- [ ] Directional taps pan with reflection arrival
- [ ] Source directivity (radio cardioid)
- [ ] Diffuse scatter vs specular ping
- [ ] Per-band reflection timbre (brick vs glass)

### Tier C — Paths & graph

- [ ] Graded obstruction through thin walls
- [ ] Portal bleed through cracked/open doors
- [ ] Corner diffraction around obstacles
- [ ] Flutter echo in narrow corridor

### Tier D — Late field

- [ ] RT60 changes on door open/close
- [ ] Reverb predelay tracks first reflection
- [ ] Zone IR tail in enclosed rooms
- [ ] No false reverb outdoors

### Tier E — Polish

- [ ] All occluders have explicit material tags
- [ ] Material variety audible (metal vs carpet vs concrete)
- [ ] RAY AUDIO toggle resets immediately
- [ ] No audio pops on geometry revision (door swing)

---

## Acceptance (Phase 5 complete)

- Full material library with documented α, s, and transmission presets
- All level geometry tagged — no material guessing
- Transition order tuned and documented per room category
- `guide.md` contains complete manual QA checklist
- Debug HUD available for acoustic path visualization
- Performance within budget at 20 Hz trace with 4+ sources
