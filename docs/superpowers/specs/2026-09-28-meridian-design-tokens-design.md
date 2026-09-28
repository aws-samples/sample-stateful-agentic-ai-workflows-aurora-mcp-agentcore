# Meridian design tokens and Apple-style themes

Date: 2026-09-28
Status: design, approved for planning
Branch: `meridian-design-tokens` (to be created from `main`)

## Goal

Make the Meridian showcase look clean in both themes by replacing its
hard-coded styling with one small set of role variables and fixed scales,
modelled on Apple's system structure: semantic surfaces and labels, a single
tint, a short type ramp, grouping by tone instead of borders. Apple's surface
values are not adopted where they fail Meridian's contrast tests or wash out on
a projector.

The palette was never the problem. Dark is already `#000`/`#fff` and light is
`#fff`/`#111`. The clutter comes from hundreds of one-off values, four grey
temperatures and four variable namespaces, measured below.

## Decisions

| Topic | Decision |
| --- | --- |
| Approach | Full replacement. Old variables are deleted, not aliased. |
| Typeface | System font (`system-ui`, SF Pro on the presenting Mac). Mono is `ui-monospace` (SF Mono). The serif is dropped. |
| Hues | Blue, yellow and green keep the re:Invent 2026 deck hues (`0578FF`, `F3C300`, `6EE750`). Red and cyan use Apple's hues; the deck has neither. Purple is dropped. |
| Themes | Dark and light are peers and pass the same tests. Dark stays the default. |
| Projector preset | Stops overriding colors. Base colors are projector-safe in both themes; the preset only raises the type scale. |
| Labels | All-caps eyebrow labels become sentence case. |
| Merge | Nothing merges to `main` until the last stage, so rehearsals never see a half-converted look. |

## Current state, measured on 2026-09-28

Scope: `frontend/src/showcase/**/*.css` and `frontend/src/index.css`. "Live"
excludes rules whose every selector branch names a class that no TS/TSX file
references.

**Dead CSS.** 164 `mds-`/`mc-` classes appear in CSS and nowhere in `src` or
`e2e` TS/TSX. Rules made only of those classes hold about 2,190 declarations
(`meridianShowcase.css` 747, `recoveryWorkspace.css` 721, `discoveryWorkspace.css`
358, `surfaceSwitch.css` 334, other files 31) and 293 color literals. Spot
checks confirmed `mds-concierge-rail`, `mds-breadcrumb`, `mds-discovery-grid`
and `mds-desktop-nav` are unreferenced; the Concierge was rebuilt in `c5d63b4`
and again under `mc-*`. Three class prefixes are built at runtime
(`mds-brief-phase-`, `mds-service-mark-`, `mds-trip-visual-`) and are treated
as live.

**Live styling.**

| Measure | Value |
| --- | --- |
| Color literal uses / distinct | 964 / 658 |
| Grey temperatures in dark | 4: neutral tokens; cool slate text (`#8fa0b4`, `#afc1db`, 89 uses); warm brown-black fills and gradients (`#17130f`, `rgba(12,9,6,…)`); green-tinted `--proof-*` |
| Light-mode borders | Warm (`#ebe7e1`) and cool (`#e1e7ed`) mixed |
| Variable namespaces | `--mds` 93, `--mc` 20, `--proof` 6, `--app` 4 |
| Type multipliers | `--mds-fs` (1, 1.16, 1.04), `--mc-type-scale` (1.18, 1.3, 1.1, 1.04), `--mds-fs-chrome` (1.08) |
| Rendered font sizes on the laptop | 32 values, 8 to 50px, at the root multipliers (1 and 1.18) |
| Font weights | 30 distinct values, 300 to 900 |
| Letter-spacing values | 24 |
| Border radii | 25 |
| `box-shadow` | 84 literal, 15 via variable, 33 `none` |
| Before dead-code removal | 114 gradient declarations, 27 `backdrop-filter`, 57 `text-transform: uppercase`, 329 border declarations |

**Undefined variable.** `--mds-mono` is referenced six times but never
defined; the defined variable is `--mds-font-mono`. Five uses carry a fallback
and render monospace only because of it. The sixth,
`.mds-checkpoint-thread strong` (`recoveryWorkspace.css:5506`), has none, but
its component `CheckpointedPlanCard` is never rendered, so nothing on screen
is wrong today. The token check's undefined-variable and no-fallback rules
cover this class of bug.

**Unrendered components.** Eight exported components are imported nowhere
outside their own file and tests: `RecoveryRouteMap`, `RecommendationCards`,
`AuroraEvidenceStrip`, `SessionReceipt`, `JourneyPanel`,
`RecoveryGuardrailsCard`, `CheckpointedPlanCard` and `PhaseSelector` (about
800 lines). Their class names keep otherwise dead CSS looking live, so the
dead-CSS figure above is a floor.

**Contrast tests already in force.** `e2e/accessibility.spec.ts:80-107` runs in
both themes, desktop and projector, and reads `--mc-surface`, `--mc-soft`,
`--mc-muted`, `--mc-line` and `--mc-accent` directly:

- secondary text (`muted` on `soft`) at least 7:1
- primary action label on its background at least 7:1, default, hover and focus
- action boundary against `surface` at least 3:1
- field boundary (`line` on `soft`) at least 3:1
- focus indicator (`accent` on `soft`) at least 3:1

Axe runs WCAG 2.2 AA on all five views at 1366, 640 and 320px, and on the
projector preset at 1920, 1280, 960 and 320px.

**Consumers outside the showcase.** `index.css` and `preflight.css:263` read
`--app-*`. `/stage` embeds `BriefingArchitecture` and remaps its variables in
`stage/demo-stage.css:1527` (`.ds-kiosk-architecture` sets `--mds-font` and
five `--mc-*` names). `/stage` and the mockups import Geist, so the Geist
packages stay.

**Deck.** `Meridian-reInvent-2026-dark-v3.pptx` uses Ember Modern and theme
accents `FF6200`, `F3C300`, `FF4D8B`, `B052FF`, `0578FF`, `6EE750`. It embeds
`02-concierge-dark.png` (1444x1387) and `09-business-receipt.png` (2466x1387)
with native crops.

## Design

### Token file

One file, `frontend/src/showcase/tokens.css`, imported from `index.css` so the
loading screen and the app read the same values. `main.tsx` already sets
`<html data-theme>` before the showcase mounts, so tokens are defined on
`:root` (dark) and `:root[data-theme='light']`. All names keep the `--mds-`
prefix. Every current `--mds-*` color variable, and all of `--mc-*`,
`--proof-*` and `--app-*`, are deleted once their consumers move.

`--mds-type-scale` is set only in this file: laptop default, the narrow
viewport value, and the projector value.

### Color roles

| Variable | Dark | Light | Use |
| --- | --- | --- | --- |
| `--mds-ground` | `#000000` | `#ededef` | Page background |
| `--mds-surface` | `#252527` | `#ffffff` | Grouped content: cards, rail sections |
| `--mds-surface-2` | `#363638` | `#ededef` | Inputs, hover, selected rows, inset blocks |
| `--mds-separator` | `#3f3f41` | `#d9d9db` | Decorative row dividers |
| `--mds-control-line` | `#848486` | `#838385` | Input and secondary button edges |
| `--mds-label` | `#ffffff` | `#1d1d1f` | Primary text |
| `--mds-label-2` | `#c8c8ca` | `#4d4d4f` | Secondary text |
| `--mds-label-3` | `#a4a4a6` | `#676769` | Captions, timestamps, placeholders |
| `--mds-blue` | `#74acfe` | `#005dca` | Links, selected tab, focus ring |
| `--mds-tint` | `var(--mds-blue)` | `var(--mds-blue)` | The one accent |
| `--mds-action` | `#92beff` | `#014fae` | Filled primary button |
| `--mds-action-hover` | `#aecfff` | `#00459a` | Its hover state |
| `--mds-on-action` | `#000000` | `#ffffff` | Label on the filled button |
| `--mds-green` | `#6ee750` | `#207101` | Confirmed, succeeded |
| `--mds-yellow` | `#f3c300` | `#785f03` | Memory, pending, caution |
| `--mds-red` | `#fe897a` | `#c60213` | Interruption, failure |
| `--mds-cyan` | `#64d2ff` | `#056897` | Checkpointed, durable state |

Each hue has a `-fill` for badge and banner backgrounds, opaque so layers
never stack: `color-mix(in srgb, var(--mds-<hue>) 16%, var(--mds-surface))` in
dark and `10%` in light. Resolved values:

| Fill | Dark | Light |
| --- | --- | --- |
| blue | `#323b49` | `#e6effa` |
| green | `#31442e` | `#e9f1e6` |
| yellow | `#463e21` | `#f2efe6` |
| red | `#483534` | `#f9e6e7` |
| cyan | `#2f414a` | `#e6f0f5` |

Two gradient tokens survive: `--mds-image-scrim` (a bottom-up black gradient
behind white text on trip photos, the same in both themes) and the loading
skeleton pair `--mds-skeleton` (`var(--mds-surface-2)`) and
`--mds-skeleton-shimmer` (`--mds-label` at 8% over `--mds-surface-2`).
`--mds-scrim` (`rgb(0 0 0 / 0.5)` dark, `rgb(0 0 0 / 0.32)` light) keeps the
drawer backdrop.

**Contrast, measured against the worst-case background `--mds-surface-2`.**

| Pair | Dark | Light | Required |
| --- | --- | --- | --- |
| `label-2` | 7.22 | 7.21 | 7 (test) |
| `label-3` | 4.85 | 4.83 | 4.5 |
| `control-line` | 3.23 | 3.24 | 3 (test) |
| `tint` | 5.21 | 5.25 | 3 (focus test), 4.5 as text |
| `on-action` on `action` | 11.03 | 7.69 | 7 (test) |
| `on-action` on `action-hover` | 13.17 | 9.06 | 7 (test) |
| `action` against `surface` | 8.04 | 7.69 | 3 (test) |
| green / yellow / red / cyan | 7.58 / 7.24 / 5.21 / 7.01 | 5.25 / 5.23 / 5.25 / 5.23 | 4.5 |
| each hue on its own fill | 4.89 to 6.61 | 5.12 to 5.31 | 4.5 |

Surface steps: dark `surface` against `ground` 1.37:1 and `surface-2` against
`surface` 1.27:1; light 1.17:1 each.

**Where this departs from Apple, and why.**

| Apple | Meridian | Reason |
| --- | --- | --- |
| Translucent secondary labels (light `secondaryLabel` on white is 3.44:1) | Opaque `label-2` at 7.2:1 | The secondary-copy test requires 7:1 |
| White on `systemBlue` (4.02:1); white on deck `0578FF` is 4.09:1 | Dark: light blue with black label. Light: darker blue with white | The action-label test requires 7:1 |
| Dark elevation `#000` / `#1C1C1E` (1.23:1) | `#000` / `#252527` (1.37:1) | Projectors lift black and compress small steps |
| Vibrancy and blur materials | None | Translucency turns to mush on a projector |

### Type

Fonts: `--mds-font: system-ui, -apple-system, sans-serif` and
`--mds-font-mono: ui-monospace, Menlo, monospace`. The Geist stylistic-set
`font-feature-settings` and `--mds-serif` are removed.

One multiplier, `--mds-type-scale`: 1 on the laptop, 0.9 below 860px, and
1.1 with projector readability on (today's projector preset raises Concierge
type by the same 1.3 / 1.18 ratio); both are tuned by screenshot. Caption and
footnote keep a 12px floor through `max()`. Each style is a `font` shorthand
token, for example
`--mds-type-body: 400 calc(15px * var(--mds-type-scale))/1.467 var(--mds-font)`,
used as `font: var(--mds-type-body)`. The type tokens are declared on both
`:root` and `.mds-root`, because a custom property's `var()` references are
resolved where it is declared: declaring them only on `:root` would ignore the
projector multiplier set on `.mds-root`.

Laptop sizes are mapped from what renders today: `.mds-desktop-app` always
carries `is-projector`, so `--mds-fs` is 1.16 everywhere except the discovery
workspace (1), `--mds-fs-chrome` is 1.08 and `--mc-type-scale` is 1.18.

| Style | Size / line height | Weight | Replaces |
| --- | --- | --- | --- |
| `caption` | 12 / 16 | 400 | 8 to 12px; nothing renders below 12px |
| `footnote` | 13 / 18 | 400 | 13 to 14px secondary text |
| `body` | 15 / 22 | 400 | 14 to 16px |
| `headline` | 15 / 22 | 600 | Emphasised labels at body size |
| `title-3` | 18 / 24 | 600 | 17 to 20px |
| `title-2` | 22 / 28 | 700 | 21 to 25px |
| `title-1` | 28 / 34 | 700 | 26 to 32px |
| `large-title` | 36 / 42 | 700 | 33px and larger |

Weights: `--mds-weight-regular` 400, `-medium` 500 (buttons, tabs, controls),
`-semibold` 600, `-bold` 700. Old values round to the nearest.

Tracking, starting values to confirm by screenshot: 0 up to 15px (the
default, so no token), `--mds-tracking-title` -0.01em (18 to 22px),
`--mds-tracking-display` -0.02em (28 to 36px).

Eyebrow labels drop `text-transform: uppercase` and use `footnote` at 600 in
`label-2`. Any string authored in capitals in TSX moves to sentence case in the
same stage.

### Shape

| Token | Value | Use | Replaces |
| --- | --- | --- | --- |
| `--mds-radius-s` | 6px | Chips, tags | 2 to 7px |
| `--mds-radius-m` | 10px | Buttons, inputs, list rows, nav items | 8 to 11px |
| `--mds-radius-l` | 14px | Cards, grouped sections | 12 to 18px |
| `--mds-radius-full` | 999px | Pills | 999px |

`0` and `50%` (circles) stay literal. Chat bubble tails combine `l` and `s`.

### Depth

- Borders only for control edges (`--mds-control-line`), row separators
  (`--mds-separator`) and focus. Cards, panels and rails have none and separate
  by `--mds-surface` against `--mds-ground`. The `border-color: transparent`
  overrides in `projectorReadability.css` are deleted.
- Focus everywhere: `outline: 2px solid var(--mds-tint); outline-offset: 2px`,
  replacing box-shadow rings.
- One shadow, `--mds-shadow-float`, for menus, tooltips and popovers only.
  Dark `0 12px 32px rgb(0 0 0 / 0.6), 0 0 0 1px var(--mds-separator)`; light
  `0 12px 32px rgb(0 0 0 / 0.14), 0 0 0 1px var(--mds-separator)`. The 1px ring
  keeps a floating element visible on black, where a shadow alone is not.
- No other gradients, and no `backdrop-filter`. The `@supports not
  (backdrop-filter …)` fallback block goes with them.
- The existing 220ms theme cross-fade stays.

## Non-goals

- Spacing. 49 distinct padding, margin and gap values; a 4px grid would move
  every layout. Separate spec after re:Invent.
- Restyling `/stage` and the mockups. Only the `.ds-kiosk-architecture` remap
  changes, so the embedded briefing diagram keeps its colors. The diagram's
  text takes the system font there too, since its type tokens resolve on
  `:root`.
- Layout, copy (other than uppercase labels), icons and motion.
- Apple's Liquid Glass (iOS 26 and macOS Tahoe) or any translucency.

## Stages

Each stage is one or more commits on `meridian-design-tokens`, verified before
the next begins.

0. **Checks and baseline.**
   - `frontend/scripts/check-design-tokens.mjs`, run from `npm run lint`,
     fails on any of these in showcase CSS:
     - a color literal outside `tokens.css` (`transparent`, `currentColor` and
       `inherit` allowed)
     - `font-size` or `font-weight` not from a token, or a `font` shorthand
       not from a type token
     - `border-radius` other than a radius token, `0` or `50%`
     - `box-shadow` other than `none` or `--mds-shadow-float`
     - any `backdrop-filter`
     - a gradient outside a token
     - a `var(--x)` with no definition in the scanned CSS
     - a fallback on an `--mds-` token, which hid the mono bug

     Unconverted files sit on an exemption list in the script that shrinks each
     stage and is deleted at the end.
   - Screenshot script: 5 views (`briefing`, `concierge`, `ladder`,
     `recovery`, `proof`) x 2 themes x desktop (1440x1000) and projector
     (1920x1080, `present=1`), plus one `/stage` shot: 21 images. It runs
     against one journey kept with `scripts/kill_and_resume_demo.py --keep`,
     with reduced motion and fonts loaded, and masks relative timestamps.
1. **Delete dead code.** First delete the eight unrendered components and the
   tests that exercise only them. Then remove only CSS rules the static scan
   marks dead. A DOM probe across all views, themes and modes, with every
   `<details>` opened, records the classes that actually render; it can prove
   a class live but not dead, so any statically dead class the probe sees is
   kept and investigated. Screenshots must match the stage 0 baseline pixel
   for pixel.
2. **Depth.** Remove decorative gradients, blur, literal shadows and
   decorative borders; add `--mds-shadow-float`, `--mds-image-scrim` and the
   focus outline. This goes before color so about 300 literals are deleted
   rather than converted.
3. **Color.** Add `tokens.css`. Convert every color literal and every old
   color variable in the showcase, `index.css` and `preflight.css`. Delete
   `--mc-*`, `--proof-*`, `--app-*` and the old `--mds-*` colors. Remove the
   color overrides from `projectorReadability.css` and `presentationMode.css`.
   Point `accessibility.spec.ts` at `--mds-surface`, `--mds-surface-2`,
   `--mds-label-2`, `--mds-control-line` and `--mds-tint`, keeping its
   thresholds. Update the `/stage` remap.
4. **Type.** Fonts, the ramp, the single multiplier, weights, tracking and
   sentence-case labels. Delete `--mds-fs`, `--mds-fs-chrome` and
   `--mc-type-scale`.
5. **Radii.**
6. **Captures and docs.**
   - Re-take the nine captures at their recorded dimensions. This needs a
     fresh live run and a new hold, released afterwards with the scoped
     release helper, as in the September 27 pass. Confirm with the presenter
     before creating the hold.
   - Update the capture table in `docs/presentation/reinvent-2026/README.md`
     and the theme and projector sections of `docs/PRESENTER_GUIDE.md`.
   - The deck is not patched. DAT307 is being rebuilt on the RIV26 template
     under its own spec after this work lands, and the new captures feed that
     rebuild. The template's theme colors and fonts match the ones this spec
     draws hues from, so no palette change follows from it.

## Verification

Every stage:

- `npm run lint` (ESLint plus the token check), the TypeScript build,
  `npm run test:run` and `npm run test:accessibility`.
- The 21-image screenshot set compared with the previous stage and reviewed
  by eye. On this project, three CSS bugs were visible only in screenshots.

From stage 4:

- A Playwright pass listing elements that are newly clipped against the
  baseline (`scrollWidth > clientWidth` where overflow is hidden). The font
  change alters text widths.

Done means the exemption list is gone, the token check passes with no
exemptions, no `--mc-`, `--proof-` or `--app-` name remains in `src`, and all
test suites pass in both themes and both modes.

## Risks and open checks

- **Projector.** Whether dark `surface` cards stay distinct from the black
  page on the room projector cannot be checked here. It joins the pending
  back-row legibility check in the deck README.
- **Optical sizes.** Chrome should switch SF Pro between its Text and Display
  cuts through `font-optical-sizing: auto` (moderate confidence). Screenshots
  at 12px and 36px confirm it.
- **Text width.** SF Pro and Geist differ in width, so labels may wrap or
  clip. The clipping pass catches them; fixes are layout changes inside the
  affected rule, not new tokens.
- **Timing.** The event date is not recorded in the repo. Stages 0 to 5 are
  local and reversible; stage 6 creates a demo hold and waits for the
  presenter's go-ahead.
