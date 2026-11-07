# Art Bible — {{title}}

> One per game project, at `<project>/art/bible.md`. Written by you, the master (Journeyman can interview you and draft it). Journeyman checks every change in this project against it.
> Keep it short and concrete. "Gritty" is a mood; "palette below, roughness 0.6–0.9" is a rule.
> Fields marked (checked) are enforced automatically; the rest guide Journeyman's choices.

## 1. Vision (2–4 sentences)
What the game feels like, and what it must never feel like.

{{vision}}

## 2. References (your own words, no images pulled from the web)
- Games, films or places whose *look* you mean, and what exactly to take from each:
  - e.g. "<game>: the chunky silhouettes and flat lighting, not its colours"

{{references}}

## 3. Visual style
- Overall look: {{look}}
- Shape language: (round & friendly / angular & hostile / blocky & industrial) …
- Detail level: where detail goes (characters, interactables) vs where it stays simple (background) …
- Lighting mood: time of day, contrast, colour temperature …

## 4. Palette (checked)
| Role | Hex | Notes |
|---|---|---|
| Primary | #…… | |
| Secondary | #…… | |
| Accent (interactables only) | #…… | |
| Shadow tint | #…… | |
| UI base | #…… | |
- Palette, in your words: {{palette}}
- Max colour distance (ΔE) from the palette for surface colours: 12
- Saturation ceiling outside accents: …

## 5. 3D rules (checked)
- Units: metres; player height: 1.8 m; door height: 2.1 m
- Triangle budgets: hero character ≤ …, NPC ≤ …, prop small ≤ …, prop large ≤ …
- Texel density: … px/m (± 20 %)
- Pivots: characters/props bottom-centre; doors at hinge
- Naming: `Part_Material_Variant` (e.g. `Coat_Cloth_Dark`); bones per rigify
- Materials: roughness range …–…, metallic only on real metal, no pure black or white albedo

## 6. 2D / UI rules (checked where possible)
- UI style: flat / skeuomorphic / diegetic …
- Fonts: … (licence: …)
- Line weight: … px at 1080p; corner radius: …
- Pixel art (if used): resolution …, palette size ≤ …, no sub-pixel rotation

## 7. Sound palette
- Overall: {{sound}}
- SFX character: e.g. "dry, close, punchy; no cartoon boings"
- Music: tempo range … BPM, keys/modes …, instruments/patches …, loops (seamless, 60–120 s)
- Loudness (checked): music −16 LUFS, ambience −22 LUFS, SFX peaks ≤ −1 dBTP

## 8. Never (hard bans)
- e.g. lens flares, chromatic aberration, neon outside UI, stock "whoosh" transitions …

{{bans}}

## 9. Allowed human imports
- Sources you allow (your own work, bought packs, CC0 libraries) and their licences.
- Default: none.
