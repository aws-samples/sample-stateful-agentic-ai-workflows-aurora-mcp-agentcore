# Cursor Prompt — Recreate the Meridian Cinematic Device Showcase

You are Cursor working inside the Meridian app repository. Build a production-quality, slide-ready cinematic device showcase that mimics the provided reference image: a realistic MacBook-style desktop app and a matching smartphone app side by side on a dark reflective keynote stage.

## Objective

Create a new standalone visual route/component for Meridian that looks like a world-class product launch hero image. It should not look like a normal dashboard page. It should look like a polished Apple-style keynote render implemented in HTML/CSS/React.

Target output:
- A 16:9 cinematic slide composition.
- Left: realistic MacBook-style laptop angled toward the viewer.
- Right: realistic iPhone-style mobile device standing beside it.
- Both screens show Meridian, an agentic travel concierge.
- Dark premium UI, teal/cyan accents, warm amber rim light, realistic device reflections, and a glossy studio surface.

## Route / file structure

Add this as a demo/showcase surface, not as the main product UI.

Suggested route:
- `/showcase`
- `/device-showcase`
- or `/slides/meridian-device-showcase`

Suggested component structure:
- `src/showcase/MeridianDeviceShowcase.tsx`
- `src/showcase/components/LaptopFrame.tsx`
- `src/showcase/components/DesktopMeridianApp.tsx`
- `src/showcase/components/PhoneFrame.tsx`
- `src/showcase/components/MobileMeridianApp.tsx`
- `src/showcase/components/TripArt.tsx`
- `src/showcase/meridianShowcase.css` or Tailwind component classes

Do not use external stock images. Use CSS gradients or existing local assets only.

## Reference composition

The final scene should match this layout:

1. Background
   - Deep black/navy cinematic backdrop.
   - Left side has cool blue/cyan glow.
   - Right side has warm amber/orange glow.
   - Subtle vignette.
   - Reflective dark tabletop at the bottom.
   - Realistic device shadows and soft reflections.

2. Laptop
   - Space-gray MacBook-like body.
   - Large 16:10 display with black bezel and small top notch.
   - Slight 3D angle/perspective, not flat.
   - Metallic base below screen with subtle hinge/trackpad impression.
   - Optional tiny port shapes on left side.
   - Laptop should occupy roughly 65–70% of the width.

3. Desktop app screen
   - Dark Meridian UI.
   - Left sidebar with brand `Meridian` and nav items:
     - Concierge active
     - Trips
     - Discover
     - Profile
     - Preferences
     - Messages with small badge
     - Settings
   - Main area:
     - `Good morning, Alex.`
     - `Where would you like to go next?`
     - User bubble:
       `I’m looking for a long weekend in wine country in November. Boutique, walkable towns, great food, and relaxing spa options.`
     - Assistant line:
       `Perfect. I’ve found a few places that match your style.`
     - Three recommendation cards:
       - Willamette Valley, Oregon — Nov 7–10 — From $1,950
       - Napa Valley, California — Nov 14–17 — From $2,450
       - Mendoza, Argentina — Nov 21–24 — From $1,850
     - CTA button: `View more recommendations`
     - Composer: `Ask Meridian anything...`
     - Quick actions: `Add travelers`, `Change dates`, `Add spa`, `Direct flights`
   - Right panel:
     - `Traveler context`
     - Alex Morgan, email, profile, travel style, interests, loyalty programs, recent trips
     - `Meridian activity` panel with live checklist:
       - Understanding your request
       - Searching preference-matched destinations
       - Checking availability & pricing
       - Curating personalized recommendations
       - Optimizing your itinerary

4. Phone
   - Modern iPhone-like device.
   - Dark metal frame, rounded corners, dynamic island, side button.
   - Positioned to the right of the laptop, slightly taller than the laptop base but shorter than the laptop screen.
   - Matching Meridian mobile UI.
   - Header: `Meridian`
   - Profile row: Alex Morgan + View profile
   - Same user prompt bubble.
   - Assistant copy: `Here are a few recommendations I think you’ll love.`
   - Trip cards:
     - Willamette Valley, Oregon, Trending, From $1,950
     - Napa Valley, California, From $2,450
   - Bottom composer and bottom nav.

## Visual design tokens

Use a refined dark palette:

```css
:root {
  --bg-0: #05080d;
  --bg-1: #0a1017;
  --bg-2: #101923;
  --panel: rgba(12, 19, 28, 0.86);
  --panel-2: rgba(18, 28, 40, 0.82);
  --line: rgba(255,255,255,.08);
  --line-2: rgba(255,255,255,.14);
  --ink: #f5f8fb;
  --ink-2: #d7e1ea;
  --muted: #8c9aa8;
  --dim: #607080;
  --cyan: #28d7e6;
  --cyan-2: #65f4ff;
  --amber: #dca15a;
  --gold: #f3c779;
  --green: #35d49c;
  --blue: #5ca8ff;
  --violet: #a68cff;
}
```

## Styling requirements

- Use `aspect-ratio: 16 / 9` for the whole stage.
- Use CSS perspective/transform for device realism.
- Use `radial-gradient` glows for cinematic lighting.
- Use subtle `box-shadow`, `filter: drop-shadow`, and translucent borders.
- Use glassy cards with `rgba()` backgrounds and 1px borders.
- Make UI text sharp and readable enough for a slide.
- Use CSS-generated destination art, not stock photos, unless local assets already exist.
- Keep all copy real and Meridian-specific.
- Do not make it look like a wireframe or toy mockup.
- Avoid emojis in the final UI unless they are tiny and tasteful. Prefer glyphs/icons or CSS shapes.
- Respect `prefers-reduced-motion`.

## Interaction

This can be mostly static. Optional polish:
- Pulse the final activity item.
- Add a subtle glow on the active navigation item.
- Add gentle background grain/noise using CSS.
- Add a `?clean=1` mode that hides any dev labels if you add them.

## Acceptance criteria

1. The route loads without needing backend data.
2. The composition visually matches a premium MacBook + mobile app product render.
3. The word `Meridian` is visible on both desktop and mobile screens.
4. The desktop screen includes sidebar, greeting, chat prompt, recommendation cards, traveler context, and Meridian activity panel.
5. The mobile screen includes profile, prompt, recommendations, composer, and bottom nav.
6. It is slide-safe at 16:9 and looks good at 1920×1080.
7. No external network images or font imports are required.
8. Existing app routes are not broken.
9. Build passes.

## Implementation hint

Use the supplied standalone HTML mockup as the visual source of truth. Port the CSS into React components without changing the composition unless necessary for responsiveness.
