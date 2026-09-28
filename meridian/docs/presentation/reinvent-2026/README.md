# Meridian - re:Invent 2026

[Editable dark PowerPoint](Meridian-reInvent-2026-dark-v3.pptx) | [Speaker notes](SPEAKER_NOTES.md)

Updated September 27, 2026. `Meridian-reInvent-2026-dark-v3.pptx` is the current deliverable; earlier copies are retained because one was open for review. Built from the supplied official `RIV26_SpeakerTemplate_20260831 (2).potx`. The Toronto Meridian deck preserves the two-friends opening; recent Mosaic and multi-tenant AgentCore decks in Downloads informed the numbered diagrams and on-click builds. Original decks and template were not modified.

## Story and pacing

Start with the finished Concierge, open the Capability ladder, move to Recovery desk, and finish with System evidence. All five phase names and demo prompts follow the current app. Each phase has four separate slides: architecture, code highlights from the running path, architectural takeaway, and one live-demo checkpoint.

| Phase | Architecture | Code | Takeaway | Live demo | Time window |
| --- | --- | --- | --- | --- | --- |
| SQL | 4 | 5 | 6 | 7 | 3-6 min |
| MCP | 8 | 9 | 10 | 11 | 6-9 min |
| Retrieval | 12 | 13 | 14 | 15 | 9-12 min |
| Production | 18 | 19 | 20 | 21 | 12-20 min |
| Workflow | 22 | 23 | 24 | 25 | 20-28 min |

32 core slides, followed by four hidden Q&A/reference slides (33-36). The main path is still planned for 40 minutes, with 20 minutes of discussion and flex. Slides 26-28 continue checkpoint 5 with response-loss reasoning, transaction replay and Aurora receipt readback. They are not additional independent live-demo checkpoints. Each takeaway is a short pause, not a new lecture. Timings are estimates; the timed human rehearsal remains pending.

Embedded speaker notes and `SPEAKER_NOTES.md` include exact prompts, click cues, source symbols, proof boundaries and cuts. Use this folder's slide order rather than the older Summit deck. The five demo links target the local dark app; select the phase shown on the slide after opening the Capability ladder.

## Design

Every slide has a black background. Architecture objects have no card fill or colored top stripes. AgentCore services use thin neutral gray outlines with black interiors; other numbered flows sit directly on black. Connectors are straight. Icons retain their official service colors, including the supplied purple AgentCore variants. The official re:Invent layouts, artwork and all 19 embedded font files remain. Diagrams, labels, connectors and code are editable.

Slide 2 restores “We built Meridian!” with the two-friends story on the left and a much larger native app-image crop on the right. Aurora's grounded search, durable workflow state and business outcomes frame the current architecture. The fictional growth story is not presented as a measured capacity claim.

The Capability ladder, all five architecture diagrams and response-loss sequence use real PowerPoint entrance animations: 700 ms fades that reveal each logical step and its connecting arrow together. No arrow wipes remain. Every step waits for a click; slides do not advance on a timer. The title, notes and closing preserve the original template.

No re:Invent session code was supplied. The title uses `CHALK TALK | L300`; confirm the final event title, session code and speaker information before submission.

## Production and policy additions

Slides 16-17 introduce the transition beyond a developer laptop and the selected AgentCore building blocks before Phase 4. Slide 18 groups Runtime, Gateway, Memory and Policy within a labeled logical boundary; this is not a network-topology diagram. The five architectural takeaways now name their services. Phase 5 includes the supplied LangGraph logo. The Policy takeaway includes the official white Cedar wordmark.

Hidden slide 33 contrasts Cedar request checks, AgentCore temporal policies written in Dogwood, and Aurora transaction checks. It is optional Q&A material, not another core demo. Meridian does not enable temporal policies. The notes explain authenticated approval binding, session scope, one-time use and target-side validation.

## Captures and evidence

Nine original dark app captures are retained from September 27 EDT (September 28 UTC). The deck embeds the Concierge and business receipt captures, with native PowerPoint crops. Other screenshots remain available for dated fallback. Dimensions below reflect the actual captured viewports. No new business transaction or fault injection was executed during this deck-editing pass.

| Capture | Dimensions | Purpose |
| --- | --- | --- |
| `01-solution-briefing.png` | 2466 x 1387 | Solution briefing |
| `02-concierge-dark.png` | 1444 x 1387 | Finished Concierge; slide 2 |
| `03-sql-grounded.png` | 2466 x 1387 | SQL result; checkpoint 1 fallback |
| `04-retrieval-results.png` | 2466 x 1387 | Retrieval result; checkpoint 3 fallback |
| `05-recovery-start.png` | 2466 x 1387 | Recovery entry state |
| `06-recovery-paused.png` | 2466 x 1387 | Saved shortlist; checkpoint 5 fallback |
| `07-recovery-held.png` | 2466 x 1387 | Successful resumed hold |
| `08-system-evidence.png` | 2466 x 1387 | Checkpoint and execution readback |
| `09-business-receipt.png` | 2466 x 1387 | Business receipt; slide 28 |

The dark capture run used journey `jrn_762519f62448` and thread `phase5-5d0c4869-12ad-492c-9e38-0bda542135f5`. Both attempts used worker `worker-f660ff69`: this proves ordinary pause/resume, not worker replacement. The recorded hold was `hold_64d3e8e4d070`, request `hrq_64d3e8e4d070`, for two fictional travelers and $3,898. It was created at 00:46:12 UTC with expiry 01:01:12 UTC on September 28 (September 27 EDT). After capture, the scoped release helper removed that one owned demo hold. The screenshot remains historical evidence; it is not a currently active reservation.

No flight seats, supplier bookings or payments were created. The dedicated lost-response and process-kill helpers were not rerun for this deck; notes describe their intended demonstration and require honest live/source/dated-evidence labeling. No new claim of hosted parity, full regression coverage, timed human rehearsal or physical room readiness is made.

## Validation

- PASS: Package/schema checks against the supplied template; all internal relationships resolve.
- PASS: 36 black slides; four hidden Q&A slides; all five phases have architecture, code, takeaway and exactly one live-demo checkpoint.
- PASS: Embedded notes match the Markdown transcript and manifest. No middle-dot separators in authored slide text or notes.
- PASS: All 19 original font payloads retained. All diagram connectors are straight, animation targets exist, and slide timing is click-driven.
- PASS: Full-deck thumbnail layout review, with embedded-font substitution treated as a renderer limitation.
- Native PowerPoint review: see `VALIDATION.json` for the final reviewed slides and animation evidence.
- PENDING: Timed 40-minute human rehearsal and actual projector/back-row legibility.

Earlier app verification is retained separately from this deck pass: 14 focused browser checks, TypeScript and a production build passed after the dark-mode/presenter repairs. No new claim of full regression coverage or hosted deployment is made here.

No PDF was requested or exported. The older PDF in the parent directory is not a rendering of this deck.

## Presenter references

Use the [presenter guide](../../PRESENTER_GUIDE.md), [runbook](../../PRESENTER_RUNBOOK_2026-09-20.md) and [code walkthrough](../../CODE_WALKTHROUGH.md) for operations. The new slide notes cite the actual source symbols, including `TurnToolTrace._pin_arguments`, and preserve the distinction between ordinary reload/resume, process replacement and injected response loss.

Capability references retained in optional notes: [AgentCore Memory LangGraph integration](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-integrate-lang.html) and [AgentCore Policy](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html). Meridian's configured choices and enabled policies are described separately from available AWS capabilities. See also [AgentCore platform](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html) and [temporal policies / Dogwood](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-temporal.html). The transparent white Cedar logo comes from [the Cedar project](https://cedarpolicy.com/); the LangGraph logo comes from the supplied Downloads asset.
