# Solution briefing visual walkthrough - September 20, 2026

The briefing now explains preparation and all five phases in the default reading
flow. The earlier consolidation removed useful teaching detail along with the
extra navigation. This follow-up restores that detail through diagrams while
retaining the five application tabs, one main architecture and three references.

## What changed

- Data preparation uses three visible source-to-store paths: typed package facts,
  descriptions prepared for vector and full-text search, and traveler facts
  protected by identity grants and row-level security.
- All five phases have compact diagrams. SQL and MCP establish the data paths;
  production adds governed tools; workflow adds durable execution and replay.
- Phase 3 shows an illustrative Bali query, Bedrock query embeddings, Aurora's
  semantic and lexical branches, candidate merging by package ID, Bedrock
  reranking, and a small destination photograph from the seeded catalog.
- CloudFront, S3 and App Runner now have official service artwork above the main
  architecture. The phase diagrams reuse the existing AWS SVG assets.
- Geist remains the shared type family. Clearer heading levels, aligned service
  labels, restrained rules and consistent spacing improve the reading hierarchy.
  Projector mode enlarges the supporting phase labels to 18px.
- Both READMEs, their screenshots and the presenter instructions reflect the new
  flow. No nested tabs, diagram switches or additional disclosures were added.

Onward London's preparation rows and travel-result imagery informed the visual
explanation. Meridian keeps its own identity and implementation: its retrieval
merges and deduplicates candidates before reranking; it does not use Onward's
reciprocal-rank fusion. The Bali card has no live score or rank claim. Current
prices, availability and budget checks remain separate tool responsibilities.

## Verification

- ESLint, TypeScript and the production frontend build pass.
- 247 frontend tests pass across 38 files.
- 533 backend and repository-contract tests pass, with 6 skipped and 110 database
  tests deselected. The existing Pydantic settings annotation warning remains.
- All 10 local Playwright accessibility tests pass, including light/dark themes,
  keyboard controls, reduced motion, expanded references, image loading and
  document reflow down to 320px.
- Screenshots cover 1440px light/dark, 900px, 390px, 320px dark and 1920px with
  audience preview and projector readability enabled. No document overflow was
  observed; the phase service labels measure 18px in projector mode.
- The default desktop briefing has 655 visible words and three disclosures.
  It is intentionally longer than the previous condensed pass: 3043px at 1440px
  desktop and 5768px at 390px phone, with preparation and every phase visible.

The detector's single side-border warning concerns the existing neutral connector
in the mobile architecture flow. It remains because it communicates sequence.

The independent Impeccable finish review returned **ship** for the briefing, with
no material visual or clarity findings. It reviewed the supplied screenshots and
source against the existing design and craft rules; it did not independently
rerun the automated tests. The desktop dark and projector overview captures cover
their first viewport, with separate retrieval details; the light desktop and
phone/tablet captures cover the full linear reading flow.

## Release scope

This is a static UI release. The backend image, authorization rules, active Aurora
cluster and retained rollback cluster are unchanged. The diagrams explain the
configured architecture; System evidence remains the source for observed execution.

The existing `MeridianWeb` CDK stack reached `UPDATE_COMPLETE` at 01:33 UTC on
September 21 (September 20 locally). Its reviewed diff changed only the static
site asset. Authenticated verification matched the hosted HTML and all eight
JavaScript/CSS assets byte-for-byte against the production build. Four access
denial checks passed, security headers remained present, and the API reported
healthy with durable Aurora checkpoints. All 10 browser accessibility tests
also passed against the hosted site using live APIs. The earlier 32-contract
workflow rehearsal was not rerun for this UI-only release.

The documentation review recorded the extension in the existing design system;
global tokens and the design sidecar remain unchanged. The product presentation
context now explicitly includes the fifth view, Solution briefing.

Timed human delivery, screen-reader rehearsal and actual room/back-row readability
remain human checks. Browser emulation does not complete those rehearsals.
