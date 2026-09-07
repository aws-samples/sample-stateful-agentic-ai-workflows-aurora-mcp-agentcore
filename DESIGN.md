---
name: Meridian
description: A capable travel concierge with calm airline service clarity.
colors:
  service-navy: "#10213c"
  service-text: "#f4f7ff"
  service-muted: "#afc1db"
  service-active: "#23436a"
  service-hover: "#1a3458"
  light-ground: "#f3f5f8"
  light-surface: "#ffffff"
  light-soft: "#edf1f7"
  light-ink: "#152640"
  light-muted: "#566780"
  light-line: "#dce3ed"
  light-accent: "#2457a7"
  light-action: "#2358ae"
  dark-ground: "#101c30"
  dark-surface: "#16243a"
  dark-soft: "#1c2c44"
  dark-ink: "#edf2fa"
  dark-muted: "#aab9ce"
  dark-line: "#304059"
  dark-accent: "#a0c1ff"
  dark-action: "#3268c6"
  on-action: "#f8faff"
  light-caution: "#80551d"
  light-caution-bg: "#fbf2e4"
  dark-caution: "#edc68a"
  dark-caution-bg: "#352d24"
typography:
  display:
    fontFamily: '"Geist Variable", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "calc(30px * var(--mc-type-scale))"
    fontWeight: 520
    lineHeight: 1.25
    letterSpacing: "-0.035em"
  headline:
    fontFamily: '"Geist Variable", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "calc(27px * var(--mc-type-scale))"
    fontWeight: 520
    lineHeight: 1.2
    letterSpacing: "-0.03em"
  body:
    fontFamily: '"Geist Variable", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "calc(14px * var(--mc-type-scale))"
    lineHeight: 1.6
  label:
    fontFamily: '"Geist Variable", ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "calc(12px * var(--mc-type-scale))"
  evidence-label:
    fontSize: "max(16px, calc(14px * var(--mc-type-scale)))"
  mono:
    fontFamily: '"Geist Mono Variable", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace'
rounded:
  small: "4px"
  chip: "5px"
  action: "6px"
  navigation: "7px"
  compact-surface: "8px"
  panel: "10px"
  card: "12px"
spacing:
  tight: "8px"
  compact: "12px"
  standard: "16px"
  inset: "18px"
  panel: "24px"
  workspace: "30px"
components:
  button-primary:
    backgroundColor: "{colors.light-action}"
    textColor: "{colors.on-action}"
    rounded: "{rounded.action}"
    padding: "10px 14px"
  button-primary-dark:
    backgroundColor: "{colors.dark-action}"
    textColor: "{colors.on-action}"
    rounded: "{rounded.action}"
    padding: "10px 14px"
  button-text:
    backgroundColor: "transparent"
    textColor: "{colors.light-accent}"
    padding: "9px 0"
  button-text-dark:
    backgroundColor: "transparent"
    textColor: "{colors.dark-accent}"
    padding: "9px 0"
  composer:
    backgroundColor: "{colors.light-soft}"
    textColor: "{colors.light-ink}"
    rounded: "{rounded.panel}"
    padding: "8px 10px 8px 16px"
  composer-dark:
    backgroundColor: "{colors.dark-soft}"
    textColor: "{colors.dark-ink}"
    rounded: "{rounded.panel}"
    padding: "8px 10px 8px 16px"
  navigation:
    backgroundColor: "{colors.service-navy}"
    textColor: "{colors.service-muted}"
    rounded: "{rounded.navigation}"
  navigation-active:
    backgroundColor: "{colors.service-active}"
    textColor: "{colors.service-text}"
    rounded: "{rounded.navigation}"
  prompt-chip:
    backgroundColor: "transparent"
    textColor: "{colors.light-ink}"
    rounded: "{rounded.chip}"
    padding: "6px 10px"
  card:
    backgroundColor: "{colors.light-surface}"
    textColor: "{colors.light-ink}"
    rounded: "{rounded.card}"
  card-dark:
    backgroundColor: "{colors.dark-surface}"
    textColor: "{colors.dark-ink}"
    rounded: "{rounded.card}"
---

# Design System: Meridian

## Overview

**Creative North Star: "Capable airline service"**

Meridian pairs calm navy navigation with cool silver and white task surfaces, useful destination photography, and clear travel details. The user's United Airlines and Delta Airlines references establish the level of service polish. Meridian keeps its own name, assets, and palette.

The interface is a working travel application: readable facts, a useful next action, and the traveler's returned preferences establish confidence. The dark theme carries the same hierarchy and component language. Presenter evidence uses this shared system while making observed state and missing evidence explicit.

**Key Characteristics:**

- Navy service navigation and blue actions anchor both themes.
- Geist variable type supports readable travel details and presentation use.
- Real destination photography gives travel content its visual identity.
- Tonal surfaces, fine borders, and modest corners organize the work.
- State labels distinguish saved progress, loading, missing evidence, and reported disruption.

The implemented source of visual authority is `meridian/frontend/src/showcase/airlineConcierge.css`, with the inherited Geist font stacks in `meridianShowcase.css`. The current airline direction supersedes the earlier generated design seed. This is a record of the built system, not an approved image composition or a specification for every legacy showcase component.

## Colors

The palette combines deep service navy, cool neutral surfaces, clear blue actions, and warm caution states. Frontmatter contains the canonical values; the live theme uses the matching `--mc-*` CSS properties.

### Primary

- **Service Navy** remains on navigation in both themes; Service Text and Service Muted establish its text hierarchy. Service Active and Service Hover communicate navigation state.
- **Action Blue** fills primary actions. **Accent Blue** identifies links, selected tabs, focus, and useful icons. These roles have separate light and dark values so text accents stay legible without forcing an overly bright action fill.
- **On Action** is the shared light foreground for filled primary controls.

Official service artwork keeps its supplied colors. In particular, the user-supplied Amazon Aurora tile retains its original magenta (`#C925D1`); this asset color is not an additional Meridian action color.

### Secondary

- **Warm Caution** and **Caution Background** identify reported cancellation, recovery concerns, and request errors. Pair the color with explicit text and an appropriate icon.

### Neutral

- **Ground** is the outer application canvas. **Surface** contains active tasks and travel details; **Soft** separates subordinate information within them.
- **Ink** is the reading color; **Muted** carries secondary information. **Line** separates adjacent areas and outlines fields without heavy framing.
- Light mode uses cool silver and white. Dark mode replaces each neutral role with its navy counterpart while preserving the same information hierarchy.

**The Theme Pair Rule.** Use the semantic theme roles together. Keep the navy service navigation stable while changing the canvas, reading colors, borders, accents, and caution treatments as a complete set.

## Typography

**Display and body font:** Geist Variable, with the platform sans fallback stack in frontmatter. **Evidence identifiers:** Geist Mono Variable with its monospace fallback stack. The root enables the Geist `ss01`, `ss03`, and `cv11` features.

The hierarchy is compact and composed rather than oversized. Medium variable weights give headings definition; normal sentence case and open line spacing keep the traveler application approachable. Monetary amounts use tabular numerals.

### Hierarchy

- **Display:** the shared section heading role in frontmatter; the discovery welcome uses a nearby size with slightly tighter tracking. Evidence can use a larger two-line outcome heading when supported by the observed state.
- **Headline:** featured trip names and similarly prominent travel content. Compact card titles use smaller medium-weight text so destination, duration, provider, and price can be scanned together.
- **Body:** conversation and explanatory content. Conversation lines are limited to (75ch), with generous line height (1.7); other body copy generally uses (1.6).
- **Label:** supporting navigation, facts, and action text. Labels carry meaningful names rather than decorative hierarchy.
- **Evidence label:** worker metadata, store rows, and fact labels have a minimum rendered size (16px). Identifiers wrap where necessary instead of pushing a narrow surface wider.

**The Projector Rule.** The current desktop scale is (1.18); at widths up to (860px) the compact scale is (1.04). Preserve the evidence-label minimum when adjusting the scale. Small incidental captions in older code are not a target for new information that the audience must read.

## Layout

The desktop shell uses a service sidebar, a central task area, and a contextual right rail. Expanded navigation is (220px), collapsed navigation is (76px), and the discovery brief rail is (306px). The header is (72px) tall. Above (1240px), the shell stays within the viewport: the conversation and activity own their scroll areas and the composer stays visible. Growing replies follow the latest turn only while the reader is at the bottom; reading history must preserve the reader's position.

At widths from (861px) through (1240px), discovery uses icon navigation and a narrower brief rail (268px). At widths up to (860px), it becomes a single column: compact navy navigation, a horizontally scrollable surface selector, content, composer, then traveler brief. The compact navigation bar is (66px), and the surface selector is (54px). The selected surface is scrolled into view.

Reusable workspace spacing follows the frontmatter's observed increments. Wide content commonly uses the workspace inset; compact content uses the inset spacing. Use explicit gaps and shared alignment rather than adding framing around every paragraph.

Trip collections combine a prominent photograph and trip summary with smaller supporting options. The featured image and copy stack on compact screens; supporting items become short horizontal rows. Recovery itinerary and evidence layouts also collapse into a single column. Keep long identifiers and metadata within the available width.

## Elevation & Depth

The main surfaces are flat at rest. Surface tone, fine borders, and the fixed navy navigation establish depth. Navigation, primary controls, recovery decision cards, and teaching surfaces explicitly remove decorative shadows. Destination photographs provide depth where they communicate the trip itself.

**The Task Surface Rule.** Use a tonal change or a fine dividing line to separate adjacent work. Add visual weight in proportion to the task's importance, with the primary action and the useful travel content leading.

The concierge's recovery card separates the aircraft photograph from its heading and action. Copy sits below the image on the Soft surface with the shared Ink, Muted, and Accent roles. Tall desktop audience layouts give the image extra height. Overlay behavior inherited by dialogs is not a new shadow vocabulary for ordinary cards.

## Shapes

Controls use modest corners, moving from small chips and action buttons to softly rounded panels and trip cards. Use the role-specific radius in frontmatter rather than a universal pill shape. Circular save buttons and traveler portraits retain their familiar affordances. Surface tabs are flat, with a bottom border for selection.

The itinerary preview has a ticket-derived silhouette: two information areas, a dashed division, and restrained notch details on wide screens. This shape communicates the travel context; text must still identify the document as an itinerary preview and state that it is invalid for boarding. The compact form stacks the areas and removes the notches.

Icons use Lucide and the existing travel icon components, with consistent line-based geometry and deliberate alignment beside their labels. Official service marks are the explicit exception: Amazon Aurora uses `public/brand/aurora.svg`, copied from the user-supplied `aws-amazon-aurora.svg`. The shared `AuroraIcon` preserves its colors and enforces a minimum size (20px); `ServiceMark` remains the common service-asset renderer. Keep state symbols such as checks, locks, and chevrons on the ordinary icon system. Use the Meridian M monogram from `public/brand/meridian-mark.svg` for the primary brand, message avatars, and favicon. Reserve aircraft icons for flight information. Use actual bundled destination photography with a cover crop, subject to the asset provenance in `meridian/frontend/public/travel`.

## Components

### Buttons

Primary buttons use Action Blue and On Action with modest corners. Text actions use Accent Blue and a directional icon when useful. Keep the action named: exploring a trip, opening saved items, retrying a request, and rereading evidence are separate operations. Navigation and the mobile recovery primary action retain a minimum height (44px); the featured-trip primary uses its observed minimum (42px).

Buttons in the traveler workspace, brief, shell header, and sidebar receive an accent focus outline (2px) with an offset (4px). Disabled traveler controls reduce opacity and indicate waiting; a disabled send control uses the Line and Muted colors. Save controls expose their toggled state through `aria-pressed` and a filled heart, in addition to their color change.

### Inputs / Fields

The conversational composer is a soft tonal field with a fine border and panel corners. Its input remains flexible while the square send control stays aligned at the end. Placeholder and text contrast use the Muted and Ink roles. Compact input text remains at least the browser-friendly base (16px) before scaling. Suggested prompts sit above the field; optional query settings remain adjacent to the task they affect.

### Chips

Prompt chips are small rounded rectangles with a fine border and plain text. Action settings use quieter text controls, with an accent state when active. Chips represent usable choices or settings; do not make ordinary prose look interactive.

### Cards / Containers

Trip cards put photography, a clear name, useful catalog facts, price, and a next action together. The featured trip uses a filled action; supporting trips use a text action. A soft strip can explain a preference match; caution uses the warm state pair. Save controls sit over the image with a stable light fill in both themes.

The traveler brief uses portrait, departure context, aligned definition lists, remembered preferences, and a saved-item count. A lack of returned data remains explicit. Compact screens receive the same brief in the document flow.

### Navigation

Navy service navigation provides consistent orientation. Its active item uses a slightly lighter fill; the hover state retains the same calm color family. The surface selector uses an accent underline and plain labels rather than enclosed pills. Compact service navigation uses the existing useful icon actions with accessible names.

Capability phase navigation uses intrinsic-width horizontal items and scrolls the active phase into view. Preserve each readable label rather than forcing five labels into equal narrow columns.

### Capability disclosure

Each of the five capability phases presents one takeaway heading and one explanatory sentence. The shared native disclosure, **Architecture & evidence**, starts closed and contains the request path, technology, and evidence to inspect. Its summary is a text action with a chevron, a minimum height (44px), and visible focus; the chevron turns when open. Compact layouts stack the path and definition lists. Avoid repeating the same capability explanation in several adjacent containers.

### Recovery and evidence

Reported disruption uses a caution notice and itinerary preview, followed by an operable recovery task. Recovery status must follow the derived workflow state. The preview does not invent an airline-issued flight or seat.

On wide screens, the recovery action and flight photograph share equal columns and matching heights. The itinerary endpoints balance around a centered route, with the same content inset as the recovery card. Compact screens stack the action and photograph; tablet layouts place evidence below the workspace.

Evidence uses readable worker cards, persisted-state rows, and labeled facts. Identifiers may use the mono family; surrounding explanation stays in Geist sans. Empty and loading states remain visible, and controls allow a return to recovery or a reread of the actual records. Missing evidence is not replaced by invented success.

### Motion and feedback

Travel actions use brief color transitions (160ms ease-out) only when reduced motion is not requested. Loading dots use an opacity animation (1.4s ease-in-out, alternating), under the same preference guard. Motion communicates response and waiting rather than establishing a decorative theme. Errors retain the conversation and offer retry; empty recommendations remain empty and explain a useful next step.

## Do's and Don'ts

### Do:

- **Do** use the shared semantic color pairs in both themes.
- **Do** make travel facts, the next action, and returned preferences easy to read.
- **Do** use the bundled destination photographs and established icon language.
- **Do** preserve visible keyboard focus, explicit action names, and compact layout flow.
- **Do** distinguish reported disruption, saved workflow state, observed evidence, and missing data.
- **Do** retain projector scaling and the minimum size for essential evidence metadata.

### Don't:

- **Don't** turn the airline references into invented partnerships or airline-issued documents.
- **Don't** make sample catalog content look like a live airline booking or ticket feed.
- **Don't** replace missing flight, seat, preference, or execution data with fabricated facts.
- **Don't** promote decorative glows, heavy framing, or unrelated legacy palettes into new surfaces.
- **Don't** make technical implementation detail compete with the traveler's immediate task.

### Presentation controls

Windowed preparation uses a compact toolbar above the app: audience-layout preview, projector readability, room check, and Present fullscreen. The toolbar and its open disclosures disappear in fullscreen. It is visible to anyone viewing the windowed screen; it is not a private speaker-notes channel. Keep all preparation copy inside that toolbar.

The audience layout hides the service sidebar and moves the Meridian mark into the shared header. The four surface tabs and contextual evidence remain available. At desktop sizes, projector readability uses a (1.3) type scale, essential evidence labels of at least (18px), and higher-contrast secondary colors in both themes. Windowed preview retains the toolbar; fullscreen uses the recovered height for the active workspace. Exiting fullscreen restores the presenter's preview and readability choices without remounting the conversation.

### Shared typography and hold evidence

All four surfaces use Geist Variable for headings, body copy, and controls. Shared headings use weight 520 and -0.035em tracking; labels use 550. Geist Mono remains reserved for technical identifiers and SQL. The legacy serif token aliases the shared sans family.

The recovery checkpoint and package hold are separate facts. A checkpoint before inventory verification explicitly says no inventory is held yet. The hold receipt shows booking creation, expiry, and the calculated duration. System evidence reads these timestamps from Aurora and anchors its countdown to the browser receive time, so changing tabs cannot restart it. A replacement-created hold is distinguished from one that existed before replacement. Expired holds remain visible as records and no longer count against package capacity.

### Workflow handoff and session close

Phase 5 teaches the checkpoint with a three-step path and a single action:
Run to checkpoint, then Continue at recovery desk. It does not repeat the
boarding pass or the traveler’s decision dashboard. The desk carries the same
conversation and shortlist; opening it sends no new request. Resume and verify
continues the saved work. The hold receipt sits near the itinerary; detailed
checkpoint progress belongs in the ladder trace and System evidence.

Direct 12-hour holds show hours, minutes, and seconds in trip details, the
Concierge travel brief, and the desk. They are distinct from the workflow’s
15-minute hold. Receipts retain their expiry across view changes within the
app session. Refreshing clears local direct-hold receipts, while Aurora keeps
the booking. The direct clock uses device time and names that limitation.

System evidence ends with Session takeaways, leading to an audience-facing
close and Q&A. Keep four permanent tabs. The close is an intentional detour,
with a return to the same evidence or Concierge. Large Geist typography,
three plain-language patterns, and a repository link carry the ending.
Preparation controls stay hidden in fullscreen; the closing content remains.
