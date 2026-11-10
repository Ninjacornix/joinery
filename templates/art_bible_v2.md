# Art Bible — {{title}}
<!-- joinery-bible: 2 -->

> One per game project, at `<project>/art/bible.md`. Written by you, the master (Journeyman can interview you and draft it). Journeyman checks every change in this project against it.
> Keep it short and concrete. "Gritty" is a mood; "palette below, roughness 0.6–0.9" is a rule.
> Sections marked (checked) are measured automatically. Sections marked _Open_ can wait: Journeyman suggests them when the work reaches them, and never fills them in without asking you.

## 1. Vision
What the game feels like, and what it must never feel like, in 2–4 sentences.

{{vision}}

## 2. Pillars
Three mood words, each with one line saying what it means *in this game*, so it can be checked:
> e.g. **Heavy**: doors take 0.6 s to swing; nothing bounces. **Worn**: every edge is chipped; no surface is new. **Hushed**: music only in safe rooms.

{{pillars}}

## 3. References
The centre of the bible: your own material, each item saying exactly what to take and what to leave. Journeyman cites these by id ("per R3, …"). No images pulled from the web.

### 3a. Reference board
| # | Reference | Kind | Take this | Not that | File |
|---|---|---|---|---|---|

> Add one row per reference. Kind is one of `game` `film` `place` `photo` `sketch` `board` `painting` `music`. File is optional and lives under `art/reference/`.
> e.g. `| R1 | Hob (game) | game | chunky silhouettes, flat lighting | its saturated greens | |`
> e.g. `| R2 | my photo of the quarry at dusk | photo | warm stone against a cold sky | the haze | art/reference/quarry_dusk.jpg |`
> e.g. `| R3 | board: dusk | board | the three-value lighting | the lens flares | art/reference/dusk_board.kra |`

{{references}}

### 3b. Your own boards
- Boards and sketches live in `art/reference/` only: Krita mood boards (`.kra`) you make or ask Journeyman to make in Krita, photos you took and scans of your sketches.
- Photos and scans are recorded as your import (`human-import`, licence "own work, reference only"), and only when you add them yourself. Nothing is fetched from the web.
- `art/reference/` is kept out of the game: Godot does not import it, no export contains it, and no checked rule looks at it. References can come before this bible is approved; they still need a `joinery.lock` entry.

### 3c. Never look like
Anti-references: what this game must not be mistaken for, and why.
> e.g. - "glossy mobile-game UI": fights the hand-made feel. - "photo-real foliage": breaks the low-poly shapes.

## 4. Camera and framing
Projection (perspective or orthographic), FOV or ortho size, distance and height, default pitch, where the player sits on screen, framing rules (thirds, headroom), camera motion (lag, largest shake and how long), safe area %, aspect ratios supported.
> e.g. Perspective, FOV 70°, 4.5 m behind and 1.6 m up, pitch −15°; player on the lower third; follow lag 0.12 s; shake ≤ 0.15 m for 0.25 s; safe area 90 %; 16:9 and 16:10.

{{camera}}

## 5. Shape language
A primary shape per role, with examples from this game, so a silhouette says what a thing is.
> e.g.
> | Element | Shape | Example in this game | Why |
> |---|---|---|---|
> | Friendly | round | the innkeeper's belly and hat | reads safe at a glance |
> | Hostile | angular | wolf ears and spines | reads as danger before colour does |
> | Structure | blocky | keep walls, crates | stable, man-made |
> Silhouette test: every character and interactable is readable as a black shape 64 px tall. Proportions: heads 1/5 of body height; hands oversized by 20 %.

{{shape_language}}

## 6. Visual style
- Overall look: {{look}}
- Detail placement: where detail goes (characters, interactables) and where it stays simple (background, floors).

## 7. Palette (checked)
| Role | Hex | Use |
|---|---|---|
| Primary | #…… | |
| Secondary | #…… | |
| Accent (interactables only) | #…… | |
| Shadow tint | #…… | |
| UI base | #…… | |
| UI text | #…… | |
| Danger | #…… | |
| Safe | #…… | |

- Palette, in your words: {{palette}}
- Value structure: darkest and lightest values allowed (e.g. nothing darker than #1a1a1a, nothing lighter than #f0f0f0).
- Saturation ceiling outside accents: e.g. 60 %.
- Hex values go into `[palette.roles]` below too, so images, UI contrast and colour-blind safety are checked against them.

## 8. Lighting recipes
Named setups Journeyman reuses, so every level is lit from the same few recipes.
> e.g.
> | Name | Key colour | Key angle | Key:fill | Ambient | Fog | Sky | Godot notes |
> |---|---|---|---|---|---|---|---|
> | Dusk exterior | 3200 K | 15° elevation, from the west | 4:1 | 0.25, blue-grey | depth fog from 40 m | gradient, dark top | `DirectionalLight3D` energy 1.2, shadows on; tonemap Filmic, exposure 1.0 |
> | Torch interior | #ffb060 | point lights at 2.2 m | 6:1 | 0.08 | none | none | `OmniLight3D` range 6 m, energy 1.5; SSAO on |

{{lighting}}

## 9. Materials and texel density (checked)
Roughness range, metallic only on real metal, albedo bounds (no pure black or white), texel density per asset class, texture size limits, power of two, texture naming suffixes.
> e.g. Roughness 0.4–0.95; metallic 0 or 1, only on metal named so; albedo luminance 0.03–0.92; 256 px/m ± 20 % for props, 128 px/m for terrain; textures ≤ 2048 px, power of two; `_albedo`, `_normal`, `_orm`.

{{materials}}

## 10. 3D rules (checked)
Units, reference heights, triangle budgets, bone budgets, pivots, applied scale, naming.
> e.g. Metres, +Y up; player 1.8 m, door 2.1 m; hero ≤ 15 000 triangles, prop ≤ 3 000; ≤ 80 deform bones; pivots bottom-centre, doors at the hinge; scale applied; `Part_Material_Variant` (e.g. `Coat_Cloth_Dark`), bones per Rigify.

{{models}}

## 11. Animation feel
Snappy or floaty (one sentence and a number from 1 floaty to 5 snappy), frame rate (3D 30/60 fps; sprites "on 2s" at 12 fps), holds, anticipation and overshoot, easing defaults, and a timing table.
> e.g.
> | Action | Anticipation | Action | Recovery | Notes |
> |---|---|---|---|---|
> | Jump | 3 f crouch | 6 f rise | 4 f land squash | squash 10 % on landing |
> | Door | 3 f unlatch | 0.6 s swing | 4 f settle | overshoot 3° |

{{animation}}

## 12. VFX
Technique (particles, flipbooks painted in Krita or GIMP, shaders), shapes, colours (accents only?), lifetimes, particle budget per effect, screen-space limits, flash limits (no more than 3 flashes per second).
> e.g. GPUParticles3D with flat quads; accent colours only; lifetimes 0.2–1.0 s; ≤ 64 particles per effect, ≤ 512 on screen; no full-screen flashes.

{{vfx}}

## 13. 2D and sprites (checked)
Base resolution, pixel scale, colours per sprite, outline rules, no sub-pixel rotation.
> e.g. 320 × 180 base, integer scale; ≤ 32 colours per sheet; 1 px dark outline on characters; rotation in 90° steps only.

{{sprites}}

## 14. UI look and motion
Style (flat, skeuomorphic, diegetic), fonts and their licence (a font file is an asset: you add it yourself and it is recorded as your import; Journeyman never downloads one), sizes at 1080p, line weight, corner radius, focus state, motion durations (ms) and easing, UI sounds.
> e.g. Flat panels, 2 px lines, 8 px radius; Atkinson Hyperlegible (OFL) 24 px body, 32 px headings; focus = 3 px accent outline; 120 ms ease-out; soft click from a Surge XT patch.

{{ui}}

## 15. Writing tone
Voice, person (you/we), reading level, longest UI sentence, humour, banned words, capitalisation, room for translation (+30 % length; no text baked into images).
> e.g. Dry and warm, second person, plain words (age 12 reading level); UI lines ≤ 8 words; sentence case; never "epic", "awesome".

{{writing}}

## 16. Audio
- Overall: {{sound}}
- SFX character: e.g. "dry, close, punchy; no cartoon boings".
- Music: tempo range (BPM), keys and modes, instruments or Surge XT / SuperCollider patches, loop length (seamless, 60–120 s).
- Loudness and format (checked): music −16 LUFS, ambience −22 LUFS, peaks ≤ −1 dBTP; SFX as WAV, music as OGG.

## 17. Accessibility (checked)
Which palette roles must stay distinguishable for colour-blind players, smallest text, minimum contrast, subtitles, remappable controls, reduced motion and screen shake toggles, flash limits.
> e.g. Danger and safe stay apart for protan, deutan and tritan vision; text ≥ 18 px at 1080p; contrast ≥ 4.5:1; subtitles on by default; every action remappable; shake slider; ≤ 3 flashes per second.

{{accessibility}}

## 18. Checked rules
Measured on every changed asset before you review it. Change the numbers here;
an empty map or a commented line means "not checked". Folders in `exclude` are
never checked.

```toml
{{checked_rules}}
```

## 19. Never (hard bans)
- e.g. lens flares, chromatic aberration, neon outside UI, stock "whoosh" transitions …

{{bans}}

## 20. Allowed human imports
- Sources you allow (your own work, bought packs, CC0 libraries) and their licences.
- You add these files yourself. Journeyman never downloads assets and never uses generative AI to make or change them.
- Default: none.
- Always allowed: your own reference material in `art/reference/` (3b), recorded as your import.
