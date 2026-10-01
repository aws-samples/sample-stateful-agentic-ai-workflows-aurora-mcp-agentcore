---
name: Meridian
description: Native-app restraint for a truthful travel concierge and its operational evidence.
colors:
  action: "#0071e3"
  action-hover: "#0064cc"
  on-action: "#ffffff"
  ground: "#000000"
  surface: "#252527"
  surface-2: "#363638"
  separator: "#3f3f41"
  control-line: "#848486"
  label: "#ffffff"
  label-2: "#c8c8ca"
  label-3: "#a4a4a6"
  blue: "#74acfe"
  green: "#6ee750"
  yellow: "#f3c300"
  red: "#fe897a"
  cyan: "#64d2ff"
  studio-surface: "#151517"
  studio-surface-2: "#252527"
  studio-separator: "#303032"
  light-ground: "#ededef"
  light-surface: "#ffffff"
  light-surface-2: "#ededef"
  light-separator: "#d9d9db"
  light-control-line: "#838385"
  light-label: "#1d1d1f"
  light-label-2: "#4d4d4f"
  light-label-3: "#676769"
  light-blue: "#005dca"
  light-green: "#207101"
  light-yellow: "#785f03"
  light-red: "#c60213"
  light-cyan: "#056897"
typography:
  studio-display:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "clamp(30px, 3vw, 44px)"
    fontWeight: 600
    lineHeight: 1.12
    letterSpacing: "-0.02em"
  studio-title:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "calc(28px * var(--mds-type-scale))"
    fontWeight: 600
    lineHeight: 1.18
    letterSpacing: "-0.02em"
  studio-body:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "calc(17px * var(--mds-type-scale))"
    fontWeight: 400
    lineHeight: 1.5
  studio-label:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "max(14px, calc(15px * var(--mds-type-scale)))"
    fontWeight: 400
    lineHeight: 1.4
  headline:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "calc(15px * var(--mds-type-scale))"
    fontWeight: 600
    lineHeight: 1.467
  body:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "calc(15px * var(--mds-type-scale))"
    fontWeight: 400
    lineHeight: 1.467
  title-3:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "calc(18px * var(--mds-type-scale))"
    fontWeight: 600
    lineHeight: 1.333
  title-2:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "calc(22px * var(--mds-type-scale))"
    fontWeight: 700
    lineHeight: 1.273
  title-1:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "calc(28px * var(--mds-type-scale))"
    fontWeight: 700
    lineHeight: 1.214
  large-title:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "calc(36px * var(--mds-type-scale))"
    fontWeight: 700
    lineHeight: 1.167
  footnote:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "max(12px, calc(13px * var(--mds-type-scale)))"
    fontWeight: 400
    lineHeight: 1.385
  caption:
    fontFamily: "system-ui, -apple-system, sans-serif"
    fontSize: "max(12px, calc(12px * var(--mds-type-scale)))"
    fontWeight: 400
    lineHeight: 1.333
rounded:
  s: "6px"
  m: "10px"
  l: "14px"
  full: "999px"
spacing:
  compact: "8px"
  control-gap: "12px"
  content-gap: "16px"
  row-gap: "20px"
  section-gap: "24px"
  pane-inset: "28px"
  desktop-inset: "32px"
components:
  button-primary:
    backgroundColor: "{colors.action}"
    textColor: "{colors.on-action}"
    typography: "{typography.headline}"
    rounded: "{rounded.full}"
    padding: "10px 20px"
  button-primary-hover:
    backgroundColor: "{colors.action-hover}"
    textColor: "{colors.on-action}"
  studio-starter:
    backgroundColor: "{colors.studio-surface-2}"
    textColor: "{colors.label}"
    typography: "{typography.studio-label}"
    rounded: "{rounded.l}"
    padding: "16px"
  studio-chip:
    textColor: "{colors.label-2}"
    typography: "{typography.footnote}"
    rounded: "{rounded.s}"
    padding: "4px 0"
  studio-composer:
    backgroundColor: "{colors.studio-surface-2}"
    textColor: "{colors.label}"
    typography: "{typography.studio-body}"
    rounded: "{rounded.full}"
    padding: "8px 10px 8px 20px"
  studio-card:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.label}"
    rounded: "{rounded.l}"
---

# Design System: Meridian

## Overview

**Creative North Star: "Destination Studio"**

Meridian uses the user's selected Apple-like native-app restraint: black canvas, white labels, cool neutral supporting copy, system typography and rounded blue actions. Destination imagery and clear decisions lead the Concierge; quiet surfaces keep the conversation readable. This explicit system-font choice is intentional and takes precedence over a generic display-font restriction.

This record describes the current `/showcase` source, not a universal layout replacement. Shared tokens and blue/white action controls apply across the five surfaces. The Destination Studio split belongs to Concierge; Capability ladder, Recovery desk and System evidence retain their operational layouts. Solution briefing has its own reading layout and exclusive numbered disclosures.

**Key Characteristics:**
- Strong neutral contrast with one blue action family.
- Semantic status color paired with words and icons.
- Destination-led Concierge, task-specific operational surfaces.
- Progressive disclosure and truthful runtime states.

Source of truth: `meridian/frontend/src/showcase/tokens.css`, `conciergeStudio.css`, `actionControls.css`, `airlineConcierge.css`, `solutionBriefing.css`, and their React components. `PRODUCT.md` records the presentation context. The rendered application is the source of truth for component behavior.

## Colors

A monochrome foundation supports bright blue actions, quieter blue links and distinct state colors.

### Primary

Capability numbers, the selected phase and the Architecture & evidence disclosure
use action blue with white labels. Capability controls and the disclosure share
the same full-radius capsule and 10px by 20px padding as Present fullscreen and
Explore this trip; inactive capabilities keep a quiet filled surface. Number
markers are circular. White on action blue measures 4.69:1 (WCAG AA).
- **Action blue / action-hover / on-action:** filled decisions with white labels. The same action palette is retained in light mode.
- **Blue:** tint for links, top-level navigation, focus and active progress; it is separate from the saturated button fill.
- **Green / yellow / red:** observed success; caution or pending attention; error, denied or canceled. Cyan remains available for existing informational evidence accents.

### Neutral
- **Ground / surface / surface-2:** canvas, panels and nested interactive surfaces.
- **Label / label-2 / label-3:** primary, secondary and tertiary text.
- **Separator / control-line:** quiet structure versus visibly actionable boundaries.
- **Studio variants:** the dark Concierge alone lowers panel and separator tones. They do not replace the shared operational surface values.
- **Light variants:** replace the corresponding semantic roles on the root light theme. Photo text regions retain a dark theme over the image scrim.

**The Action and Evidence Rule.** Blue identifies a next action or selection; a filled button does not establish success. Keep unknown and unrun neutral. State colors require the existing labels and icons.

## Typography

Display and body use the system family recorded in the frontmatter. Technical code uses `ui-monospace, Menlo, monospace`. The shared type ramp is caption, footnote, body/headline, title-3, title-2, title-1 and large-title. Concierge adds studio-display, studio-title, studio-body and studio-label. The display clamp belongs to its welcome; the studio title belongs to destination and conversation headings.

The shared scale is `1`, becomes `0.9` at viewport widths up to `860px`, and is `1.2` under projector readability. Caption/footnote retain a minimum legible size. The Concierge display uses its clamp rather than this multiplier. Architecture SVG text keeps scale `1` because its viewBox scales the diagram as a unit. Briefing headings use the shared size roles with medium weight (`500`); explanatory text uses normal weight. Do not infer a new weight scale from these component overrides.

**The Projector Hierarchy Rule.** Preserve primary action, destination and result hierarchy when enlarging type. Supporting text must not displace controls or clip evidence. The Briefing introduction is limited to `65ch`; deeper disclosure bodies use up to `78ch`.

## Layout

The spacing entries summarize repeated literal CSS values; the implementation does not define a global spacing-variable scale.

Concierge uses a `72px` header and viewport height minus the presenter controls. Its sidebar track collapses to zero, leaving destination content and a conversation column sized `clamp(360px, 32vw, 540px)`. Once a conversation exists, the conversation column becomes `clamp(400px, 40vw, 680px)`. The destination pane scrolls independently; messages scroll above context and the anchored composer. Desktop workspace padding is `28px 32px 24px`; conversation padding is `32px 28px 20px`.

At `1200px` insets compact. At `1050px` and below, Concierge stacks into normal document flow, navigation scrolls horizontally, and a direct jump reaches the composer. At `540px` and below, side insets become `18px`, supporting trip thumbnails are `100px` wide, and the featured image track has a `390px` minimum. Image height is allowed to grow with overlaid content; do not reintroduce overlapping copy. Short desktop viewports hide the compact context strip while the full travel brief remains available.

Solution briefing has a centered `1760px` maximum reading region, desktop padding `32px 40px 40px`, and mobile padding `24px 20px 32px` below `640px`. It presents four exclusive numbered native disclosures: architecture, data preparation, live walkthrough and boundary verification. Architecture starts open and may be collapsed; selecting another topic closes it. Supporting service bands stay inside a separate closed disclosure. This arrangement is specific to the Briefing.

**The Surface Ownership Rule.** Share tokens and actions across pages; carry over layout only when the receiving task needs it. The Concierge split is not the template for recovery or evidence.

## Elevation & Depth

Resting content relies on tonal planes, neutral separators and image clipping. The catalog image uses the source's bottom black legibility scrim, not a decorative gradient. Floating overlays use `--mds-shadow-float`; exact dark/light values are recorded in the sidecar. There is no hard offset shadow language.

The Concierge column expansion runs for `260ms` with `cubic-bezier(.22, 1, .36, 1)` only when reduced motion is not requested. Preserve stable reading after expansion. Existing hover treatments are component-specific rather than a universal lift.

## Shapes

Small, medium and large radii serve controls, thumbnails and featured imagery/starter rows. Full radius serves primary actions and the composer; avatar, save and send controls are circular. Supporting trip rows are flat and separated by rules. Preserve the difference between rounded interactive controls and unboxed content rows.

## Components

### Buttons

Shared actions are capsule-shaped blue buttons with white headline labels, a minimum height of `44px`, and the primary padding in the frontmatter. Hover uses action-hover. The featured Concierge action has its own larger `48px` minimum and `12px 24px` padding. Navigation, disclosure, selection chips and secondary text actions remain quieter. Trace utility buttons use 44px square targets with 10px corners, centered 18px icons, blue/white enabled states and neutral disabled states. Focus uses a tint outline (`2px`, `3px` offset for the shared button rule); local disclosure/input focus uses `2px` offsets. Preserve disabled states rather than applying an enabled hover to them.

### Chips

Concierge quick actions are quiet text with supporting icons, compact spacing, small corners and tint for selected state. They use a `36px` minimum in Studio; they are not blue primary buttons. Starter rows are larger neutral rounded controls, with a `60px` minimum and a neutral border that strengthens on hover.

### Cards / Containers

One featured catalog image leads Concierge with its title, facts, price and action over a legibility scrim. Supporting trips use compact rows and medium-radius thumbnails. Image crops remain within their own grid tracks. Save is a separately named toggle with a visible pressed state; clipping the image must not clip that control. Use live catalog copy, pricing and availability, including genuine empty/loading/unavailable states.

### Inputs / Fields

The Concierge composer is a rounded neutral field containing a textarea and circular blue send control. The textarea flexes within its available width and scrolls vertically for longer content. Focus is visible on the containing field. The travel brief is a keyboard-accessible, independently scrollable disclosure; opening it hides the redundant compact context summary. Long assistant content wraps; code and tables scroll horizontally within the message.

### Navigation

The shared header uses text tabs with medium-weight body labels, a tint underline for the active tab, and lighter text on hover. Concierge keeps the Meridian wordmark and circular profile control in its slim header; it hides the separate graphic mark there. Other surfaces retain the supplied mark where already used. Narrow headers offer horizontally scrollable navigation rather than compressing every label.

### Numbered briefing disclosure

Native `details` elements share the `solution-briefing` name for exclusive expansion. Number, heading, supporting sentence and chevron are one operable summary. Summaries retain visible keyboard focus and a minimum `44px` target; deeper reference summaries use `56px`. The diagram is an explanation of the system, not live transaction proof.

## Do's and Don'ts

### Do:
- **Do** preserve semantic controls, accessible names, keyboard navigation, visible focus, readable contrast and reduced motion.
- **Do** bind result and state labels to actual runtime evidence, and require explicit confirmation before a courtesy hold.
- **Do** preserve dark photo-text regions in light mode and maintain white labels on blue actions.
- **Do** keep shipping-image provenance with `meridian/frontend/public/travel/README.md`. Catalog images and the fictional portrait are owner-supplied generated JPEG assets; do not describe them as documentary photography.
- **Do** keep the approved mockup as composition reference while using the actual catalog and responsive app behavior.

### Don't:
- **Don't** use decorative colored panels or borders to replace the approved quiet Concierge surfaces. Functional focus, selection, and semantic evidence treatments remain valid.
- **Don't** introduce decorative kickers or eyebrows, glyph characters as icons, or hard offset shadows into this world.
- **Don't** invent availability, activity, bookings, prices or successful agent actions to fill a design.
- **Don't** apply the Concierge split or the Briefing disclosure sequence as a universal layout mandate.
- **Don't** treat this record as deployment proof or a passing visual comparison gate.
