# Wave Physics — Phase 4: Heavy Hybrid

**Status:** ⏳ Planned  
**Parent:** [wave_physics_master.plan.md](wave_physics_master.plan.md)  
**Previous:** [wave_physics_phase3.plan.md](wave_physics_phase3.plan.md)  
**Next:** [wave_physics_phase5.plan.md](wave_physics_phase5.plan.md)

---

## Goal

Indoor spaces stop sounding anechoic without full wave simulation. Add zone IR convolution, modal correction below Schroeder frequency, BTM edge diffraction upgrade, and stochastic late-ray energy feeding the reverb bus.

---

## Features (from master matrix)

| # | Feature | Tier | Physics |
|---|---------|------|---------|
| 15 | Convolution IR / zone | D | Hybrid early+IR late |
| 16 | Room modes LF | D | Eigenmode bank |
| 17 | Full BTM diffraction | D | Edge integral |
| 18 | Stochastic late rays | D | Path tracing energy |

Plus late reverb hybrid (statistical tail + optional IR) from the original acoustic roadmap.

---

## Implementation

| Task | File(s) | Notes |
|------|---------|-------|
| Zone RT60 / preset table | `acoustics/reverb_zones.py` | Per `RoomData.name` |
| Send bus + Schroeder or IR | Spherion engine | Small stereo convolver on wet send |
| Energy coupling from ray hits | `acoustics/tracer.py` | Reflection energy → reverb send level |
| Outdoor vs indoor auto | `nullsight_audio.py` | Disable reverb on `large` outdoor |
| Zone IR convolution | `acoustics/early_ir.py`, Spherion | Pre-baked or procedural per zone |
| Modal bank below f_schroeder | `acoustics/modes.py` | Rectangular room eigenmodes |
| BTM edge diffraction upgrade | `acoustics/diffraction.py` | Replace UTD-lite with edge integral (Svensson et al. 1999) |
| Stochastic late-ray energy | `acoustics/radiosity.py` | Path tracing → `reverb_send` level |

### Spherion API

```python
world.set_zone_ir(zone_id, ir_path)  # per room zone
world.set_late_reverb_send(entity_id, send_level)
```

After native changes: `pip install -e C:\Robotics\newer-non-robotics\spherion`

---

## Hybrid pipeline (Phase 4 target)

Real-time wireframe game cannot run FDTD/BEM. Target stack:

1. **Geometric early** (ISM + scatter + UTD/BTM) → directional delay taps (Phase 3)
2. **Statistical late** (Sabine/Eyring RT60) → Schroeder bus (Phase 2)
3. **Optional IR** per zone for late tail (this phase)
4. **Modal correction** below f_schroeder only (this phase)

### Industry reference

| System | Pattern |
|--------|---------|
| **Steam Audio** | Hybrid: geometric paths + baked/probe IR (`HybridReverbEstimator`) |
| **ODEON** | ISM below transition order; ray-radiosity above |
| **Hybrid IR + ray** | Early rays → tap delays; late → convolution |

---

## CPU budget notes

- Zone IR: small stereo convolver (256–512 tap) per active zone, not per source
- Modal bank: only below f_s ≈ 2000·√(T60/V) Hz; typically <10 modes for small rooms
- BTM: limit to 16–32 visible edges per source at 20 Hz
- Late rays: stochastic cluster, 8–16 rays per source, energy domain

---

## Test plan

### Automated

- RT60 from Sabine/Eyring matches zone preset ±10%
- Modal frequencies match analytical rectangular modes
- BTM diffraction coefficient > UTD-lite in shadow zone test case
- Late-ray energy integrates to expected reverb send level

### Manual listen

- Small room: believable late tail (not anechoic)
- Large outdoor: no false indoor reverb
- Low frequencies: modal coloration below Schroeder (subtle)
- Edge diffraction: smoother shadow-zone transition vs UTD-lite

---

## Acceptance (Phase 4 complete)

- Wet reverb send bus audible in enclosed zones
- Optional per-zone IR tail blends with Schroeder late bus
- Modal correction affects LF coloration in small rooms
- BTM upgrade improves edge diffraction vs UTD-lite
- Stochastic late rays drive reverb send without per-source convolution cost
