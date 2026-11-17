# NULLSIGHT — Deep Horror Design Spec

**Date:** 2026-07-09  
**Status:** Draft for review (core fantasy locked with author)  
**Product:** NULLSIGHT (v0.1.x scan-device prototype → story horror game)  
**Tone:** Psychological / existential dread. No jump-scare cadence. Stress that lingers after the session.

---

## 1. Core fantasy (locked)

You wake in the dark with **no memory of who you are or why you are here**. You can move. You can “scan.” You never see a handheld gadget — only an **eye** on the HUD that **bleeds more as integrity falls**. Muscle memory works; identity does not.

The place is **one contiguous underground facility**. You walk room to room. Scan memory and audio never reset on a “level load.” Signs, drafts, and distant radio keep pointing toward an **exit**. Something else is in here with you — mostly sound, rarely a wrong silhouette in the wireframe. Using the eye costs you. Blindness is death; seeing too much is also death.

**The trap:** Escape is the only goal that makes sense the whole time. Completing it **kills you**. The story never lectures that in a cutscene; the facility’s own documents and the ending threshold make it unavoidable after the fact.

**Tone rules:**

- No jump-scare timing as the primary scare tool.
- Dread from wrong continuity (scan vs wall vs radio), cost of sight, and a presence that does not need to chase every minute.
- After-session stress comes from realizing the player spent the whole game doing the “right” thing.

---

## 2. Player premise (locked)

| Question | Decision |
|----------|----------|
| Who are you? | **Unknown.** Wake / start with no briefing, badge, or stated role (option C). |
| What is the scanner? | **A + D.** You use it with muscle memory, but you never see a device. The HUD is an eye. It may be a tool; it may be *you*. The game never confirms. |
| Apparent goal | **Escape** the facility. |
| True ending | **Escape = death** (false goal → revelation). |
| Threat mix | **Environment + presence + cost-of-seeing** (A + B + C). |
| Story delivery | **Environmental only** — notes, labels, radio fragments, scan-revealed wall text, overheard audio. No journal menu, no lore cutscene dumps. |
| World structure | **Facility as autopsy** — one walkable complex; story layered in space. **Almost no level framing** (no chapter titles, no load screens between wings, no “Level 2”). |

---

## 3. Facility anatomy (design bible — not player-facing “levels”)

Planning uses **places**, not levels. The player experiences one continuous walk. Internal names below are for authors and code (`RoomData` sections / trigger volumes), never shown as stage titles.

### 3.1 Spatial spine (recommended)

Build as **one contiguous map** (or streamed chunks that feel seamless). Invisible planning layers:

1. **Threshold / Wake** — Dark, sparse geometry, radio hum, first eye bleed tutorial without tutorial chrome.
2. **Habitation** — Beds, lockers, nameplates that almost fit; wrong continuity starts (scan memory vs prop).
3. **Operations** — Corridors, doors, EXIT signs that disagree; presence territory begins (audio stalking).
4. **Research / Records** — Documents and radios that imply what the facility *did*; cost-of-seeing spikes.
5. **Containment** — Geometry and audio that feel “aware”; presence denser; eye integrity scarce.
6. **Exit threshold** — The door/shaft/airlock the whole game pointed at. Completing it is the lethal ending.

Authors may expand or rename places; the spine order (wake → human → system → truth → exit) should hold.

### 3.2 Contiguous facility rules

- No hard level reloads between places.
- Scan memory and acoustic state carry forward.
- Doors, power, and blockages are **in-world** gates, not “next level” loads.
- Large warehouse / annex geometry may exist as part of the same facility, not a separate campaign select.

### 3.3 Existing prototype mapping

`build_awakening_room()` is the live Threshold / Wake start (`load_world()`). Legacy `build_small_level()` remains for acoustics benches. Expand the facility from the Awakening Room outward — not a level-select fantasy.

---

## 4. Systems that serve the horror

### 4.1 The Eye (scanner integrity)

- HUD: **eye** that bleeds as integrity drops (replace or restyle current SCANNER bar).
- Player never sees a physical scanner mesh or inventory item.
- Integrity drains from **use** (and later from story pressure), not only from a repair minigame.
- Repair remains diegetic (blackout / “close the eye” / proximity ritual) — still no gadget model.
- At critical bleed: visual/audio unreliability (existing health FX + new eye-specific lies).

### 4.2 Cost of seeing

- Scanning is necessary to navigate (blind wireframe fantasy).
- Scanning also **costs** integrity and can attract or agitate the presence.
- Over-scanning a “wrong” room can leave permanent scan-memory scars (ghost edges that shouldn’t be there).

### 4.3 Presence (avoidance, not combat)

- No combat loop in v1 fantasy.
- Presence is primarily **audio** (footfalls, breathing, radio that isn’t radio) with rare wireframe wrongness.
- Player avoids death by pathing, timing scans, and not lingering in presence territory with a loud eye.
- Death = eye fails / presence catches you / darkness — not a boss fight.

### 4.4 Escape as death

- All EXIT signage, drafts, and radio cues are honest about *direction* and dishonest about *outcome*.
- Ending threshold should be reachable only after enough environmental story that a careful player can feel dread *before* crossing — but the game does not soft-lock them with a “are you sure?” prompt.
- Post-ending: credits / silence / return that does not undo the meaning (no cheerful New Game+ wink).

### 4.5 Story objects (environmental only)

Examples (authoring palette, not a checklist to spam):

- Wall labels and room codes that change between scans.
- Paper / clipboard text revealed only when scanned (wireframe glyphs → readable when close).
- Old radio loops and one-shot fragments (already in audio stack).
- Door plaques, employee photos with faces scratched in geometry, schedules that include *your* shift with no name.
- Acoustic “documents”: overheard PA, dead channels, structure-borne thumps underfoot.

**Out of scope for story delivery:** journal UI, quest log, voiced protagonist monologue, long unskippable cutscenes.

---

## 5. What already exists (reuse)

| Pillar | Status |
|--------|--------|
| Blind scan + persistent memory | Implemented |
| Contiguous facility authoring | Implemented (`LevelBuilder`, `world_authoring.md`) |
| Door interaction | Implemented (expand types later) |
| Vitals + low-health audio/visual FX | Implemented (wire real damage sources) |
| Scanner repair minigame | Implemented (retheme as eye ritual) |
| Spatial acoustics / lying audio hooks | Implemented (phantoms, obstruction, radio) |
| Objectives / death / ending / presence AI | **Missing — this spec’s game layer** |
| Eye HUD art | HUD art credit exists; eye bleed UX not yet the scanner metaphor |

---

## 6. Build order (plan fully, ship part-by-part)

**Principle:** Finish the design bible (this doc + map sketch + ending beat) before large content. Implement **place by place** on one map, never as separate “levels.”

Suggested implementation waves (after this spec is approved):

1. **Eye metaphor + integrity drain on scan** — HUD, cost, death when eye dies.  
2. **Presence v0** — audio stalker + simple avoidance fail state.  
3. **Expand facility spine** — grow small bunker into wake → ops → records without load screens.  
4. **Environmental story props** — scan-readable text, radio fragments, wrong continuity.  
5. **Exit threshold + ending** — escape = death; credits tone.  
6. **Polish dread** — scan-memory lies, presence density by place, repair as bargaining.

A separate implementation plan should be written after this design is approved (do not start coding the game layer until then).

---

## 7. Explicit non-goals (near-term)

- Jump-scare director / scripted sting library as primary scare.
- Combat, guns, inventory management.
- Multiplayer.
- Level-select campaign or chapter select screen.
- Full lore wiki / journal.
- Explaining the eye’s ontology in plain text (“you are a camera” / “you are dead”) before the ending earns it.

---

## 8. Success criteria

A player who finishes should:

1. Have believed escape was the correct goal the entire time.  
2. Feel the ending as inevitable in hindsight, not random.  
3. Still feel uneasy hours later — about the eye, the presence, or what “out” meant.  
4. Never have needed a jump scare to stay tense.

---

## 9. Open items (to refine in follow-up design passes)

These are **not** blockers for approving the core fantasy; they need answers before deep content authoring:

- Exact nature of the presence (human? former staff? the facility?).
- Exact mechanism of “escape = death” (airlock, surface, “release,” medical).
- How much the player’s identity is revealed vs left blank.
- Map scale (hours of play) and whether backtracking is required.
- Whether multiple endings exist (spec currently assumes **one** lethal escape ending; optional “refuse the exit” ending can be discussed later).

---

## 10. Decision log

| Date | Decision |
|------|----------|
| 2026-07-09 | Deep psychological horror; story-heavy; no jump-scare focus. |
| 2026-07-09 | Player wakes with no knowledge of who/why (C). |
| 2026-07-09 | Scanner = muscle memory + never seen; HUD is bleeding eye (A+D). |
| 2026-07-09 | Goal = escape; ending reframes escape as death (A+D). |
| 2026-07-09 | Threat = environment + presence + cost of seeing (A+B+C). |
| 2026-07-09 | Story = environmental only (A). |
| 2026-07-09 | Structure = facility as autopsy; minimize level-based framing. |
| 2026-07-09 | Plan the whole game first; build place-by-place on one map. |
| 2026-07-09 | Awakening Room: west door, north window, center-left pillar, real sunken bay + stairs; Space=jump / Tab=scan. |
