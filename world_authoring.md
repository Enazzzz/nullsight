# NULLSIGHT — World Authoring Guide

How to build the playable space for NULLSIGHT. The game is **one contiguous facility**, not a stack of separate levels. You walk from room to room; scan memory and audio carry forward with no reload.

Use **sections** only for planning (entry hall, east wing, pump room). The player never sees section boundaries.

---

## Quick start

1. Sketch the layout on **1 m grid paper** (top-down, X right, Z down on paper = +Z forward in game).
2. Add a `build_*()` function in `levels.py` using `LevelBuilder`.
3. Point `load_world()` at your builder.
4. Add spatial sounds in `nullsight_audio.py` → `_level_sounds()` (or extend `RoomData` later).
5. Run `python main.py` and iterate.

```python
# levels.py — minimal new world

def build_my_facility() -> RoomData:
	return (
		LevelBuilder(24.0, 18.0)
		.name("facility")
		.spawn_at(2.0, 2.0)
		.outer_shell()
		.wall_x(12.0, 0.0, 7.0)    # wall with gap z=7–11 below
		.wall_x(12.0, 11.0, 18.0)
		.pillar(6.0, 14.0)
		.build()
	)


def load_world() -> RoomData:
	return build_my_facility()
```

---

## Coordinate system

| Axis | Direction | Notes |
|------|-----------|--------|
| **X** | Right | Room spans `0 … width` |
| **Y** | Up | Floor default `0`, ceiling default `4` |
| **Z** | Forward | Room spans `0 … depth` |

- Units are **meters**.
- Player **feet** sit on the XZ plane (`y = 0` at spawn).
- **Eye height** is `1.6 m` above feet (`camera.py`).
- Default spawn **faces +Z** (yaw `0`).

When placing audio, use world `(x, y, z)` with `y` around `1.0–2.0` for ear-level sources.

---

## LevelBuilder API

Import from `level_builder.py`. Every builder call returns `self` for chaining.

### Footprint

```python
LevelBuilder(width, depth, floor_y=0.0, ceiling_y=4.0)
```

The room always occupies **`[0, width] × [0, depth]`** on the floor. There is no offset API yet — plan one big bounding box and place sections inside it.

### Metadata

| Method | Purpose |
|--------|---------|
| `.name("facility")` | Identifier on `RoomData.name` (audio lookup uses this today) |
| `.spawn_at(x, z, y=0.0)` | Player start position |

If you omit spawn, it defaults to the room center.

### Geometry

| Method | Blocks scans? | Description |
|--------|---------------|-------------|
| `.outer_shell()` | Boundaries only | Outer wireframe + 1 m floor/ceiling/wall grids. Shell occlusion uses **boundary planes**, not AABBs. |
| `.wall_x(x, z0, z1)` | Yes | Wall on constant **X**, from `z0` to `z1`, floor to ceiling. |
| `.wall_z(z, x0, x1)` | Yes | Wall on constant **Z**, from `x0` to `x1`, floor to ceiling. |
| `.pillar(x, z, size=1.2)` | Yes | Floor-to-ceiling column `(x,z)` to `(x+size, z+size)`. |
| `.box(x0, y0, z0, x1, y1, z1)` | Yes | Axis-aligned prop or block. |
| Any of above + `see_through=True` | **No** | Wireframe only — visible, does not occlude scans. |
| Any solid + `collidable=False` | Yes (scan) / **No** (walk) | Blocks scans but the player can walk through. |

Solid walls get a **0.2 m** thick occluder slab automatically. **Collidable by default** unless `collidable=False`. Wireframe edges are subdivided **4×** for a denser look.

### Finish

```python
.build()  # → RoomData
```

---

## Doorways and connected rooms

There are no door entities. **Leave a gap** in a wall by splitting it into two segments:

```
        z=0                    z=18
         |----------------------|
         |      ROOM A          |
         |                      |
    x=12 ████████   gap   ████████  ← two .wall_x() calls
         |                      |
         |      ROOM B          |
         |----------------------|
```

Example — 4 m doorway centered at `z = 9`:

```python
.wall_x(12.0, 0.0, 7.0)   # z: 0 → 7
.wall_x(12.0, 11.0, 18.0)  # z: 11 → 18
# opening: z 7 → 11
```

Corridor width = gap size. **2 m** minimum feels comfortable; **1 m** feels tight.

Reference: `build_large_level()` in `levels.py` — multiple split walls and a pillar grid with deliberate gaps.

---

## Planning workflow

### 1. Top-down sketch

Draw the **outer perimeter** first (that becomes `LevelBuilder(width, depth)`). Mark:

- Solid walls (split for doorways)
- Pillars and props
- Spawn (usually near one edge, facing into the facility)
- Beacon / hum positions (ahead of spawn on the first room)

### 2. Section labels (optional)

Comment blocks in code — invisible to the player:

```python
# --- Section: entry (0–12 x, 0–12 z) ---
# --- Section: east hall (12–24 x, 0–18 z) ---
```

### 3. Build order

1. `.outer_shell()`
2. Interior `.wall_x` / `.wall_z` (doorways via gaps)
3. `.pillar()` / `.box()` props
4. `.spawn_at()`
5. `.build()`

### 4. Wire to the game

In `levels.py`:

```python
def load_world() -> RoomData:
	return build_my_facility()
```

`main.py` calls `load_world()` — no CLI flags.

### 5. Audio

In `nullsight_audio.py`, `_level_sounds(room)` returns a list of `LevelSound` entries:

```python
LevelSound("beacon", x, y, z, gain=0.32)
LevelSound("hum", x, y, z, gain=0.20)
```

Asset keys: `"beacon"`, `"hum"` (bundled in `assets/`).

**Placement tips:**

- Put the first **beacon** somewhere the player can hear within ~5 s of spawn, ideally **ahead (+Z)**.
- Place sources **behind walls** you want the player to discover later — occlusion is the point.
- Match `room.name` in a branch, or add a shared list when you move sounds onto `RoomData`.

### 6. Test

```powershell
python main.py
python bench.py          # edge count + FPS for small/large presets
```

In Python:

```python
from levels import load_world, count_edges
room = load_world()
print(room.spawn, room.bounds, count_edges(room))
```

---

## Scan device constraints

Design geometry around how the scan works (`scan.py` defaults):

| Parameter | Default | Design use |
|-----------|---------|------------|
| Max radius | 14 m | One pulse covers ~28 m diameter; larger spaces need multiple vantage points |
| Cooldown | 5.6 s | Time between pulses — corridors slow pacing naturally |
| Wave speed | 10 m/s | ~1.4 s for pulse to reach max radius |
| FOV | 70° | Must **face** geometry to capture it; corners matter |

Solid walls and props **block** scan rays. `see_through` geometry does not.

Scan **memory persists** for the whole session — no reset when walking into a new section.

---

## Movement bounds

The camera is clamped to `room.bounds` with a **0.35 m** margin from the outer shell. Interior walls do **not** block movement yet — you can walk through pillars and dividers. Layout still guides exploration and occlusion.

---

## Examples in the repo

| Function | Size | Good for learning |
|----------|------|-------------------|
| `build_small_level()` | 12×12 | Minimal shell + one pillar + one box + spawn |
| `build_large_level()` | 40×40 | Split walls, doorways, pillar grid, scattered props |

To temporarily play the warehouse while authoring:

```python
def load_world() -> RoomData:
	return build_large_level()
```

---

## Performance

More geometry → more wireframe edges → higher GPU/CPU cost.

- Floor/ceiling/wall grids: **1 m** spacing
- Each edge subdivided **4×**
- Dense pillar grids add up fast — see `build_large_level()` loop

Check cost:

```python
from levels import count_edges, load_world
print(count_edges(load_world()))
```

Run `bench.py` before and after large layout changes.

---

## Current limitations

| Limitation | Workaround |
|------------|------------|
| Single axis-aligned bounding box | One large `LevelBuilder(w, d)`; sections live inside |
| Origin fixed at `(0, 0)` | No world offsets yet — plan coordinates from corner |
| No interior collision | Use `collidable=True` (default) on solids; pass `collidable=False` to opt out |
| Audio keyed by `room.name` | Use one name (e.g. `"facility"`) or extend `_level_sounds` |
| Fixed 4 m ceiling | Pass `ceiling_y=` if you extend builder per-room later |

---

## Checklist before shipping a new world

- [ ] Spawn faces into the facility (+Z), not into a wall
- [ ] Outer shell matches sketch; no accidental gaps in the perimeter
- [ ] Doorway gaps are wide enough to walk through (≥ 2 m recommended)
- [ ] At least one audio landmark reachable or audible from spawn
- [ ] `load_world()` points at your builder
- [ ] Sounds added/updated in `_level_sounds()`
- [ ] Quick playtest: scan, walk every section, listen for beacons through walls
- [ ] `count_edges()` / `bench.py` if the layout is large or pillar-heavy

---

## File reference

| File | Role |
|------|------|
| `level_builder.py` | Authoring API |
| `levels.py` | World builders + `load_world()` |
| `room.py` | `RoomData` payload |
| `nullsight_audio.py` | Spatial loop placement |
| `main.py` | Loads world at startup |
| `scan.py` | Scan radius, cooldown, memory |
