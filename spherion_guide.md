# Spherion Guide

A comprehensive reference for installing, using, integrating, tuning, and extending **Spherion** — a CPU-only Higher Order Ambisonics (HOA) audio engine for games with a Python-first API, real-time device output, stereo spatial objects, listener pose sync, hot-swappable HRTF profiles, and optimized binaural convolution.

**Version covered:** `0.2.0`

---

## Table of contents

1. [What Spherion does](#what-spherion-does)
2. [Installation](#installation)
3. [Architecture overview](#architecture-overview)
4. [Coordinate system and units](#coordinate-system-and-units)
5. [Core concepts](#core-concepts)
6. [Acoustic physics reference](#acoustic-physics-reference)
7. [Atmospheric conditions and weather](#atmospheric-conditions-and-weather)
8. [API reference](#api-reference)
9. [`AudioWorld` wrapper](#audioworld-wrapper)
10. [Quick start](#quick-start)
11. [Tutorials](#tutorials)
12. [Game integration patterns](#game-integration-patterns)
13. [Performance tuning](#performance-tuning)
14. [IR convolution modes](#ir-convolution-modes)
15. [HRTF workflow and SOFA files](#hrtf-workflow-and-sofa-files)
16. [Native implementation notes](#native-implementation-notes)
17. [Build and CMake reference](#build-and-cmake-reference)
18. [Testing](#testing)
19. [Examples in this repo](#examples-in-this-repo)
20. [Troubleshooting](#troubleshooting)
21. [Best practices](#best-practices)
22. [Roadmap and current limitations](#roadmap-and-current-limitations)
23. [Quick reference card](#quick-reference-card)

---

## What Spherion does

Spherion is designed for **interactive 3D audio in games** where you want:

- **Stereo sources as spatial objects** placed in world space (e.g. a radio 10 m ahead and 5 m up).
- **Listener sync** so camera/head rotation (yaw, pitch, roll) and player absolute position move the perceived sound field.
- **An internal HOA bus** up to **10th order** (121 channels at max order).
- **Binaural headphone output** with **runtime HRTF profile swapping** and crossfade.
- **Two convolution strategies:** a fast global HOA-bus convolve (default) or per-object IR convolution for custom HRIRs.
- **Low-latency CPU rendering** with real-time audio device I/O via miniaudio.
- **Sample rates from 48 kHz to 192 kHz**.
- **Offline rendering** via `process_block()` for CI, tooling, and headless tests.
- **Acoustic physics** — inverse-distance spreading, ISO 9613-1 air absorption, optional propagation delay, Doppler, and weather-driven atmosphere (change-notify, not per-frame).

Python controls placement, pose, and HRTF selection. Weather/climate uses **notify-on-change** or a **provider** — do not push temperature/humidity every frame. The real-time audio callback runs entirely in native C++ and does not invoke the Python interpreter except when polling an optional atmospheric provider.

### v0.2 highlights

| Area | What changed |
|------|----------------|
| Encoding | SN3D spherical harmonics; **block-rate encoding** (SH/trig once per object per block) |
| Convolution | Time-domain FIR with AVX2 dot products; block-batched convolve; global vs hybrid per-object modes |
| RT path | Lock-free object snapshots; reusable render buffers; bulk sample playback |
| HRTF | Single-parse `load_hrtf_bundle`; async worker joins cleanly; auto-commit in render loop |
| Python | `set_listener_pose()`; GIL released on hot setters; `process_block()` renders direct-to-NumPy |
| Build | LTO enabled in pip builds; AVX2 on Release x86; optional C++ unit tests |
| Tests | **92 pytest tests**; online MIT KEMAR convolution tests; native C++ tests |
| Physics | ISO 9613-1 air absorption; optional propagation delay, Doppler, directional SOFA HRTF |
| Atmosphere | Change-notify / provider listener — **do not push T/RH every frame**; toggle off = 20 °C / 50 % RH |

---

## Installation

### Requirements

| Requirement | Notes |
|-------------|-------|
| Python | 3.10+ |
| CMake | 3.24+ |
| C++ compiler | MSVC 2022 (Windows), or Clang/GCC on other platforms |
| Git | Used by CMake `FetchContent` for miniaudio |
| Network | First build downloads miniaudio; optional SOFA fetch for tests |

### Install into your default Python environment

From the project root:

```powershell
cd c:\Robotics\newer-non-robotics\spherion
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

The `[dev]` extra installs **pytest** and **h5py** (needed for `scripts/fetch_kemar_hrir.py` and `@pytest.mark.online` tests).

Editable install (`-e`) is recommended during development. After **any C++ change** in `src/` or `include/`, reinstall:

```powershell
python -m pip install -e ".[dev]"
```

Or use the fast rebuild script:

```powershell
.\scripts\rebuild_native.ps1
```

### Verify installation

```powershell
python -c "import spherion; from spherion._native import Engine; e=Engine(48000,1,64); print('OK')"
python -m pip show spherion
python -m pytest -q
```

Expected `pip show` output includes:

```
Editable project location: C:\Robotics\newer-non-robotics\spherion
Version: 0.2.0
```

### One-shot build script (Windows)

```powershell
.\build.ps1
```

Installs dependencies, builds the native `_native` extension, and runs tests.

### Dependencies fetched at build time

CMake automatically fetches:

- **pybind11** — Python bindings (`find_package`, not duplicated FetchContent)
- **miniaudio** — audio file decode and device I/O (v0.11.22)

Optional at configure time:

- **Spatial Audio Framework (SAF)** — `SPHERION_ENABLE_SAF=OFF` by default; not used in the render path today

---

## Architecture overview

### End-to-end pipeline (per audio block)

```
┌─────────────────────────────────────────────────────────────────┐
│  CONTROL THREAD (Python / game loop)                            │
│  - set_listener_pose, set_position, set_gain, load_hrtf_async   │
│  - add/remove objects (mutex on object list)                    │
└────────────────────────────┬────────────────────────────────────┘
                             │ atomics + shared_ptr snapshot
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  AUDIO THREAD (miniaudio callback → Engine::render)             │
│                                                                 │
│  1. Snapshot listener pose (generation-tagged)                  │
│  2. refresh_atmospheric_physics() if dirty / provider changed   │
│  3. For each active object (distance-culled):                   │
│       - Bulk-render stereo samples (render_block)               │
│       - Compute SH weights ONCE per block (block-rate encode)   │
│  3. Per frame: accumulate HOA from precomputed weights          │
│  4. Decode HOA → stereo ear feeds                             │
│  5. HRIR convolution (global or per-object / hybrid)            │
│  6. Write interleaved stereo output                           │
│  7. Advance HRTF crossfade; try_commit_pending HRTF             │
└─────────────────────────────────────────────────────────────────┘
```

### Global convolution mode (default)

```
Stereo objects (world positions, bulk sample blocks)
        ↓
Block-rate object encoder (NULLSIGHT listener-space SH weights)
        ↓
Per-frame HOA accumulation (all objects → one bus)
        ↓
HOA → ear feeds (lightweight decode)
        ↓
Single shared HRIR convolution (IrConvolution, block-batched)
        ↓
miniaudio device output (stereo interleaved)
```

### Per-object convolution mode (hybrid)

Objects **with** `set_ir_path()` / `set_ir_arrays()`:

```
encode → decode → per-object IrConvolution → sum into output
```

Objects **without** custom IR:

```
encode → HOA bus (same as global) → one shared global convolve → sum into output
```

This hybrid avoids paying **N full convolutions** when only a few sources need custom HRIRs.

### Threading model

| Thread | Responsibilities | Must not |
|--------|------------------|----------|
| **Python / game** | Pose, spawn, HRTF load requests, gain | Block on audio; heavy sync file I/O in hot loop |
| **HRTF worker** | Parse SOFA / load HRIR bundle | Touch audio callback directly |
| **Audio callback** | Full render path | Call Python; allocate per block (design goal) |

**Lock-free patterns in the audio path:**

- `objects_active_` — `shared_ptr` atomic swap; mutex only on add/remove
- Listener pose — atomic fields + generation counter (`Listener::snapshot`)
- Object transforms — atomics snapshotted once per block
- Master gain, max distance, convolution mode — relaxed atomics

### Block-rate HOA encoding

Older per-sample encoding recomputed `atan2`, distance attenuation, and full SN3D spherical harmonics **for every frame × every object**.

v0.2 computes **one `ObjectEncodingWeights` struct per object per block**:

1. Transform world-relative offset to listener space.
2. Evaluate SN3D SH coefficients and distance gain **once**.
3. Inner loop per frame: `hoa[i] += sample * sh[i] * gain`.

Object/listener pose is snapshotted at block start. Sub-block pose changes are quantized to block boundaries (~5 ms @ 48 kHz / 256 frames). This is standard for block-based engines.

---

## Coordinate system and units

Spherion uses the **NULLSIGHT coordinate convention** — the same space as the NULLSIGHT game engine.

### Handedness and axes

**Right-handed, Y-up.**

| Axis | World direction |
|------|-----------------|
| **+X** | Right |
| **+Y** | Up |
| **+Z** | Forward (into the level) |

Forward is **+Z**, not Unity's "forward = −Z".

### Units

- **Positions:** meters (`m`), implicit in all APIs
- **Angles:** **radians** for yaw, pitch, and roll — not degrees

Examples: `eye_height = 1.6`, rooms are `12×12 m`, move speed `3.0 m/s`.

### Floor / gameplay plane

Movement and room layout live on the **XZ plane**:

- Camera feet on floor: `(x, 0, z)`
- Room bounds: `(min_x, max_x, min_z, max_z)`
- `spawn_at(x, z)` → world position `(x, y, z)` with default `y = 0`

### Listener (camera)

**Position — use the eye, not the feet**

```python
eye = camera.eye_position()  # (x, eye_height, z) — default eye_height = 1.6
engine.set_listener_pose(eye[0], eye[1], eye[2], camera.yaw, camera.pitch, 0.0)
```

Do **not** use `camera.position` for audio; its Y is always 0 (feet on floor).

**Orientation — radians**

| Field | Unit | Notes |
|-------|------|-------|
| `yaw` | radians | heading rotation |
| `pitch` | radians | look up/down; NULLSIGHT clamps to ±~1.45 rad |
| `roll` | radians | supported by Spherion; NULLSIGHT uses 0 |

`set_listener_pose()` updates position and orientation in **one native call** (preferred over separate position + orientation calls).

No `math.degrees`, no axis flips, no Z negation.

### Yaw = 0 heading

At `yaw = 0`, the player looks down **+Z**:

```
forward_x = sin(yaw)
forward_z = cos(yaw)
```

Mouse right (`look_dx > 0`) → yaw increases → turn right. Viewed from above (+Y), heading rotates from +Z toward +X (clockwise on a top-down map).

### Pitch sign

- Mouse up (`look_dy < 0`) → pitch increases → look up
- Mouse down → pitch decreases → look down

### World → listener transform

Spherion applies the same `world_to_camera` math as NULLSIGHT before encoding:

```
rx = x * cos_yaw - z * sin_yaw
rz = x * sin_yaw + z * cos_yaw
cy = y * cos_pitch - rz * sin_pitch
fz = y * sin_pitch + rz * cos_pitch
```

Listener-local axes:

| Axis | Meaning |
|------|---------|
| **+X** | Right |
| **+Y** | Up |
| **+Z** | Forward (view direction at pitch=0) |

### Relative direction from listener eye to source

```
rel = (source_x - eye_x, source_y - eye_y, source_z - eye_z)
```

After listener-space transform:

```
azimuth   = atan2(local_x, local_z)
elevation = atan2(local_y, sqrt(local_x² + local_z²))
```

### NULLSIGHT → Spherion direct mapping

```python
eye = camera.eye_position()
engine.set_listener_pose(eye[0], eye[1], eye[2], camera.yaw, camera.pitch, 0.0)
```

Place sounds in the same world space as NULLSIGHT wireframe vertices:

```python
obj.set_position(x, y, z)  # world meters, same as game objects
```

---

## Core concepts

### Engine

The `Engine` owns:

- Sample rate, ambisonic order, encode order cap, and block size
- The listener state (orientation + position)
- All stereo objects
- The binaural decoder and HRTF manager
- The miniaudio output device
- Reusable render buffers (`feed_*`, `conv_*`, `hoa_frame_`, per-object block buffers)

Create one engine per audio output context (typically one per game session).

### StereoObject

A `StereoObject` is a **decoded stereo audio file** that plays back in 3D space. You:

1. Add it with `add_stereo_object(path, loop=True)` (GIL released during decode).
2. Place it with `set_position(x, y, z)` in **world coordinates**.
3. Optionally pause with `set_active(False)` or restart with `reset_playhead()`.
4. Optionally assign a per-object HRIR with `set_ir_path()` or `set_ir_arrays()`.

Supported file formats depend on miniaudio (WAV, MP3, FLAC, OGG, and others).

Sample playback uses a **bulk `render_block` path**: atomics are read once per block, contiguous sample regions are copied efficiently, and the playhead is stored once at block end.

### HOA order

Ambisonic **order** controls spatial resolution. Channel count is:

```
channels = (order + 1)²
```

| Order | Channels | Typical use |
|-------|----------|-------------|
| 1 | 4 | Fastest, coarse |
| 3 | 16 | Good default for games |
| 5 | 36 | High quality desktop |
| 7 | 64 | Demanding |
| 10 | 121 | Maximum resolution, very CPU-heavy |

**`encode_order`** (optional) caps encoding CPU separately from bus order:

```python
config = spherion.EngineConfig()
config.order = 5           # full HOA bus resolution
config.encode_order = 3    # encode only up to 3rd-order SH (cheaper)
engine = spherion.Engine(config)
```

Higher order improves directional accuracy, especially at higher frequencies, at the cost of CPU.

### Block size and latency

`block_size` is the number of frames processed per callback period. Approximate latency:

```
latency_ms ≈ (block_size / sample_rate_hz) × 1000
```

| block_size | @ 48 kHz | @ 96 kHz | @ 192 kHz |
|------------|----------|----------|-----------|
| 128 | ~2.7 ms | ~1.3 ms | ~0.7 ms |
| 256 | ~5.3 ms | ~2.7 ms | ~1.3 ms |
| 512 | ~10.7 ms | ~5.3 ms | ~2.7 ms |
| 1024 | ~21.3 ms | ~10.7 ms | ~5.3 ms |

Lower block sizes reduce latency but increase per-callback overhead. Buffers are pre-sized to `block_size` at engine construction; growing beyond configured size triggers resize (rare if `block_size` matches device period).

### HRTF profiles

An HRTF shapes how HOA is decoded into left/right ear signals for headphone playback. Spherion supports:

- Loading a profile at engine init via `hrtf_path`
- **Async hot-swap** via `load_hrtf_async()` + `commit_hrtf()`
- **Auto-commit** on each audio block via `try_commit_pending()` inside `render_internal`

Profiles are loaded on a background thread (single parse via `load_hrtf_bundle`) and swapped with a 256-sample gain crossfade.

### Distance model

Configured via `EngineConfig`. Attenuation follows **free-field physics** on linear gain:

- **Sound pressure (amplitude)** decays as **1/r** (inverse distance law).
- **Sound intensity (energy)** decays as **1/r²** — that is already the square of pressure, so do **not** apply inverse-square to amplitude (it makes sources fall off roughly one power of *r* too fast).

Optional `air_absorption_db_per_m` adds mild broadband pressure loss with distance (ISO 9613-style; default ~0.012 dB/m).

| Field | Default | Meaning |
|-------|---------|---------|
| `reference_distance_m` | 1.0 | Distance at which gain is nominal |
| `min_distance_m` | 0.5 | Clamps near-field distance used in rolloff |
| `distance_rolloff` | 1 | 0=none, **1=inverse (1/r, physical)**, 2=inverse-square on amplitude (legacy), 3=inverse clamped at ref |
| `air_absorption_db_per_m` | 0.012 | Extra dB loss per metre (0=off) |
| `max_audible_distance_m` | 0 | Engine-side cull + smooth Hann fade (0=off) |

Fade width near max distance is `max(4 m, 12% of max_audible_distance_m)` so large worlds do not cliff-cut in the last 2 m.

The engine culls objects beyond `max_audible_distance_m` in the render path. `AudioWorld.cull_by_distance()` is optional; the engine already culls when max distance is set.

See [Acoustic physics reference](#acoustic-physics-reference) and [Atmospheric conditions and weather](#atmospheric-conditions-and-weather) for the full physics model, toggles, and integration patterns.

---

## Acoustic physics reference

This section documents **every acoustic mechanic Spherion applies today**, how it is configured, where it runs in the pipeline, and what is *not* modeled. Use it when tuning NULLSIGHT integration, debugging “sources fall off too fast,” or deciding which optional physics toggles to enable.

### Design philosophy

Spherion targets **interactive free-field 3D audio on headphones**:

- **Physically motivated** where it improves believability (inverse-distance spreading, ISO air absorption, Woodworth ITD, Doppler).
- **Block-rate** where possible — distance, SH weights, atmospheric coefficients, and Doppler rates are computed **once per object per audio block**, not per sample.
- **Optional extras** (propagation delay, Doppler, live weather) default **off** so baseline CPU stays predictable.
- **No room acoustics** — no early reflections, late reverb, occlusion portals, or diffraction. Indoor scenes will sound drier than reality unless you add reverb elsewhere.

### Pipeline placement

```
Per block (audio thread):
  refresh_atmospheric_physics()     ← only when dirty / toggle changed / provider value changed
  For each active object:
    snapshot pose + velocity
    distance, listener-relative angles
    Doppler rate (optional)
    propagation delay in samples (optional)
    render_block(samples, doppler_rate)
    HF air-absorption one-pole filter (optional, uses effective atmosphere)
    fractional delay line (optional)
    block-rate SH encode + distance gain (includes broadband air loss)
  HOA decode → HRTF convolution → output
```

Control-thread APIs (`set_position`, `notify_atmospheric_changed`, etc.) update atomics or caches. The audio thread reads snapshots; it never calls Python.

---

### Spherical spreading (distance rolloff)

Sound pressure in a free field decays with distance. Spherion applies rolloff on **linear pressure gain**, not on intensity.

| `distance_rolloff` | Name | Pressure gain (beyond `min_distance_m`) | When to use |
|--------------------|------|----------------------------------------|-------------|
| **0** | None | 1.0 | Debug, UI beds, unrealistic “infinite reach” |
| **1** | Inverse (default) | `reference_distance_m / r` | **Physical free field** — pressure ∝ 1/r |
| **2** | Inverse-square on amplitude | `(reference_distance_m / r)²` | Legacy; **too steep** for amplitude (intensity would be 1/r⁴) |
| **3** | Inverse clamped | Unity inside `reference_distance_m`, then 1/r | Prevent near-field boost when ref > min |

**Important:** In acoustics, **intensity** falls as 1/r². That is already the square of pressure when pressure ∝ 1/r. Mode 2 applies 1/r² to amplitude, which makes sources inaudible much faster than real outdoor propagation.

Near-field clamping uses `min_distance_m` (default 0.5 m) so objects on top of the listener do not divide by zero.

**Reference distance:** At `r = reference_distance_m`, nominal gain is 1.0 (before object gain and master gain).

---

### Air absorption

Two complementary mechanisms use **ISO 9613-1:1993** coefficients derived from temperature, relative humidity, and pressure:

| Mechanism | Frequency anchor | Applied as | Toggle |
|-----------|------------------|------------|--------|
| **Broadband** | 1 kHz | `dB/m × distance` on pressure in distance model | Always (via `effective_atmosphere_`) |
| **HF one-pole** | 4 kHz | Per-sample low-pass vs distance on object buffers | `enable_hf_air_absorption` (default **on**) |

When atmospheric override is **disabled**, effective conditions are fixed at **20 °C, 50 % RH, 101.325 kPa** — typical comfortable indoor air. When **enabled**, live temperature and humidity from your game drive both broadband and HF loss, and speed of sound.

Legacy scalar `EngineConfig.air_absorption_db_per_m` is overwritten at runtime when atmospheric physics refresh runs. Prefer the atmospheric API for games with weather.

**Audible effect:** Hot, dry air absorbs high frequencies more aggressively over distance; humid cool air is slightly “brighter” at range. The effect is subtle at gameplay distances (< 50 m) but accumulates for snipers, aircraft, and large open worlds.

---

### Speed of sound

Propagation delay and Doppler use:

```
c ≈ 331.3 + 0.606 × T_c   (m/s, ideal gas approximation)
```

At 20 °C, c ≈ **343 m/s** (`kSpeedOfSound20C`). At 35 °C, c ≈ **352 m/s**.

When `use_atmospheric_conditions` is on, **T from effective atmosphere** updates `effective_speed_of_sound_mps_` on the audio thread. This affects:

- Propagation delay (`τ = r / c`)
- Doppler pitch ratio denominators

---

### Propagation delay (optional)

| Field | Default | Meaning |
|-------|---------|---------|
| `enable_propagation_delay` | `False` | Per-object fractional delay line |
| `max_propagation_delay_s` | `0.05` | Cap delay at 50 ms (~17 m @ 343 m/s) |

When enabled, each spatial object is delayed by `min(r/c, max_propagation_delay_s)` using a ring-buffer **fractional delay line** with linear interpolation. Delay lines are sized for the configured max distance cap.

**Use when:** Large outdoor maps where “sound arrives late” matters (artillery, thunder, distant explosions). **Skip when:** Fast-paced indoor combat — the 50 ms cap and block-rate pose snap are noticeable for close sources.

Propagation delay and Doppler both respect `spatial_bypass` objects (direct stereo mix, no 3D path).

---

### Doppler (optional)

| Field | Default | Meaning |
|-------|---------|---------|
| `enable_doppler` | `False` | Resample object playback rate per block |

Formula (radial velocities along source→listener axis, + = approaching):

```
f_received / f_emitted = (c + v_listener_along) / (c - v_source_along)
```

Set velocities in **world m/s**:

```python
obj.set_velocity(vx, vy, vz)           # source
engine.set_listener_velocity(vx, vy, vz)  # listener (camera rig, vehicle)
```

Implemented via fractional playhead advance in `StereoObject::render_block`. Extreme closing speeds can pitch-shift heavily; keep gameplay speeds plausible.

**Use when:** Vehicles, passing trains, swooping projectiles. **Skip when:** Walking simulators — foot-speed Doppler is below perceptual threshold.

---

### Direction and HOA encoding

- **SN3D real spherical harmonics**, orders 1–10 (`channels = (order+1)²`).
- Direction computed in **listener-local axes** after NULLSIGHT `world_to_camera` rotation (+X right, +Y up, +Z forward).
- **`encode_order`** caps SH evaluation CPU separately from bus order.
- **`preserve_stereo_width`** (default `True`): injects L−R side energy into ACN1 for stereo assets (radios, music). Not a physical wave property — disable for point-like SFX.

---

### Binaural decode and HRTF

**Global path (default):**

1. First-order SN3D decode: `L = W − √2·X`, `R = W + √2·X` (in-phase lateral).
2. Higher orders add diffuse ambience to the bus.
3. Single shared HRIR convolution (`IrConvolution`, AVX2 on x86 Release).

**SOFA measured HRTF:** libmysofa loads `Data.IR`. Optional **`directional_hrtf=True`** enables per-source direction lookup via `mysofa_getfilter_float` and **automatically enables per-object convolution** for those sources.

**Synthesized fallback** (empty/invalid path): Woodworth **ITD** + frequency-weighted **head-shadow ILD** on a rigid sphere (a = 8.75 cm). Used when SOFA parse fails.

| Mode | Config | Behavior |
|------|--------|----------|
| Global SOFA | `hrtf_path="profile.sofa"`, `directional_hrtf=False` | One HRIR set convolved on the HOA bus |
| Directional SOFA | `directional_hrtf=True` + valid SOFA grid | Per-object IR selected by azimuth/elevation |
| Synthesized | `hrtf_path=""` | Analytic head model |

---

### Max audible distance (game boundary)

Not a physical limit — a **mixing and CPU boundary**:

- Objects beyond `max_audible_distance_m` are skipped in encoding.
- Smooth **Hann fade** over `max(4 m, 12% of max)` prevents cliff cuts.

Set on engine init or via `set_max_audible_distance()`. Independent of rolloff physics — you can have 1/r law to 80 m then hard cull at 100 m with fade.

---

### Physics toggles quick reference

| Toggle / field | Default | CPU impact | Audible effect |
|----------------|---------|------------|----------------|
| `distance_rolloff = 1` | on (mode 1) | Low | Realistic level vs distance |
| `enable_hf_air_absorption` | **True** | Low–medium | Distant sources duller |
| `use_atmospheric_conditions` | False | Negligible when off | Weather-driven absorption + c |
| `enable_propagation_delay` | False | Medium | Late arrival of distant events |
| `enable_doppler` | False | Low | Pitch shift from motion |
| `directional_hrtf` | False | **High** | Head-related filtering follows source direction |
| `per_object_convolution` | False | High if many custom IRs | Per-source HRIR paths |

---

### What is NOT modeled

| Missing effect | Workaround |
|----------------|------------|
| Room reverb / early reflections | External reverb send; dry Spherion bus |
| Occlusion / obstruction | Game-side low-pass + gain ducking |
| Diffraction around corners | Not available |
| Ground reflection / atmospheric refraction | Not available |
| Wind noise | Not available |
| Nonlinear propagation (shock waves) | Not available |
| HOA rotation above 1st order | `scene_rotator` is 1st-order only; listener rotation handled in encoder |

Implementation files: `include/spherion/acoustics.hpp`, `include/spherion/atmospheric.hpp`, `include/spherion/distance_model.hpp`, `src/engine.cpp` (`gather_object_states`).

---

## Atmospheric conditions and weather

Spherion can drive **speed of sound** and **ISO 9613-1 air absorption** from live ambient **temperature (°C)** and **relative humidity (%)**. This section is the authoritative guide for integrating NULLSIGHT weather, biomes, or any dynamic climate system.

### Critical rule: do not push every frame

**Do not call atmospheric APIs every game frame with the current weather values.**

The engine maintains an internal **live cache** and a **dirty flag**. Physics recomputation (absorption coefficients, speed of sound) runs on the audio thread only when:

1. **`notify_atmospheric_changed(T, RH)`** receives values that differ from the cache (within ε = 0.05), or
2. A registered **`set_atmospheric_provider()`** callable returns new values on poll, or
3. **`set_use_atmospheric_conditions(enabled)`** changes the toggle.

Polling the provider happens once per audio block when the toggle is on, but **ISO math is skipped** unless values actually changed. Your game should either:

- **Push on change** — call `notify_atmospheric_changed` from your weather system when T or RH updates, or
- **Register a provider once** — let the engine read your weather object internally.

Both patterns avoid per-frame Python→native traffic for unchanged weather.

---

### Toggle: live vs average indoor air

| `use_atmospheric_conditions` | Effective T / RH | Speed of sound | Absorption |
|------------------------------|------------------|----------------|------------|
| **`False` (default)** | Fixed **20 °C, 50 % RH** | ~343 m/s | Average indoor ISO curve |
| **`True`** | Live cache (from notify or provider) | From live T | From live T + RH |

Live values stored in the cache **remain** when you disable the toggle — they are simply **ignored** for physics until you enable again. This lets you snap back to averages without re-sending weather.

```python
engine.set_use_atmospheric_conditions(False)  # immediate return to 20/50 effective
engine.set_use_atmospheric_conditions(True)   # resume using cached live values
```

---

### API reference (atmospheric)

#### `spherion.AtmosphericConditions`

```python
conditions = spherion.AtmosphericConditions()
conditions.temperature_c = 28.0
conditions.relative_humidity_percent = 65.0
conditions.pressure_kpa = 101.325  # optional; sea-level default
```

| Field | Range (clamped) | Default |
|-------|-----------------|---------|
| `temperature_c` | −20 … 50 °C | 20.0 |
| `relative_humidity_percent` | 10 … 100 % | 50.0 |
| `pressure_kpa` | preserved if set | 101.325 |

#### `Engine.notify_atmospheric_changed(temperature_c, relative_humidity_percent) -> bool`

**Primary push API.** Call when your weather/climate system changes temperature or humidity.

- Clamps inputs to ISO practical ranges.
- Returns **`True`** if the live cache was updated.
- Returns **`False`** if values are equal to the cached state (deduplicated — no physics work scheduled).
- **GIL released** — safe from game thread while audio runs.
- Does **not** require `process_block()` on the control thread; the next audio block picks up the dirty flag.

```python
if weather.temperature_c != last_t or weather.rh != last_rh:
    engine.notify_atmospheric_changed(weather.temperature_c, weather.rh)
    last_t, last_rh = weather.temperature_c, weather.rh
```

#### `Engine.set_atmospheric_provider(provider)`

Register a **callable** invoked on the audio thread when `use_atmospheric_conditions` is `True`.

- Pass **`None`** to clear (same as `clear_atmospheric_provider()`).
- Callable may return:
  - `spherion.AtmosphericConditions`, or
  - `(temperature_c, relative_humidity_percent)` tuple.
- Provider runs **with GIL acquired** (Python callable). Keep it **O(1)** — read two floats from your weather singleton, do not allocate or I/O.
- Exceptions are swallowed on the audio thread to protect playback.

```python
engine.set_atmospheric_provider(lambda: (world.weather.temp_c, world.weather.rh_pct))
engine.set_use_atmospheric_conditions(True)
engine.set_use_atmospheric_conditions(True)
```

Do **not** combine provider + notify every frame for the same values — pick one pattern.

#### `Engine.clear_atmospheric_provider()`

Removes the registered callable. Safe to call when no provider is set.

#### `Engine.set_use_atmospheric_conditions(enabled)`

Enable or disable live atmospheric physics. Toggling schedules a physics refresh on the next audio block even if T/RH unchanged.

#### `Engine.set_atmospheric_conditions(t, rh)` (deprecated)

Alias for `notify_atmospheric_changed`. Prefer the notify name in new code.

#### Read-only atmospheric state

| Method / property | Returns |
|-------------------|---------|
| `use_atmospheric_conditions` | Whether live cache drives physics |
| `atmospheric_conditions_live()` | Last stored live T/RH (clamped), regardless of toggle |
| `atmospheric_conditions_effective()` | T/RH actually applied on the audio thread (averages when toggle off) |

Inspect effective conditions after at least one `process_block()` or real-time block when debugging offline:

```python
engine.notify_atmospheric_changed(32.0, 70.0)
engine.set_use_atmospheric_conditions(True)
engine.process_block(engine.block_size)
print(engine.atmospheric_conditions_effective().temperature_c)
```

---

### Internal engine behavior (for integrators)

Understanding the native side helps debug “weather does nothing” reports.

```
Control thread                          Audio thread (each block)
─────────────────                       ───────────────────────────
notify_atmospheric_changed(T, RH)  →    refresh_atmospheric_physics()
  ├─ clamp T/RH                           ├─ if toggle on: poll provider
  ├─ compare to live_atmosphere_cache_    ├─ if !dirty: return early (fast path)
  ├─ if changed: set dirty flag           ├─ resolve effective T/RH
  └─ update atomics + config              ├─ speed_of_sound_mps(T)
                                          ├─ distance_model_.air_absorption_db_per_m
                                          └─ clear dirty flag
```

**Deduplication:** `atmospheric_conditions_equal()` uses ε = **0.05** for T (°C), RH (%), and pressure (kPa). Smaller weather tick changes below this threshold do not trigger recomputation.

**Provider mutex:** `set_atmospheric_provider` / `clear_atmospheric_provider` take a mutex; the audio thread copies the callable under lock then invokes it outside the lock.

**Init defaults:** Engine starts with `atmospheric_physics_dirty_ = true` so the first block always establishes effective atmosphere from config.

---

### ISO 9613-1 model (what the numbers mean)

Spherion implements **ISO 9613-1:1993** pure-tone atmospheric absorption:

- **`iso9613_absorption_db_per_m(f, conditions)`** — full coefficient at frequency `f`.
- **`atmospheric_broadband_absorption_db_per_m`** — evaluates at **1 kHz** for distance-model broadband loss.
- **`atmospheric_hf_absorption_db_per_m`** — evaluates at **4 kHz** for the per-sample HF low-pass.

Reference averages when toggle is off (`average_atmospheric_conditions()`):

| Quantity | Value |
|----------|-------|
| Temperature | 20 °C |
| Relative humidity | 50 % |
| Pressure | 101.325 kPa (sea level) |

**Example qualitative trends** (not exact dB — depends on distance):

| Air mass | Typical effect at 50 m |
|----------|------------------------|
| 20 °C, 50 % RH (default) | Baseline HF rolloff |
| 35 °C, 15 % RH (hot dry) | Stronger HF absorption |
| 5 °C, 90 % RH (cold humid) | Milder HF loss |

Speed of sound rises ~0.6 m/s per °C, affecting delay and Doppler when those features are enabled.

---

### Integration patterns

#### Pattern A: Push on change (recommended for event-driven weather)

Wire your weather system’s **change callback**, not the render loop:

```python
class WeatherSystem:
    def __init__(self, engine: spherion.Engine) -> None:
        self.engine = engine
        self.temperature_c = 20.0
        self.relative_humidity_percent = 50.0
        self.engine.set_use_atmospheric_conditions(True)

    def apply_snapshot(self, temperature_c: float, relative_humidity_percent: float) -> None:
        """Call when biome/weather/storm state changes."""
        self.temperature_c = temperature_c
        self.relative_humidity_percent = relative_humidity_percent
        self.engine.notify_atmospheric_changed(temperature_c, relative_humidity_percent)

    def on_storm_front_passed(self, new_t: float, new_rh: float) -> None:
        self.apply_snapshot(new_t, new_rh)
```

Game loop only syncs pose — **not** weather:

```python
def game_tick(engine, camera, weather):
    eye = camera.eye_position()
    engine.set_listener_pose(eye[0], eye[1], eye[2], camera.yaw, camera.pitch, 0.0)
    # weather is NOT updated here
```

#### Pattern B: Provider (recommended when weather lives in a singleton)

Register once at session start; engine polls internally:

```python
def setup_audio(engine, world):
    engine.set_atmospheric_provider(
        lambda: (world.climate.temperature_c, world.climate.relative_humidity_percent)
    )
    engine.set_use_atmospheric_conditions(True)
```

The provider is polled each audio block, but ISO recomputation runs **only when returned values differ** from the cache.

#### Pattern C: NULLSIGHT-style session config

Typical NULLSIGHT integration with outdoor audio:

```python
config = spherion.EngineConfig()
config.order = 3
config.block_size = 256
config.max_audible_distance_m = 80.0
config.distance_rolloff = 1
config.enable_hf_air_absorption = True
config.use_atmospheric_conditions = True
config.atmospheric_temperature_c = 20.0
config.atmospheric_relative_humidity_percent = 50.0
engine = spherion.Engine(config)

engine.set_atmospheric_provider(lambda: (
    nullsight.world.weather.temperature_c,
    nullsight.world.weather.relative_humidity_percent,
))
```

Or push from NULLSIGHT when the weather tick fires (e.g. every in-game minute, not every frame):

```python
def on_weather_tick(t_c: float, rh: float) -> None:
    audio_engine.notify_atmospheric_changed(t_c, rh)
```

#### Pattern D: Indoor levels (toggle off)

Interior scenes with no weather simulation — leave defaults:

```python
engine.set_use_atmospheric_conditions(False)
# Effective atmosphere locked to 20 °C / 50 % RH
```

You may still call `notify_atmospheric_changed` to cache outdoor values for when the player exits — they take effect when the toggle turns back on.

---

### `EngineConfig` atmospheric and physics fields

Set at construction; initial live cache seeded from atmospheric fields:

| Field | Default | Purpose |
|-------|---------|---------|
| `use_atmospheric_conditions` | `False` | Master toggle for live vs average air |
| `atmospheric_temperature_c` | `20.0` | Initial live cache temperature |
| `atmospheric_relative_humidity_percent` | `50.0` | Initial live cache humidity |
| `enable_propagation_delay` | `False` | Distance-based delay lines |
| `max_propagation_delay_s` | `0.05` | Cap propagation delay (seconds) |
| `enable_doppler` | `False` | Playback-rate Doppler |
| `enable_hf_air_absorption` | `True` | ISO 4 kHz one-pole vs distance |
| `directional_hrtf` | `False` | libmysofa per-direction IR lookup |
| `speed_of_sound_mps` | `343.0` | Legacy config field; runtime uses atmospheric T when refreshed |

---

### Velocity APIs (Doppler)

When `enable_doppler=True`:

```python
obj.set_velocity(vx_mps, vy_mps, vz_mps)
engine.set_listener_velocity(vx_mps, vy_mps, vz_mps)
```

Velocities are snapshotted per block with pose. Doppler uses the radial component along the source→listener line.

---

### Anti-patterns (atmospheric)

| Anti-pattern | Why it is wrong |
|--------------|-----------------|
| `notify_atmospheric_changed(T, RH)` every frame | Defeats deduplication; use provider or change events |
| Expecting effective T/RH before first audio block | Effective state updates in `refresh_atmospheric_physics()` |
| Heavy work inside atmospheric provider | Runs on audio thread with GIL; keep reads O(1) |
| Forgetting to enable toggle | Live cache ignored; always 20/50 effective |
| Mixing provider + per-frame notify | Redundant; pick one integration style |

---

### FAQ

**Q: Does disabling the toggle clear my live weather values?**  
A: No. `atmospheric_conditions_live()` still returns cached T/RH. Only effective conditions switch to 20/50.

**Q: How often does the provider run?**  
A: Once per audio block, but ISO math runs only on change.

**Q: Can I set pressure from altitude?**  
A: Return `AtmosphericConditions` with `pressure_kpa` from your barometric model.

**Q: Offline tests show stale effective conditions?**  
A: Call `engine.process_block(n)` after notify/toggle changes.

---

## API reference


### `spherion.Engine`

```python
engine = spherion.Engine(
    sample_rate=48000,   # Hz, clamped to 48000–192000
    order=3,             # HOA bus order, clamped to 1–10
    block_size=256,      # frames per callback, clamped to 64–2048
    hrtf_path="",        # optional initial HRTF/SOFA path
    max_audible_distance_m=0.0,  # 0 disables engine-side distance culling
)

# Or use EngineConfig for advanced options:
config = spherion.EngineConfig()
config.order = 5
config.encode_order = 2          # cap encode CPU separately from bus order
config.reference_distance_m = 1.0
config.min_distance_m = 0.5
config.distance_rolloff = 1      # 0=none, 1=inverse (1/r), 2=inverse-square on amplitude
config.air_absorption_db_per_m = 0.012   # legacy scalar; ISO T/RH drives runtime when refreshed
config.use_atmospheric_conditions = False  # True = use live values from notify_atmospheric_changed()
config.atmospheric_temperature_c = 20.0
config.atmospheric_relative_humidity_percent = 50.0
engine = spherion.Engine(config)

# When weather changes (not every frame):
engine.set_use_atmospheric_conditions(True)
engine.notify_atmospheric_changed(temperature_c=28.0, relative_humidity_percent=65.0)

# Or register a provider once; the engine polls it and updates only when values change:
engine.set_atmospheric_provider(lambda: (weather.temperature_c, weather.relative_humidity_percent))
```

#### Lifecycle

| Method | Description |
|--------|-------------|
| `start()` | Open and start the real-time output device |
| `stop()` | Stop and close the output device |

`stop()` is also called automatically when the engine is destroyed.

#### Read-only lifecycle

| Property | Description |
|----------|-------------|
| `is_running` | `True` when the output device is active |
| `object_count` | Number of registered stereo objects |

#### Objects

| Method | Description |
|--------|-------------|
| `add_stereo_object(path, loop=True)` | Decode file and register as spatial object. Returns `StereoObject`. GIL released during decode. |
| `remove_stereo_object(stereo_object)` | Remove object from the engine. Returns `True` if removed. |

#### Listener sync

| Method | Description |
|--------|-------------|
| `set_listener_pose(x, y, z, yaw, pitch=0, roll=0)` | **Preferred:** set eye position and orientation in one call (GIL released) |
| `set_listener_orientation(yaw, pitch=0, roll=0)` | Head/camera rotation in **radians** (GIL released) |
| `set_listener_position(x, y, z)` | Listener **eye** position in world meters (GIL released) |
| `set_listener_velocity(vx, vy, vz)` | Listener velocity in m/s for Doppler (GIL released) |
| `listener_state()` | Read back current listener pose, velocity, and generation tag |

Call **pose** updates every frame from your game loop. Call **weather/atmospheric** updates only on change — see [Atmospheric conditions and weather](#atmospheric-conditions-and-weather).

#### Gain and distance

| Method | Description |
|--------|-------------|
| `set_master_gain(gain)` | Master output gain (clamped to ≥ 0) |
| `set_max_audible_distance(distance_m)` | Skip encoding beyond this distance (0 = off); smooth fade near boundary |

| Property | Description |
|----------|-------------|
| `master_gain` | Current master gain |
| `max_audible_distance` | Current max audible distance in meters |

#### HRTF

| Method | Description |
|--------|-------------|
| `load_hrtf_async(sofa_path)` | Start background load of a new HRTF profile (single file parse) |
| `commit_hrtf()` | Apply loaded profile if ready. Returns `True` on success. Polls up to ~1 s. |
| `last_hrtf_error` | Last HRTF load error message (empty on success) |
| `is_hrtf_loading` | `True` while a background HRTF load is in progress |

The render loop also auto-commits pending HRTF profiles each block with crossfade.

#### Atmospheric conditions

See [Atmospheric conditions and weather](#atmospheric-conditions-and-weather) for full integration guide.

| Method / property | Description |
|-------------------|-------------|
| `notify_atmospheric_changed(temperature_c, relative_humidity_percent)` | **Primary:** notify on T/RH change; returns `True` if cache updated (GIL released) |
| `set_atmospheric_provider(callable \| None)` | Register weather reader polled by engine; physics updates only on change |
| `clear_atmospheric_provider()` | Remove registered provider |
| `set_use_atmospheric_conditions(enabled)` | Toggle live weather vs fixed 20 °C / 50 % RH (GIL released) |
| `set_atmospheric_conditions(t, rh)` | Deprecated alias for `notify_atmospheric_changed` |
| `use_atmospheric_conditions` | Read toggle state |
| `atmospheric_conditions_live()` | Last live T/RH cache (clamped) |
| `atmospheric_conditions_effective()` | T/RH applied on audio thread (averages when toggle off) |

#### Offline rendering

| Method | Description |
|--------|-------------|
| `process_block(frame_count)` | Render one block without device I/O; returns `(frames, 2)` float32 NumPy array. Renders direct into NumPy buffer (no extra copy). GIL released during native render. |

#### Convolution mode

| Method / property | Description |
|-------------------|-------------|
| `set_per_object_convolution(enabled)` | Toggle global vs hybrid per-object IR convolution |
| `per_object_convolution` | Current mode (`False` = global, default) |

#### Read-only properties

| Property | Description |
|----------|-------------|
| `sample_rate_hz` | Active sample rate |
| `order` | Active HOA bus order |
| `encode_order` | Active HOA encode order (may be lower than bus order) |
| `block_size` | Configured block size |

---

### `spherion.StereoObject`

Returned by `add_stereo_object`.

| Method | Description |
|--------|-------------|
| `set_position(x, y, z)` | World-space position in meters (GIL released) |
| `get_position()` | Returns `(x, y, z)` tuple |
| `set_velocity(vx, vy, vz)` | World velocity in m/s for Doppler when `enable_doppler=True` (GIL released) |
| `set_gain(gain)` | Per-object linear gain (GIL released) |
| `gain()` | Current per-object gain |
| `is_playing()` | `True` when active and playhead not exhausted |
| `set_loop(loop)` | Enable/disable looping (GIL released) |
| `set_active(active)` | Enable/disable playback (GIL released) |
| `reset_playhead()` | Restart from beginning |
| `set_ir_path(path, sample_rate_hz)` | Assign per-object HRIR from a SOFA file (GIL released) |
| `set_ir_arrays(left_ir, right_ir)` | Assign per-object HRIR from 1-D float32 NumPy arrays |
| `clear_ir()` | Remove custom IR; fall back to global bus in per-object mode |
| `has_custom_ir()` | `True` when a custom IR is loaded |

---

### `spherion.EngineConfig`

Low-level config struct. See [Acoustic physics reference](#acoustic-physics-reference) for physics field semantics.

```python
config = spherion.EngineConfig()
config.sample_rate_hz = 96000
config.order = 5
config.encode_order = 3       # optional CPU cap (0 = use full order)
config.block_size = 512
config.hrtf_path = "profiles/my_hrtf.sofa"
config.max_audible_distance_m = 50.0
config.reference_distance_m = 1.0
config.min_distance_m = 1.0
config.distance_rolloff = 1   # 0=none, 1=inverse 1/r, 2=legacy inverse-square, 3=clamped
config.air_absorption_db_per_m = 0.012  # legacy; ISO T/RH overwrites at refresh
config.preserve_stereo_width = True
config.per_object_convolution = False
config.enable_propagation_delay = False
config.max_propagation_delay_s = 0.05
config.enable_doppler = False
config.enable_hf_air_absorption = True
config.directional_hrtf = False
config.use_atmospheric_conditions = False
config.atmospheric_temperature_c = 20.0
config.atmospheric_relative_humidity_percent = 50.0
engine = spherion.Engine(config)
```

---

### `spherion.AtmosphericConditions`

Data class for atmospheric state (`temperature_c`, `relative_humidity_percent`, `pressure_kpa`). Used as provider return value or for inspection via `atmospheric_conditions_live()` / `atmospheric_conditions_effective()`.

---

### `spherion.compute_listener_relative_angles`

Debug and test helper exposing NULLSIGHT listener-space math:

```python
angles = spherion.compute_listener_relative_angles(
    rel_x_m, rel_y_m, rel_z_m,
    yaw_rad, pitch_rad=0.0, roll_rad=0.0,
)
# angles.azimuth_rad, angles.elevation_rad
# angles.local_x, angles.local_y, angles.local_z
```

Used by `tests/test_nullsight_coords.py` for coordinate sanity checks.

---

## AudioWorld wrapper

`spherion.AudioWorld` is a thin game-friendly registry over `Engine`:

```python
world = spherion.AudioWorld(
    sample_rate=48000,
    order=3,
    block_size=256,
    hrtf_path="",
    max_audible_distance_m=0.0,
    per_object_convolution=False,
)
world.start()

entity_id = world.spawn(
    "assets/sound.wav",
    x=0.0, y=0.0, z=10.0,
    loop=True,
    gain=1.0,
    ir_path="",  # optional per-object SOFA path
)

world.update_entity(entity_id, x=1.0, y=0.0, z=10.0, gain=0.8, active=True)
world.sync_listener(eye_x, eye_y, eye_z, yaw_rad, pitch_rad, roll_rad)
world.sync_camera(camera)  # NULLSIGHT-like camera object

world.set_per_object_convolution(True)
world.set_master_gain(0.9)
world.despawn(entity_id)
world.stop()
```

| Method | Description |
|--------|-------------|
| `spawn(path, x, y, z, loop, gain, ir_path)` | Add spatial object; returns entity id |
| `despawn(entity_id)` | Remove object |
| `update_entity(id, x, y, z, gain?, active?)` | Update pose and optional gain/active |
| `sync_listener(...)` | Calls `engine.set_listener_pose()` internally |
| `sync_camera(camera)` | Uses `camera.eye_position()`, yaw, pitch, roll |
| `cull_by_distance(eye, max_m)` | Python-side active cull (engine also culls when max distance set) |
| `contains(id)` / `get_entity(id)` | Registry lookup |

---

## Quick start

```python
import math
import spherion

engine = spherion.Engine(
    sample_rate=48000,
    order=3,
    block_size=256,
    hrtf_path="",
)
engine.start()

obj = engine.add_stereo_object("assets/loop.wav", loop=True)
obj.set_position(0.0, 5.0, 10.0)  # 10 m forward (+Z), 5 m up

engine.set_listener_pose(0.0, 1.6, 0.0, math.pi / 4.0, 0.0, 0.0)

# ... run your game loop ...

engine.stop()
```

---

## Tutorials

### Tutorial 1: Static source, rotating listener

```python
import math
import time
import spherion

engine = spherion.Engine(sample_rate=48000, order=3, block_size=256)
engine.start()

obj = engine.add_stereo_object("assets/alarm.wav", loop=True)
obj.set_position(0.0, 1.5, 8.0)

try:
    for step in range(0, 360, 2):
        yaw = math.radians(float(step))
        engine.set_listener_pose(0.0, 1.6, 0.0, yaw, 0.0, 0.0)
        time.sleep(1.0 / 60.0)
finally:
    engine.stop()
```

### Tutorial 2: Moving player through a static scene

```python
import math
import time
import spherion

engine = spherion.Engine(sample_rate=48000, order=3, block_size=256)
engine.start()

radio = engine.add_stereo_object("assets/radio_loop.wav", loop=True)
radio.set_position(10.0, 1.0, 0.0)

fountain = engine.add_stereo_object("assets/water.wav", loop=True)
fountain.set_position(-6.0, 0.0, 12.0)

try:
    t = 0.0
    while t < 30.0:
        px = 3.0 * math.sin(t)
        pz = 3.0 * math.cos(t)
        yaw = t % (2.0 * math.pi)
        engine.set_listener_pose(px, 1.6, pz, yaw, 0.0, 0.0)
        t += 1.0 / 60.0
        time.sleep(1.0 / 60.0)
finally:
    engine.stop()
```

### Tutorial 3: Hot-swapping HRTF mid-playback

```python
import time
import spherion

engine = spherion.Engine(sample_rate=48000, order=3, block_size=256)
engine.start()

obj = engine.add_stereo_object("assets/music.wav", loop=True)
obj.set_position(0.0, 2.0, 8.0)

try:
    engine.load_hrtf_async("profiles/user_a.sofa")
    while not engine.commit_hrtf():
        time.sleep(0.02)
    time.sleep(5.0)

    engine.load_hrtf_async("profiles/user_b.sofa")
    while not engine.commit_hrtf():
        time.sleep(0.02)
    time.sleep(5.0)
finally:
    engine.stop()
```

### Tutorial 4: Offline block rendering (no audio device)

```python
import numpy as np
import spherion

engine = spherion.Engine(sample_rate=48000, order=3, block_size=256)
obj = engine.add_stereo_object("assets/loop.wav", loop=True)
obj.set_position(0.0, 0.0, 5.0)

block = engine.process_block(512)  # shape (512, 2), float32
rms = float(np.sqrt(np.mean(block * block)))
print(f"RMS: {rms:.4f}")
```

### Tutorial 5: Per-object IR from NumPy arrays

```python
import numpy as np
import spherion

# Example: load taps from sidecar binaries produced by fetch_kemar_hrir.py
left = np.fromfile("tests/fixtures/mit_kemar_normal_pinna.sofa.hrir/left.bin", dtype=np.float32)
right = np.fromfile("tests/fixtures/mit_kemar_normal_pinna.sofa.hrir/right.bin", dtype=np.float32)

engine = spherion.Engine(sample_rate=48000, order=3, block_size=256)
engine.set_per_object_convolution(True)
obj = engine.add_stereo_object("assets/loop.wav", loop=True)
obj.set_ir_arrays(left, right)
obj.set_position(0.0, 0.0, 4.0)

out = engine.process_block(256)
```

### Tutorial 6: Weather-driven atmosphere (change-notify)

```python
import spherion

config = spherion.EngineConfig()
config.use_atmospheric_conditions = True
engine = spherion.Engine(config)

# Simulate weather system — call only when values change:
last_t, last_rh = 20.0, 50.0

def on_weather_changed(t_c: float, rh: float) -> None:
    global last_t, last_rh
    if abs(t_c - last_t) < 0.05 and abs(rh - last_rh) < 0.05:
        return
    if engine.notify_atmospheric_changed(t_c, rh):
        print(f"Atmosphere updated: {t_c}°C, {rh}% RH")
    last_t, last_rh = t_c, rh

on_weather_changed(28.0, 65.0)   # storm front
engine.process_block(256)        # offline: pick up effective state
print(engine.atmospheric_conditions_effective().temperature_c)
```

Alternative — register provider once:

```python
class Climate:
    temperature_c = 22.0
    relative_humidity_percent = 55.0

climate = Climate()
engine.set_atmospheric_provider(lambda: (climate.temperature_c, climate.relative_humidity_percent))
engine.set_use_atmospheric_conditions(True)
```

---

## Game integration patterns

### Pattern A: Per-frame sync from NULLSIGHT game loop

```python
def game_tick(engine, camera):
    eye = camera.eye_position()
    engine.set_listener_pose(eye[0], eye[1], eye[2], camera.yaw, camera.pitch, 0.0)
```

Call at 30–120 Hz. Order relative to physics/render does not matter as long as it is steady.

### Pattern B: AudioWorld registry

See [AudioWorld wrapper](#audioworld-wrapper).

### Pattern C: Head tracking bridge

```python
import math

def on_head_pose(yaw_deg, pitch_deg, roll_deg):
    engine.set_listener_orientation(
        math.radians(yaw_deg),
        math.radians(pitch_deg),
        math.radians(roll_deg),
    )
```

For NULLSIGHT-native cameras, pass `camera.yaw` and `camera.pitch` directly — they are already radians.

### Pattern D: Weather and atmosphere (not per frame)

**Pose:** every frame. **Weather:** only when T or RH changes.

```python
def on_biome_entered(biome) -> None:
    engine.set_use_atmospheric_conditions(biome.has_outdoor_air)
    engine.notify_atmospheric_changed(biome.temperature_c, biome.relative_humidity_percent)

def game_tick(engine, camera):
    eye = camera.eye_position()
    engine.set_listener_pose(eye[0], eye[1], eye[2], camera.yaw, camera.pitch, 0.0)
    # Do NOT call notify_atmospheric_changed here every frame
```

Or register a provider at level load — see [Atmospheric conditions and weather](#atmospheric-conditions-and-weather).

### Pattern E: Other engine conversions

**Unity (Y-up, forward = +Z)** — same axes as NULLSIGHT; no conversion needed.

**Unreal (Z-up, forward = +X):**

```python
def unreal_to_spherion(ue_x, ue_y, ue_z):
    return ue_y, ue_z, ue_x
```

---

## Performance tuning

### Recommended starting points

| Scenario | sample_rate | order | encode_order | block_size |
|----------|-------------|-------|--------------|------------|
| Prototype / laptop | 48000 | 1–3 | = order | 256 |
| Desktop game (default) | 48000 | 3–5 | 3 | 128–256 |
| Many sources (20+) | 48000 | 3 | 2–3 | 256–512 |
| High-fidelity desktop | 96000 | 5 | 3–5 | 256–512 |
| Audiophile / offline RT | 192000 | 5–7 | ≤5 | 512–1024 |
| Custom IR per source | 48000 | 3 | 3 | 256+ (profile CPU) |

### CPU cost drivers (highest to lowest impact)

1. **HOA order** — channel count grows as `(N+1)²`; SH evaluation cost scales similarly
2. **Active object count** — block-rate encoding is **O(objects per block)**, not O(objects × frames) for SH/trig
3. **HRIR length × convolution mode** — 512-tap KEMAR IRs dominate when many custom-IR convolves run
4. **`per_object_convolution=True`** — hybrid mode limits cost to custom-IR sources + one global bus
5. **Sample rate** — doubles cost at each step (48 → 96 → 192 kHz)
6. **Block size** — smaller blocks mean more callbacks per second (overhead), not more SH work

### Approximate per-block CPU model

At `order=3`, `block_size=256`, 512-tap HRIR, 48 kHz:

| Stage | Global mode | Per-object (N custom IRs) |
|-------|-------------|-------------------------|
| Block-rate SH encode | O(N objects) | O(N objects) |
| HOA → ear feeds | O(256 × channels) | O(256 × N × channels) for custom IR path |
| FIR convolution | O(256 × 512) **once** | O(256 × 512 × N) custom + **once** for global bus |

**Dominant term with many objects:** block-rate encoding (cheap) unless long IRs + per-object mode.

### Knobs that matter most

| Knob | Effect |
|------|--------|
| `encode_order` | Largest encoding savings without lowering bus order |
| `per_object_convolution=False` | Default; one convolve for all sources |
| `block_size` | Match device period; 256 is a safe default |
| Custom IR count | Each custom IR adds a full convolver in per-object mode |
| `order` | Raise only when directionality sounds blurry |

### Latency vs stability

- **block_size 128** — responsive head tracking (< 10 ms @ 48 kHz)
- **block_size 256–512** — use if you hear dropouts
- **192 kHz** — prefer 512+ block size unless CPU headroom is verified

### Profiling checklist

1. Start at `order=3`, `encode_order=3`, `block_size=256`, `sample_rate=48000`, global convolution.
2. Add objects one at a time; profile with your game's typical count.
3. Enable `per_object_convolution` only for sources that need distinct HRIRs.
4. If CPU-bound on convolution, shorten IRs or reduce custom-IR object count.
5. Increase order only if directionality is insufficient.
6. Run `python -m pytest -v` after native changes to catch regressions.

### Release build optimizations (pip default)

Pip installs use **Release** builds with:

- **`SPHERION_ENABLE_LTO=ON`** — link-time optimization (~5–15% on hot paths)
- **`SPHERION_ENABLE_AVX2=ON`** — AVX2 FMA dot products in FIR convolution (x86 Release)

---

## IR convolution modes

Spherion supports two binaural convolution strategies:

| Mode | Flag | Behavior |
|------|------|----------|
| **Global** (default) | `per_object_convolution=False` | All sources → HOA bus → **one** shared HRIR convolve |
| **Per-object (hybrid)** | `per_object_convolution=True` | Custom-IR sources convolve individually; others use **global bus** convolve |

### Global convolution (default)

```python
engine = spherion.Engine(sample_rate=48000, order=3, block_size=256, hrtf_path="profiles/default.sofa")
# Global mode is the default; explicit toggle:
engine.set_per_object_convolution(False)
```

### Per-object convolution with custom IRs

```python
config = spherion.EngineConfig()
config.per_object_convolution = True
engine = spherion.Engine(config)

footstep = engine.add_stereo_object("assets/footstep.wav", loop=False)
footstep.set_ir_path("profiles/close_field.sofa", engine.sample_rate_hz)

ambience = engine.add_stereo_object("assets/ambience.wav", loop=True)
# No custom IR: encoded into global HOA bus, one shared convolve (not N convolves).

engine.set_per_object_convolution(True)
```

### Loading IR from NumPy

```python
import numpy as np

left = np.fromfile("path/to/left.bin", dtype=np.float32)
right = np.fromfile("path/to/right.bin", dtype=np.float32)
obj.set_ir_arrays(left, right)
```

### AudioWorld shortcut

```python
world = spherion.AudioWorld(per_object_convolution=True)
entity_id = world.spawn("assets/radio.wav", 0, 0, 10, ir_path="profiles/radio_ir.sofa")
```

### Implementation details

- **`IrConvolution`** — stereo FIR with independent ring-buffer state per instance
- **Block-batched** `process_block()` — convolves entire ear-feed buffers per call
- **AVX2** dot product on x86 Release builds for inner FIR loops
- **Power-of-2 IR lengths** (128, 512, …) use bitmask ring indexing instead of modulo
- **Global IR revision** — objects without custom IR in hybrid mode do not copy coefficients every block; only on HRTF swap

---

## HRTF workflow and SOFA files

### File formats

Spherion accepts:

- **SOFA files** (`.sofa`) — parsed natively via **libmysofa** (`Data.IR`, `Data.SamplingRate`)
- **Synthesized fallback** — head-model IR when path is empty or parse fails
- **Optional sidecar directories** — `<sofa_path>.hrir/` with `left.bin`, `right.bin`, `meta.txt` (legacy / cache)

### Native SOFA loading

The C++ loader (`try_load_hrir_sofa` in `sofa_parser.cpp`) uses [libmysofa](https://github.com/hoene/libmysofa) to read AES SOFA / HDF5 files directly. No Python or h5py is required at runtime.

Load order in `load_hrir_from_path()`:

1. Empty path → synthesized IR
2. **SOFA parse** (measurement index 0 by default)
3. Optional `.hrir/` sidecar (backward compatibility)
4. Synthesized IR from file hash

Point `hrtf_path` or `set_ir_path()` at any valid `.sofa` file:

```python
engine = spherion.Engine(
    sample_rate=48000,
    order=3,
    block_size=256,
    hrtf_path="profiles/mit_kemar_normal_pinna.sofa",
)
```

### Optional HRIR sidecar convention

Sidecars are **optional**. They remain supported for faster cold loads or legacy pipelines:

```
profiles/my_hrtf.sofa
profiles/my_hrtf.sofa.hrir/
  left.bin      # float32 HRIR taps, little-endian
  right.bin
  meta.txt      # sr=..., n=..., measurement=...
```

Generate a sidecar with:

```powershell
python scripts/fetch_kemar_hrir.py --sofa profiles/my_hrtf.sofa
```

### MIT KEMAR reference IR

Download the standard MIT KEMAR dataset (SOFA only — no sidecar required):

```powershell
python scripts/fetch_kemar_hrir.py
```

This fetches [MIT KEMAR normal pinna SOFA](https://sofacoustics.org/data/database/mit/mit_kemar_normal_pinna.sofa) to:

```
tests/fixtures/mit_kemar_normal_pinna.sofa
```

The script also writes an optional `.hrir/` sidecar for legacy tests and binary comparisons:

```
tests/fixtures/mit_kemar_normal_pinna.sofa.hrir/
```

Use at engine init:

```python
engine = spherion.Engine(
    sample_rate=48000,
    order=3,
    block_size=256,
    hrtf_path="tests/fixtures/mit_kemar_normal_pinna.sofa",
)
```

**Note:** KEMAR is measured at 44.1 kHz (512 taps). The engine often runs at 48 kHz; IRs are used as-is today (resampling at load time is planned).

### Initial load at startup

```python
engine = spherion.Engine(
    sample_rate=48000,
    order=3,
    block_size=256,
    hrtf_path="profiles/default.sofa",
)
```

### Runtime swap

```python
engine.load_hrtf_async("profiles/user_measured.sofa")

# Option 1: poll from Python
while not engine.commit_hrtf():
    time.sleep(0.02)

# Option 2: auto-commit on next audio block (render_internal)
```

### Tips for good HRTF results

- Prefer measured SOFA (MIT KEMAR, THK, etc.) over random files.
- Use `.sofa` paths directly; sidecars are optional for caching only.
- Swap during quiet moments (menus, loading screens).
- Test with static and moving sources after each swap.

---

## Native implementation notes

For contributors and advanced integrators.

### Key source files

| File | Role |
|------|------|
| `src/engine.cpp` | Render orchestration, global/hybrid per-object paths, atmospheric refresh, Doppler/delay |
| `src/object_encoder.cpp` | Block-rate `ObjectEncodingWeights`, HOA accumulate |
| `src/spherical_harmonics.cpp` | SN3D SH evaluation, cached norm table |
| `src/binaural_decoder.cpp` | HOA→ear feeds, global convolver, HRTF crossfade |
| `src/ir_convolution.cpp` | Stereo FIR, AVX2 dot product, `process_block` |
| `src/stereo_object.cpp` | Bulk `render_block`, Doppler playhead, per-object convolver |
| `src/distance_model.cpp` | Inverse-distance spreading, broadband air loss |
| `src/acoustics.cpp` | Speed of sound, ITD/ILD, Doppler ratio |
| `src/atmospheric.cpp` | ISO 9613-1 absorption, condition resolve/dedup |
| `src/air_absorption_filter.cpp` | HF one-pole filter vs distance |
| `src/fractional_delay.cpp` | Propagation delay lines |
| `src/hrtf_database.cpp` | Directional SOFA lookup via libmysofa |
| `src/sofa_parser.cpp` | Sidecar load, synthesized IR fallback |
| `src/hrtf_manager.cpp` | Async load, `load_hrtf_bundle`, worker join |
| `src/python_bindings.cpp` | pybind11 API, GIL release, atmospheric provider |

### `ObjectEncodingWeights`

```cpp
struct ObjectEncodingWeights {
    std::array<float, 121> sh;
    std::size_t sh_count;
    float attenuation;
    bool preserve_stereo_width;
};
```

Computed once per object per block via `compute_object_encoding_weights()`. Per-frame work uses `accumulate_encoded_sample_to_hoa()`.

### Design goals for the audio callback

- No Python
- No file I/O
- Minimize heap allocation (reused `ObjectRenderState` buffers, pre-sized block buffers)
- Lock-free reads for objects and listener

---

## Build and CMake reference

### Pip / scikit-build defines (`pyproject.toml`)

| CMake option | Pip default | Meaning |
|--------------|-------------|---------|
| `SPHERION_ENABLE_SAF` | OFF | Optional Spatial Audio Framework |
| `SPHERION_BUILD_CPP_TESTS` | ON | Build `spherion_cpp_tests` executable |
| `SPHERION_ENABLE_LTO` | ON | Link-time optimization (Release) |
| `SPHERION_ENABLE_AVX2` | ON (CMake default) | AVX2 flags on x86 Release |

### Manual CMake configure

```powershell
cmake -B build -DSPHERION_ENABLE_LTO=ON -DSPHERION_ENABLE_AVX2=ON
cmake --build build --config Release
```

### Rebuild after C++ edits

```powershell
python -m pip install -e ".[dev]"
# or
.\scripts\rebuild_native.ps1
```

---

## Testing

### Full suite

```powershell
python -m pip install -e ".[dev]"
python -m pytest -v
```

**92 tests** as of v0.2.0, including convolution, coordinates, HRTF, physics/atmosphere, AudioWorld, lifecycle, and smoke tests.

### Test categories

| Marker / path | What it covers |
|---------------|----------------|
| `tests/test_*.py` | Unit tests (no audio device required for most) |
| `tests/smoke/` (`@pytest.mark.smoke`) | Real device playback, pose sweeps, HRTF hot-swap |
| `tests/test_convolution_online.py` (`@pytest.mark.online`) | Real MIT KEMAR HRIR download + convolution |
| `tests/test_physics_features.py` | Propagation delay, Doppler, atmospheric notify/provider/dedup |
| `tests/cpp/test_atmospheric.cpp` | Native ISO absorption and condition resolve |
| `tests/cpp/` | Native C++ tests (encoder, SH, distance, IR convolution, atmosphere) |

### Online convolution tests

Requires network (auto-fetches SOFA fixture):

```powershell
python -m pytest -m online tests/test_convolution_online.py -v
```

The online suite includes `test_pure_sofa_load_without_sidecar`, which verifies native SOFA parsing without a `.hrir/` directory.

### Smoke tests only

```powershell
python -m pytest -v -m smoke
python scripts/smoke_test.py
```

### Coordinate sanity

```powershell
python -m pytest tests/test_nullsight_coords.py -v
```

Expected at listener origin, `yaw=0`, `pitch=0`:

| Source offset | Expected |
|---------------|----------|
| `(0, 0, 5)` | Ahead (`azimuth ≈ 0`) |
| `(5, 0, 0)` | Hard right (`azimuth ≈ π/2`) |
| `(0, 5, 0)` | Above (`elevation ≈ π/2`) |

### C++ unit tests

Built when `SPHERION_BUILD_CPP_TESTS=ON`:

```powershell
cmake --build build --config Release --target spherion_cpp_tests
.\build\Release\spherion_cpp_tests.exe
```

---

## Examples in this repo

| File | What it demonstrates |
|------|----------------------|
| `examples/basic_spatial.py` | Single object, listener yaw sweep (uses `assets/loop.mp3`) |
| `examples/pose_sync.py` | Player orbit + orientation animation |
| `scripts/orbit_hrtf_demo.py` | **Listener yaw sweep**, **pitch sweep**, or optional source orbit |
| `examples/hrtf_hot_swap.py` | Async HRTF load and commit |
| `scripts/fetch_kemar_hrir.py` | Download MIT KEMAR SOFA (+ optional `.hrir` sidecar) |
| `scripts/rebuild_native.ps1` | Fast native extension rebuild |
| `scripts/smoke_test.py` | Smoke test runner |
| `tests/test_nullsight_coords.py` | NULLSIGHT coordinate sanity tests |
| `tests/test_convolution_online.py` | Real KEMAR IR convolution |

```powershell
python scripts/orbit_hrtf_demo.py --hrtf "C:\path\to\profile.sofa"
python scripts/orbit_hrtf_demo.py --mode sweep-pitch --hrtf "C:\path\to\profile.sofa"
python scripts/orbit_hrtf_demo.py --mode orbit-source --hrtf "C:\path\to\profile.sofa"
python examples/basic_spatial.py
python examples/pose_sync.py
python examples/hrtf_hot_swap.py
```

Place `assets/loop.mp3` (or pass `--audio`) before running demos.

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'spherion._native'`

The C++ extension was not built. Reinstall:

```powershell
python -m pip install -e ".[dev]"
```

### Wrong Python environment

Confirm you are using the intended interpreter:

```powershell
python -m pip show spherion
where python
```

### `Failed to decode audio file`

- Check path and miniaudio-supported format.
- Use absolute paths in production.

### `Failed to initialize miniaudio device`

- Close exclusive-mode apps using the device.
- Try `block_size` 256 or 512.
- Verify default playback device is enabled (Windows).

### No audible spatial effect

- Confirm `engine.start()` was called.
- Verify `set_active(True)` and non-silent audio file.
- Object should not be at listener position (0 relative offset).
- Update listener pose every frame.

### HRTF / SOFA has no effect

- Confirm the path is a valid AES SOFA file (not a dummy text placeholder).
- Invalid or non-SOFA files fall back to a synthesized IR (same path twice can sound identical).
- Call `set_profile` without crossfade at init for immediate IR commit.

### Audio dropouts / crackling

- Increase `block_size`.
- Lower `order`, `encode_order`, or `sample_rate`.
- Reduce active objects; use `set_active(False)` for distant sources.
- Stay on global convolution unless custom IRs are required.

### HRTF swap never completes

- Check `last_hrtf_error` and file path.
- Poll `commit_hrtf()` with timeout.
- Worker now **joins** previous load (no detached thread pile-up).

### CMake / build failures

- CMake 3.24+, C++20 compiler required.
- Network for first miniaudio fetch.
- Re-run `pip install -e ".[dev]"` after any `src/` or `include/` change.

---

## Best practices

### Runtime and integration

#### 1. Engine lifecycle

Create the engine once per session. Always stop on shutdown:

```python
engine = spherion.Engine(sample_rate=48000, order=3, block_size=256)
engine.start()
try:
    ...
finally:
    engine.stop()
```

Start the engine **before** or **after** adding objects — both work. Objects added while stopped begin playing once `start()` is called.

#### 2. Never touch Python from the audio thread

Spherion's callback is native-only. From your side:

- Update pose, gain, and spawn/despawn from the **game/control thread**.
- Use `load_hrtf_async()` for disk loads — never synchronous SOFA reads in the hot loop.
- Do not register your own Python audio callbacks that do heavy work while Spherion is running.

#### 3. Update listener pose every frame

Stale pose causes visual/audio desync. Even when stationary, sending the same pose each frame is cheap:

```python
while running:
    eye = camera.eye_position()
    engine.set_listener_pose(eye[0], eye[1], eye[2], camera.yaw, camera.pitch, 0.0)
    tick_game()
```

Prefer **`set_listener_pose()`** over separate `set_listener_position()` + `set_listener_orientation()` — one native call, one listener generation bump.

#### 4. Use eye position, never feet

NULLSIGHT's `camera.position` is feet-on-floor (`y = 0`). Audio must use `camera.eye_position()` (~1.6 m) so elevated sources image correctly.

#### 5. World space for objects — never listener-relative

```python
obj.set_position(10.0, 5.0, 10.0)  # world meters
engine.set_listener_pose(eye_x, eye_y, eye_z, yaw, pitch, roll)
```

The engine subtracts listener position internally. Manual relative math in Python **double-transforms** when the listener moves.

#### 6. Single control thread for object mutations

`add_stereo_object` and `remove_stereo_object` take a mutex on the object list. Spawn and despawn from **one thread** (loading screen, level loader, main game thread) — not concurrently from worker pools.

Pose updates (`set_position`, `set_gain`, `set_active`) are safe from the game loop while audio runs.

#### 7. Choose `AudioWorld` or raw `Engine` deliberately

| Use `AudioWorld` when… | Use raw `Engine` when… |
|------------------------|-------------------------|
| You want entity IDs and a spawn registry | You manage your own object map |
| NULLSIGHT-style `sync_camera()` fits | You need minimal wrapper overhead |
| Distance cull helpers are useful | You integrate with a custom ECS |

Both call the same native render path. `AudioWorld` is convenience, not a different mixer.

#### 8. Freeze engine config at session start

`sample_rate`, `order`, and `block_size` are fixed for the device lifetime. Changing them requires a **new** `Engine`. Plan quality targets before `start()`:

```python
config = spherion.EngineConfig()
config.sample_rate_hz = 48000
config.order = 3
config.block_size = 256
config.encode_order = 3
engine = spherion.Engine(config)
```

---

### Content, mixing, and object management

#### 9. Decode once, reuse many times

`add_stereo_object` decodes the entire file into memory. For footsteps, gunshots, or UI ticks:

- **Pool** a small set of `StereoObject` instances per sound type.
- Call `reset_playhead()` + `set_active(True)` to retrigger.
- Do **not** call `add_stereo_object` per event.

```python
footstep_pool = [engine.add_stereo_object("assets/step.wav", loop=False) for _ in range(8)]

def play_footstep(idx: int, x: float, y: float, z: float) -> None:
    obj = footstep_pool[idx % len(footstep_pool)]
    obj.set_position(x, y, z)
    obj.reset_playhead()
    obj.set_active(True)
```

#### 10. Pause vs remove vs mute

| Goal | API |
|------|-----|
| Temporarily silent, keep in mixer | `obj.set_active(False)` |
| Stop and free mixer slot | `engine.remove_stereo_object(obj)` |
| Fade entire mix | `engine.set_master_gain(0.0)` |
| Mute one source but keep position | `obj.set_gain(0.0)` + `set_active(True)` |

Use `set_active(False)` for distance culling and off-screen sources. Use `remove_stereo_object` when the entity is destroyed permanently.

#### 11. Moving world objects — update position only

When a source moves (vehicle, NPC, projectile), update world position each frame. Do not respawn:

```python
obj.set_position(npc_x, npc_y, npc_z)
```

Block-rate encoding recomputes SH weights once per block from the new position automatically.

#### 12. Master gain and per-object gain staging

Leave headroom for simultaneous sources:

```python
engine.set_master_gain(0.85)   # global headroom
obj.set_gain(0.7)                # loud source attenuation
```

Clipping in the HOA bus is not gracefully limited — staging gains prevents harsh summing when many objects overlap.

#### 13. `preserve_stereo_width` — when to disable

`EngineConfig.preserve_stereo_width = True` (default) encodes L/R difference into HOA for stereo sources (radios, music).

Disable for **mono-like** point sources if you want tighter imaging:

```python
config.preserve_stereo_width = False
```

Most gameplay SFX can stay `True`; dialogue beds often sound fine either way.

#### 14. Looping vs one-shot semantics

| `loop=True` | `loop=False` |
|-------------|--------------|
| Ambience, music, machines | Footsteps, impacts, UI |
| `is_playing()` always True while active | `is_playing()` False after playhead exhausts |
| No `reset_playhead()` needed | Call `reset_playhead()` before replay |

For one-shots, check `is_playing()` before retriggering the same pool slot to avoid cutting off a tail mid-convolve.

---

### Distance, culling, and audibility

#### 15. Prefer engine-side distance culling

Set once at init or when the cull radius changes:

```python
engine.set_max_audible_distance(80.0)
```

The render path skips encoding beyond this distance with a smooth fade. This is cheaper than Python-side distance loops every frame.

`AudioWorld.cull_by_distance()` is optional redundancy — useful if you also need a Python-side entity count, but not required when `max_audible_distance_m` is set on the engine.

#### 16. Hysteresis for cull on/off (game logic)

Toggling `set_active` exactly at the boundary causes audible chatter. In your game code, use separate enter/exit radii:

```python
CULL_ON = 80.0
CULL_OFF = 70.0

if dist > CULL_ON:
    obj.set_active(False)
elif dist < CULL_OFF:
    obj.set_active(True)
```

#### 17. Minimum audible distance

Sources very close to the listener ear (< 0.3 m relative) can image unnaturally. Offset emitters slightly from entity origins:

```python
obj.set_position(npc_x, npc_y + 0.2, npc_z)  # mouth height, not pivot
```

---

### HRTF, HRIR, and assets

#### 18. Ship `.sofa` files directly

Spherion reads SOFA natively. Point `hrtf_path` at measured profiles:

```python
engine = spherion.Engine(..., hrtf_path="profiles/game_hrtf.sofa")
```

Optional `.hrir/` sidecars can still be pre-extracted for faster startup in legacy pipelines:

```powershell
python scripts/fetch_kemar_hrir.py --sofa profiles/game_hrtf.sofa
```

#### 19. Match sample rate when possible

Engine at 48 kHz + HRIR measured at 44.1 kHz works today (no resampling yet) but ITD/ILD scaling may be slightly off. Prefer:

- Engine `sample_rate_hz = 48000` with HRIRs extracted at 48 kHz, or
- Document the mismatch for your QA team until load-time resampling lands.

#### 20. One default HRTF, optional user profiles

Ship a known-good default (synthesized or a measured `.sofa`). Expose user HRTF selection as an **opt-in** menu setting with fallback:

```python
if not safe_hrtf_swap(engine, user_path):
    safe_hrtf_swap(engine, "profiles/default.sofa")
```

#### 21. HRTF hot-swap discipline

- Swap during menus, pauses, or loading screens.
- Do not call `load_hrtf_async` every frame.
- Check `last_hrtf_error` on failure.
- Rely on render-loop auto-commit, or poll `commit_hrtf()` with a timeout:

```python
def safe_hrtf_swap(engine, path: str, timeout_s: float = 5.0) -> bool:
    import time
    engine.load_hrtf_async(path)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if engine.commit_hrtf():
            return True
        if not engine.is_hrtf_loading and engine.last_hrtf_error:
            return False
        time.sleep(0.02)
    return False
```

#### 22. File and path hygiene

Resolve paths at startup; avoid relative paths that depend on `cwd`:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
PROFILES = ROOT / "profiles"

engine.add_stereo_object(str(ASSETS / "loop.wav"), loop=True)
```

Pre-decode long music beds to **WAV** for fastest `add_stereo_object` load times.

#### 23. Headphones for spatial QA

Binaural output is designed for **headphones**. Speaker playback will not validate HRTF imaging. QA spatial audio on headphones even if the game supports speakers for non-spatial mix.

---

### Performance

#### 24. Choose `order` and `encode_order` deliberately

| Content | order | encode_order |
|---------|-------|--------------|
| UI / dialogue | 1 | 1 |
| Ambient / music | 3 | 3 |
| Gameplay cues | 3–5 | 3 |
| Showcase / VR | 5–7 | ≤5 |

`encode_order` is the cheapest quality knob — lower it before dropping bus `order`.

#### 25. Manage object count

- **< 10 objects:** order 3, global mode — safe on most machines
- **10–30:** profile CPU; lower `encode_order`
- **30+:** cull with `set_active(False)` or `max_audible_distance_m`

#### 26. Convolution mode discipline

- Default **global** for almost all scenes.
- Enable **per_object** only when specific sources need distinct measured IRs.
- In hybrid mode, only `has_custom_ir()` sources pay per-object convolve cost.
- Assign custom IRs with `set_ir_path` / `set_ir_arrays` only where acoustically justified (intimate VO, radio filter, underwater muffling).

#### 27. Match `block_size` to your interaction model

| Use case | Suggested block_size |
|----------|---------------------|
| Head-tracked VR | 128–256 |
| First-person PC game | 256 |
| Third-person / strategy | 256–512 |
| Menu / ambient only | 512 |

If you hear dropouts, **increase block_size first** before lowering order.

#### 28. Sample rate strategy

- **48000 Hz** — default; best compatibility with HRIR databases.
- **96000 Hz** — music-forward desktop titles with CPU headroom.
- **192000 Hz** — specialized; pair with order ≤ 5 and profile first.

Recreating the engine is required to change sample rate.

#### 29. Load heavy work off the gameplay frame

Decode and spawn during loading screens:

```python
def load_level_audio(engine, manifest: list[tuple[str, float, float, float]]):
    objects = []
    for path, x, y, z in manifest:
        obj = engine.add_stereo_object(path, loop=True)
        obj.set_position(x, y, z)
        objects.append(obj)
    return objects
```

Avoid decoding large libraries during combat or cutscenes.

---

### Development, CI, and debugging

#### 30. Rebuild after native changes

Any edit under `src/` or `include/`:

```powershell
python -m pip install -e ".[dev]"
# or
.\scripts\rebuild_native.ps1
```

Editable install does not always rebuild C++ automatically.

#### 31. Test without audio hardware

```powershell
python -m pytest -q
```

Use `process_block()` for golden-file or regression tests:

```python
block = engine.process_block(engine.block_size)
assert block.shape == (engine.block_size, 2)
```

#### 32. Run coordinate tests after pose integration

```powershell
python -m pytest tests/test_nullsight_coords.py -v
```

Run after any custom camera bridge or coordinate conversion layer.

#### 33. Run convolution tests when changing HRTF assets

```powershell
python scripts/fetch_kemar_hrir.py
python -m pytest tests/test_convolution_online.py -v
```

#### 34. Debugging spatial issues — ordered checklist

1. Is `engine.start()` called?
2. Is the object `active` and not exhausted (`loop=True` or `reset_playhead()`)?
3. Is the object > 0.5 m from the listener (relative)?
4. Are angles in **radians**?
5. Is **eye position** used, not feet?
6. Is order ≥ 3 for directional content?
7. Are **headphones** connected?
8. For real SOFA: is the file a valid AES SOFA (HDF5) profile?
9. After native changes: did you reinstall?

#### 35. Log HRTF state in development builds

```python
if engine.last_hrtf_error:
    logger.warning("HRTF error: %s", engine.last_hrtf_error)
if engine.is_hrtf_loading:
    logger.debug("HRTF load in progress...")
```

---

### Production and multi-process integration

#### 36. Production hardening checklist

- [ ] `try` / `finally` with `engine.stop()`
- [ ] Absolute asset and profile paths
- [ ] Cap max simultaneous active objects
- [ ] `max_audible_distance_m` set on engine
- [ ] Default HRTF + fallback on user profile failure
- [ ] Measured `.sofa` HRTF profiles included in build
- [ ] Profile CPU at target order, object count, and sample rate
- [ ] `pytest` in CI after native builds
- [ ] Document coordinate conversion from your game engine
- [ ] Headphones called out in audio settings UX
- [ ] Weather wired via `notify_atmospheric_changed` or `set_atmospheric_provider` (not per frame)
- [ ] Indoor scenes use `set_use_atmospheric_conditions(False)` when appropriate
- [ ] Non-diegetic audio uses `spatial_bypass` / `spawn_direct`
- [ ] Decoded audio memory budget estimated for longest loops
- [ ] `clear_atmospheric_provider()` on level/session teardown
- [ ] Optional physics toggles profiled before shipping (delay, Doppler, directional HRTF)

#### 37. Sidecar Python process pattern (Unity / Unreal)

For non-Python game engines, a **sidecar process** owning Spherion is the most reliable pattern:

```
Game (C#/C++)  --UDP/socket-->  Python Spherion bridge
  pose, spawn, gain commands         owns Engine + device
```

Send compact binary or JSON commands at 60 Hz. Do not embed Python in the game render thread.

#### 38. Pause and focus loss

On application pause/minimize:

```python
engine.stop()   # releases device
# on resume:
engine.start()
```

Alternatively lower `set_master_gain(0.0)` if you want to keep the device open. `stop()` is safer on mobile and when OS audio policies suspend background apps.

#### 39. Version and environment pinning

Pin Spherion version in production requirements and verify the native extension loads:

```python
import spherion
from spherion._native import Engine  # fails fast if _native missing
```

Document the Python version and `pip install -e ".[dev]"` step for anyone building from source.

#### 40. Atmospheric and weather integration

- **Notify on change**, not every frame: `notify_atmospheric_changed(T, RH)` or `set_atmospheric_provider(...)`.
- **Indoor levels:** `set_use_atmospheric_conditions(False)` — fixed 20 °C / 50 % RH effective air.
- **Provider callables** must be O(1): read two floats, no I/O, no allocation.
- **Inspect effective state** in tests with `process_block()` after notify/toggle.
- See [Atmospheric conditions and weather](#atmospheric-conditions-and-weather) for NULLSIGHT wiring.

---

### Physics and realism tuning

#### 41. Enable optional physics deliberately

Defaults are tuned for **responsive gameplay**, not simulation demos:

| Feature | Default | Enable when… |
|---------|---------|--------------|
| `enable_hf_air_absorption` | `True` | Almost always; subtle distance dulling |
| `enable_propagation_delay` | `False` | Large outdoor maps, artillery/thunder at range |
| `enable_doppler` | `False` | Fast vehicles, trains, swooping projectiles |
| `directional_hrtf` | `False` | Showcase/desktop with CPU headroom + valid SOFA grid |
| `use_atmospheric_conditions` | `False` | Outdoor biomes with weather simulation |

Turn features on **one at a time** and A/B on headphones. Stacking delay + Doppler + directional HRTF + order 5 is a profiling task, not a default.

#### 42. Match propagation delay cap to your world scale

`max_propagation_delay_s = 0.05` caps delay at ~17 m @ 343 m/s. For a game with `max_audible_distance_m = 200`, distant events still arrive with only 50 ms extra latency — by design, to avoid breaking responsiveness.

If you enable delay for a sniper sim at 300 m, accept the cap or lower `max_audible_distance_m` so perceived delay matches design. Do not expect full `r/c` delay beyond the cap.

#### 43. Feed plausible velocities for Doppler

Velocities are **world metres per second**, snapshotted once per block:

```python
# Good: physics body linear velocity
engine.set_listener_velocity(body.vx, body.vy, body.vz)
obj.set_velocity(projectile_vx, projectile_vy, projectile_vz)

# Bad: displacement per frame without dividing by dt
# obj.set_velocity(dx, dy, dz)  # wrong units — sounds like hypersonic pitch shift
```

Walking speeds (~1.5 m/s) produce negligible Doppler. Enable the toggle only when gameplay actually exceeds ~5 m/s radial closure.

#### 44. Tune `reference_distance_m` for game feel, not just physics

Mode 1 rolloff uses `reference_distance_m / r`. At `reference_distance_m = 1.0`, a source at 10 m is −20 dB (~0.1 pressure). If ambience dies too fast:

- Raise `reference_distance_m` slightly (e.g. 2–4 m) for **louder mid-field** without abandoning 1/r law.
- Or raise per-object / master gain — but reference distance affects all sources consistently.

Document your chosen reference distance in the audio design doc so level designers place emitters consistently.

#### 45. Indoor scenes: disable live atmosphere, keep HF absorption

For interior levels with no weather sim:

```python
engine.set_use_atmospheric_conditions(False)
# Effective air stays 20 °C / 50 % RH — stable absorption curve
```

`enable_hf_air_absorption` remains independent. To make indoor corridors brighter at range, disable HF absorption in config — not the atmospheric toggle alone.

---

### Diegetic vs non-diegetic routing

#### 46. Use spatial bypass for non-diegetic audio

Menu music, UI stingers, narrator VO, and pause-layer beds should **not** go through HOA + HRTF. They belong in stereo, full-width, unaffected by listener pose:

```python
# Raw Engine:
obj = engine.add_stereo_object("assets/ui_click.wav", loop=False)
obj.set_spatial_bypass(True)

# AudioWorld helper:
menu_bed = world.spawn_direct("assets/menu_theme.mp3", loop=True, gain=0.6)
```

Bypass objects skip distance cull, Doppler, propagation delay, and SH encoding — lower CPU and correct “in your head” presentation.

#### 47. Keep diegetic and non-diegetic on one engine

You do not need two `Engine` instances. Mix spatial world sources and bypass UI sources in the same engine; the render path sums both into one device output. Use bypass for anything that should not move when the player looks around.

#### 48. Do not spatialise pure mono UI ticks

Short mono UI sounds spatialised at `(0, 0, 1)` ahead of the listener image oddly as the player turns. Bypass them or place them at world anchors only when diegetic (e.g. a physical terminal in the level).

---

### Level design, elevation, and occlusion workarounds

#### 49. Author emitters at meaningful heights

NULLSIGHT is Y-up. Match visual anchors:

| Source type | Suggested Y offset |
|-------------|-------------------|
| Footsteps / body | entity pivot + 0.0–0.2 m |
| Voice / NPC dialogue | mouth ~1.5–1.7 m |
| Wall-mounted speaker | mount height on wall |
| Ceiling machinery | ceiling Y − offset |
| Ground insects / small props | floor + 0.05 m |

Using feet position (`y = 0`) for elevated dialogue makes voices sound buried.

#### 50. Multi-floor and vertical gameplay

There is no floor occlusion. A source one storey above encodes normally by elevation angle — which is physically correct for open stairwells but wrong through concrete.

Mitigations in game logic:

- Duck gain when ray/portal graph says “blocked”
- Apply a simple low-pass (game-side) for muffled-through-wall colouration
- `set_active(False)` when floor index differs and no shared opening exists

Spherion does not replace a full occlusion system; plan game-side rules for indoor level layouts.

#### 51. Per-source max distance in design docs

Even with engine `max_audible_distance_m = 80`, assign **design radii** per category:

| Category | Typical max audibility |
|----------|------------------------|
| Footsteps / Foley | 15–25 m |
| NPC dialogue | 20–30 m |
| Gunfire | 80–150 m |
| Ambience loops | 40–80 m |
| UI / bypass | N/A (not spatial) |

Use `set_active(False)` or lower `set_gain` beyond category radius — the engine fade is global, not per-category.

#### 52. Hysteresis on `set_active`, not on gain every frame

Avoid riding `set_gain` every frame from distance — it fights block-rate encoding and is hard to tune. Prefer:

- Binary `set_active` with hysteresis (see §16), or
- A small set of gain steps updated at 5–10 Hz, not 60 Hz

---

### Memory, lifecycle, and session hygiene

#### 53. Budget decoded PCM memory at load time

`add_stereo_object` decodes the **entire file** into RAM. Rough stereo PCM size:

```
bytes ≈ duration_s × sample_rate × 2 channels × 4 bytes (float32 internal)
```

A 3-minute stereo 48 kHz track ≈ 70 MB per object. Preload manifests during loading screens and cap simultaneous decoded beds. Prefer shorter loops for ambience.

#### 54. Despawn removes native objects — keep Python references tidy

```python
engine.remove_stereo_object(obj)  # or world.despawn(entity_id)
obj = None  # drop Python ref; native buffers freed when unreferenced
```

Leaking `StereoObject` references in registries keeps decoded audio in memory after the entity is gone.

#### 55. Clear atmospheric provider on session / level teardown

If you registered a provider that closes over level state:

```python
engine.clear_atmospheric_provider()
engine.set_use_atmospheric_conditions(False)
```

Prevents the audio thread from calling stale lambdas after level unload. Re-register on next level load.

#### 56. One engine per audio output session

Creating a new `Engine` per level works but pays device open/close cost. Preferred pattern:

```python
# Level load: add/remove objects, reset gains
# Level unload: remove_stereo_object for all spawned sources
# App exit: engine.stop()
```

Reuse the same engine across levels when sample rate, order, and block size are unchanged.

---

### Testing, QA, and iteration

#### 57. Regression-test physics with `process_block`, not the device

```powershell
python -m pytest tests/test_physics_features.py -v
```

Headless CI cannot open miniaudio devices reliably. Offline blocks validate Doppler, atmosphere, and config without hardware.

#### 58. A/B spatial changes with a fixed listener orbit script

Use `scripts/orbit_hrtf_demo.py` or a small yaw sweep script when tuning HRTF, order, or reference distance — compare before/after on headphones with the same orbit path. Ad-hoc WASD testing confounds position and orientation changes.

#### 59. Log generation tags when debugging pose desync

```python
state = engine.listener_state()
# state.generation increments when pose updates land
```

If generation stalls while the camera moves, pose calls are not reaching the engine (wrong thread, wrong object, exception swallowed upstream).

#### 60. After native rebuild failures on Windows, retry install

Editable installs can fail with `Access is denied` on `_native.pyd` when Python still has the module loaded. Close running game processes and retry:

```powershell
python -m pip install -e ".[dev]"
```

Or use `.\scripts\rebuild_native.ps1` during active development.

---

### Anti-patterns

| Anti-pattern | Why |
|--------------|-----|
| New `Engine` per sound | Huge overhead; one engine mixes all objects |
| Listener-relative object coords | Double-transform when listener moves |
| `per_object_convolution=True` without custom IRs | Wastes CPU; hybrid helps, global is still better |
| order 10 @ 192 kHz without profiling | Likely real-time failure |
| `add_stereo_object` per footstep | Decode cost paid every time |
| Ignoring `stop()` on exit | Leaves device handles open |
| Sync SOFA load in game loop | Frame hitches; use `load_hrtf_async` |
| Spawning from multiple threads | Mutex contention on object list |
| `commit_hrtf()` tight spin without sleep | Wastes CPU; sleep 10–20 ms between polls |
| QA spatial audio on laptop speakers | Invalidates HRTF imaging |
| Assuming degrees for yaw/pitch | Silent wrong imaging (always radians) |
| Using `camera.position` for audio | Sources sound too low / wrong elevation |
| `notify_atmospheric_changed` every frame | Use change events or provider; engine dedupes but wastes calls |
| `distance_rolloff = 2` expecting physics | Inverse-square on amplitude is too steep; use mode 1 (1/r) |
| Heavy logic in atmospheric provider | Blocks audio thread with GIL; keep provider trivial |
| Spatialising menu/UI audio | Use `set_spatial_bypass(True)` or `spawn_direct()` |
| Doppler with per-frame displacement as velocity | Must be m/s; divide by `dt` from physics |
| `enable_propagation_delay` for close-quarters FPS | Sub-5 m delay is inaudible; adds buffer cost |
| Keeping dead level state in atmospheric provider | Clear provider on level unload |
| Riding `set_gain` every frame from distance | Use hysteresis `set_active` or coarse steps |
| Ignoring decoded file RAM cost | Long MP3 loops × many objects exhaust memory |
| Two engines for “UI + world” | One engine; bypass path for non-diegetic |
| `directional_hrtf=True` without profiling | Per-source SOFA lookup is expensive |
| Expecting wall occlusion from Spherion alone | Implement game-side muffling rules |

---

## Roadmap and current limitations

Spherion v0.2 is a production-oriented CPU HOA engine. Known boundaries:

| Area | Current state |
|------|---------------|
| HOA encoding | SN3D SH up to order 10; block-rate encode; `encode_order` CPU cap |
| Orientation | Yaw/pitch/roll radians; generation-tagged listener snapshots |
| Binaural decode | Time-domain FIR; AVX2 on x86 Release; block-batched convolve |
| Convolution modes | Global (default); hybrid per-object for custom IRs only |
| SOFA / HRIR | Native libmysofa parse of `Data.IR`; optional `.hrir/` sidecar fallback |
| HRIR resampling | Not yet — engine SR may differ from measurement SR (e.g. 44.1 vs 48 kHz) |
| HRTF swap | Gain crossfade; full dual-IR crossfade not implemented |
| Live PCM input | File-based stereo objects only; no `feed_pcm()` yet |
| Partitioned FFT conv | Planned for 512+ tap IRs at scale |
| Object removal | `remove_stereo_object()`, `AudioWorld.despawn()` |
| Coordinates | Fixed NULLSIGHT: Y-up, +Z forward, radians, eye position |
| Offline API | `process_block()` direct-to-NumPy |
| Distance physics | Inverse 1/r (mode 1); optional ISO air absorption |
| Atmosphere | Change-notify + provider; toggle off = 20 °C / 50 % RH |
| Propagation delay | Optional per-object; 50 ms cap default |
| Doppler | Optional; source + listener velocity |
| Directional HRTF | Optional libmysofa per-source direction |
| Room reverb | Not modeled — free-field only |
| SAF integration | Optional build flag; not in render path |

For faster native rebuilds: `.\scripts\rebuild_native.ps1`

---

## Quick reference card

```python
import math
import spherion

# Setup
engine = spherion.Engine(48000, 3, 256, hrtf_path="")
engine.start()

# Spawn (+Z forward)
obj = engine.add_stereo_object("assets/sound.wav", loop=True)
obj.set_position(x, y, z)
obj.set_active(True)
obj.set_ir_path("profiles/custom.sofa", engine.sample_rate_hz)  # optional

# Per-frame sync (preferred)
engine.set_listener_pose(eye_x, eye_y, eye_z, yaw_rad, pitch_rad, roll_rad)

# Weather — on change only, not every frame:
engine.set_use_atmospheric_conditions(True)
engine.notify_atmospheric_changed(28.0, 65.0)
# or: engine.set_atmospheric_provider(lambda: (weather.t, weather.rh))

# Physics toggles (EngineConfig at init):
# enable_hf_air_absorption=True, enable_doppler=False, enable_propagation_delay=False

# Convolution mode
engine.set_per_object_convolution(False)  # global (default)

# HRTF swap
engine.load_hrtf_async("profiles/new.sofa")
engine.commit_hrtf()

# Offline render
block = engine.process_block(256)  # (256, 2) float32

# Teardown
engine.stop()
```

---

*For build instructions and a minimal example, see [README.md](README.md).*
