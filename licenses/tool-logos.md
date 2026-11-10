# Tool logos

The Joinery tool rail and change cards show each creative app's official logo, in its own
colours, so an app is recognised at a glance. The files live in
`godot_addon/joinery/icons/apps/`. They are editor interface, not game assets, and they are
not covered by the add-on's MIT licence: each keeps the licence below, and every name and
logo is a trademark of its owner, used only to refer to that application.

Every file was taken from the project's own repository or website (nothing generated, nothing
from third-party icon sites), checked for scripts, external references and embedded images
(none), then optimised: editor metadata, unused definitions and ids removed, numbers rounded,
and a 20 × 20 size set on the original square viewBox. Colours and shapes are unchanged. The
hashes below are also in `icons/apps/sources.json`, which `tests/test_tool_logos.py` checks.

| File | App | Source (pinned) | Source SHA-256 | Licence and terms |
|---|---|---|---|---|
| `godot.svg` | Godot Engine | [misc/logo/icon.svg](https://github.com/godotengine/godot/blob/4.7.2-stable/misc/logo/icon.svg), tag `4.7.2-stable` (`ed1daf0b`) | `1443ae974842b81765d5b50e6a729ebbf41eba2e9e515ab0919f2ea5c8b8f475` | CC BY 4.0, © 2017 Andrea Calabró (`misc/logo/LICENSE.txt`). |
| `krita.svg` | Krita | [krita/pics/branding/default/sc-apps-krita.svgz](https://invent.kde.org/graphics/krita/-/blob/0ad7895407b7de242b5b9438ea2c3c979507e6d2/krita/pics/branding/default/sc-apps-krita.svgz), commit `0ad78954` (2026-11-10), decompressed | `2aa7e4a604b50a68f707289735bd4215c63c5f60d979a04492b92b8ba3e76347` (`.svgz`) | GPL-3.0-or-later (the repository licence; the icon build script is SPDX GPL-3.0-or-later). No separate logo policy is published on krita.org. |
| `gimp.svg` | GIMP | [images/logo/gimp-logo-shadow.svg](https://gitlab.gnome.org/GNOME/gimp-data/-/blob/bb251717687ec37a622763c4fa003a3c28461796/images/logo/gimp-logo-shadow.svg) in `GNOME/gimp-data`, commit `bb251717` (2026-11-09) | `04f1dccbcd4cdc8560c54fba6223d2656f915144158f07f6230a318c8105bcdb` | CC BY-SA 4.0 (`images/logo/README.md`). The shadow variant is the one GIMP's own guidelines name for unknown background colours. Reuse is welcomed as long as nobody pretends to be the GIMP team. |
| `inkscape.svg` | Inkscape | [share/branding/inkscape-flat.svg](https://gitlab.com/inkscape/inkscape/-/blob/7cd0c014d2f19d17da7d3417907b2a7486348df4/share/branding/inkscape-flat.svg), commit `7cd0c014` (2026-11-05) | `424b54a148228f68e5cc8947118dad9f7992361ca19b2fe2b5083fd6ff1b9bf0` | CC BY-SA 3.0 US, Andrew Michael Fitzsimon. Registered trademark of the Software Freedom Conservancy; the [Inkscape trademark policy](https://inkscape.org/about/branding/) allows use without permission to refer to the Inkscape software. |
| `inkscape-dark.svg` | Inkscape, on dark backgrounds | [inkscape-flat-w-transp-white.svg](https://media.inkscape.org/media/resources/file/inkscape-flat-w-transp-white.svg), the official "Flat Logo 1 Color Variant – White" linked from [inkscape.org/about/branding](https://inkscape.org/about/branding/), fetched 2026-11-10 | `009b1322edbf97573ad3276a5aca60ed85b5988b2033e344b03dba34cdaadf3d` | As above. The branding page names this variant for dark backgrounds; the rail uses it in the dark theme. |
| `material-maker.svg` | Material Maker | [MaterialMakerLG.icon/Assets/mm.svg](https://github.com/RodZill4/material-maker/blob/7d6daae3c74c09720afe4138ce09699ab3591ce1/MaterialMakerLG.icon/Assets/mm.svg), commit `7d6daae3` | `1fc5614861fb259840a50c5284810e9764c3fd5f0c8004667fba3774c30c9f5a` | MIT, © 2018-present Rodolphe Suescun and contributors (`LICENSE.md`). |
| `surge-xt.svg` | Surge XT | [resources/assets/Logo/SVG/surge_icon.svg](https://github.com/surge-synthesizer/surge/blob/d307b472cd5e986baf61f9db0a4f173183aba3f9/resources/assets/Logo/SVG/surge_icon.svg), commit `d307b472` (identical to the Linux app icon) | `0193bfeb28923ea0e6fd9eb9f85b81158ef0e972aed2e0d462f84fd73ec002f8` | GPL-3.0 (the repository licence). |
| `supercollider.svg` | SuperCollider | [icons/sc_logo.svg](https://github.com/supercollider/supercollider/blob/9bdaa413525ea8e88480d2f15a87bfcd73c1df96/icons/sc_logo.svg), commit `9bdaa413` | `7625fe90fd89f80c56d15ac0d63a7b0f554fa088fca4f25fcae27e163d5788fe` | GPL-3.0 (the repository licence). |

The modified CC BY-SA files are offered under the same licence as their source; the GPL files
are their own complete source.

## Blender: no logo shipped

The [Blender logo guidelines](https://www.blender.org/about/logo/) let third parties use the
logo only to point to Blender or give credit, and only "unaltered … and always complete
(logo + text blender)"; commercial use needs explicit permission. A 20 px rail icon cannot
carry the word mark, so Joinery does not ship the Blender logo. The rail and change cards show
an in-house glyph instead (`godot_addon/joinery/icons/tools/blender.svg`, marked as such in
the file). Using the official logo would need Blender Foundation's permission.

## Apps without a logo

Custom tools and any app not listed here show the monochrome domain glyph, tinted with the
theme, as before.
