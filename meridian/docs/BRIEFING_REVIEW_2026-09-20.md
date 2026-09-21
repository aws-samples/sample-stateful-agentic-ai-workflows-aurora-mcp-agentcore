# Solution briefing review - September 20, 2026

Historical consolidation pass. The later [visual walkthrough review](BRIEFING_VISUALS_2026-09-20.md)
restores visible preparation and five phase diagrams following presenter feedback.
The word counts and page heights below describe this earlier pass, not the current UI.

The briefing now presents the architecture first, then explains governance and
recovery, with three expandable technical references. The five application
surfaces remain available. This review covers the briefing, its shell behavior,
and the shared kiosk architecture component.

## Findings and changes

| Severity | Finding and impact | Resolution |
| --- | --- | --- |
| P1 | Five app tabs, six section links and four diagram modes required readers to understand three navigation levels. | Removed both briefing navigation levels; one linear reading flow. |
| P1 | Selecting a focus mode replaced the architecture with prose, hiding the central explanation. | Architecture stays visible; distinct Runtime and FastAPI paths share governed tools. |
| P2 | The 820px minimum diagram width required horizontal scrolling on phones. | A semantic vertical flow replaces the SVG in narrow containers, with equivalent service and state boundaries. |
| P2 | Ten disclosures, repeated phase summaries and repeated service descriptions fragmented the technical reference. | Three disclosures: data and phases, tool contracts and Cedar, recovery and evidence. |
| P2 | Traveler controls consumed width in a reading view; diagram labels were small. | Hide the sidebar only in the briefing, retain the brand and main navigation, enlarge diagram labels. |
| P1 | Expanded-reference testing found a new inline link differentiated only by color (WCAG 1.4.1). | Add an explicit underline; verified in both themes. |

No P0 blockers were found. All findings above are addressed. The common cause
was duplicated explanation spread across multiple navigation structures.

## Before and after

Measured in Chromium, with references closed, at identical viewport sizes:

| Measure | Before | After |
| --- | ---: | ---: |
| Briefing section navigation buttons | 6 | 0 |
| Diagram mode buttons | 4 | 0 |
| Technical disclosures | 10 | 3 |
| Visible words, 1440px desktop | 816 | 335 |
| Briefing height, 1440px desktop | 3505px | 1748px |
| Briefing height, 390px phone | 5999px | 2561px |

The default desktop text is 59% shorter and the page is 50% shorter. Content
needed for implementation review remains available: all four MCP tool names,
three configured Cedar statements, workload and traveler authorization, data
preparation, five phases, three failure windows, and observability boundaries.
Dogwood remains explicitly an assessed extension, linked to its source document.

## Technical audit

Implementation integrity: **pass**. The page uses Meridian's existing type,
semantic theme tokens, official AWS icons and native keyboard-accessible
disclosures. The diagram distinguishes governed calls from direct Data API
access; conversation memory remains separate from workflow checkpoints.
No backend behavior, authorization rule, or database binding changed.

| Dimension | Score / 4 | Evidence and limits |
| --- | ---: | --- |
| Accessibility | 3 | Keyboard disclosure and handoff checks, accessible SVG description, zero automated WCAG A/AA findings in tested states. Human screen-reader rehearsal remains separate. |
| Performance | 3 | Fewer controls, no new dependency or network request, removed diagram mode state and obsolete content/styles. Production build passes; no new device performance benchmark. |
| Responsive behavior | 4 | Desktop, tablet and phone review; expanded content at 320px without document overflow. Shared kiosk diagram also checked. |
| Theming | 4 | Light and dark screenshots, semantic colors, automated contrast checks. |
| Implementation integrity | 4 | One coherent reading flow, preserved technical contracts, shared diagram and reusable native details. |
| **Total** | **18 / 20** | **Excellent within the reviewed scope; not a full WCAG certification.** |

The Impeccable detector reported one side-border warning on the mobile diagram.
Inspection confirmed a neutral connector joining architecture steps, not a
decorative card accent. It is retained for its diagram meaning.

## Validation

- ESLint and TypeScript checks pass.
- 246 frontend tests pass across 38 files.
- 533 backend and repository-contract tests pass (6 skipped, 110 database
  tests deselected), including the prohibition on tracked deployment addresses.
- Production frontend build passes.
- All 10 Playwright accessibility tests pass: five surfaces in light/dark at
  1366/640/320px; keyboard settings and reduced motion; expanded briefing
  references and both surface handoffs at 1440/900/320px in both themes.
- Visual checks at 1440, 900, 390 and 320px, plus 1920px with projector
  readability; shared kiosk architecture at 1440px.
- README screenshot replaced with the actual September 20 briefing.

## Hosted release

The existing `MeridianWeb` CDK stack reached `UPDATE_COMPLETE` at 00:56 UTC on
September 21 (September 20 locally). The reviewed diff changed only the static
site asset. The App Runner image, active `meridian-encrypted` cluster and
original rollback cluster were retained.

Authenticated verification matched `index.html` and all eight JavaScript/CSS
assets byte-for-byte against the local build. Four access-denial checks passed,
security headers were present, and the API reported healthy with durable Aurora
checkpoints. All 10 browser accessibility tests also passed against the hosted
site with live APIs. This UI release did not rerun the earlier 32-contract
workflow rehearsal; those results remain separately dated in the release report.

Open `/showcase?view=briefing` on the configured hosted site. Deployment addresses
remain in ignored local release records, as required by the repository's
published-endpoint contract.

The architecture explains the design, not live execution. Timed human delivery,
screen-reader use and actual room/back-row readability remain the presenter
rehearsal described in the existing release follow-up.
