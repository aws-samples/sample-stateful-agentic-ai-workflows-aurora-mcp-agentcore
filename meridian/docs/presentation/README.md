# Meridian re:Invent chalk talk

## Current re:Invent 2026 deck

Use the [current dark PowerPoint v3](reinvent-2026/Meridian-reInvent-2026-dark-v3.pptx),
[complete speaker notes](reinvent-2026/SPEAKER_NOTES.md), and
[capture/validation record](reinvent-2026/README.md). This September 27 deck uses
the supplied RIV26 template, purple AgentCore icons and fresh local-app captures.
It follows Concierge, Capability ladder, Recovery desk and System evidence.
All five phases have separate architecture, running-code, takeaway and live-demo
slides. There are 32 core slides and four hidden Q&A slides, planned for 40 minutes
plus 20 minutes of discussion. Diagrams use numbered on-click builds with no card
fills or colored borders. The production transition and AgentCore platform introduction precede Phase 4.
Takeaways name the services; LangGraph and Cedar logos are included. Dogwood
temporal policies remain optional Q&A material. Package checks pass; see the
validation record for native PowerPoint coverage. Timed rehearsal and physical projector review remain pending.

## Previous Summit-derived deck

- [Editable PowerPoint](Meridian-reInvent-chalk-talk.pptx)
- [Previous PDF export](Meridian-reInvent-chalk-talk.pdf) - September 19 rendering; refresh pending.
- [Searchable slide text and speaker notes](SLIDE_NOTES.md)
- [Presenter runbook](../PRESENTER_RUNBOOK_2026-09-20.md)
- [Readiness and evidence boundaries](../READINESS_2026-09-20.md)

The user selected `DAT301-R-Toronto-Meridian.pptx` as the latest approved presentation. This copy preserves its AWS template, masters, native editable shapes, title and closing artwork. The Toronto original remains unchanged in Downloads/Presentations/2026. Its SHA-256 is `2e23dad9b0a14c6d8dc5ca6f4fc4e123f17f366cde6efb1992a9b7f2c8b32c37`. The repository copy is now the editable source; no private generator or external original is needed to edit or present it.

There are 25 visible slides: 23 core slides for a planned 40-minute presentation, followed by two optional discussion slides. Reserve 20 minutes for discussion and flex. No re:Invent session code was supplied, so the title uses “re:Invent · L300”.

The revision adds workload authorization, Cedar enforcement, RLS, the separation of conversation memory from workflow checkpoints, worker leases, stable hold intent, replay protection, lost-response recovery and independent evidence. It removes the illustrative 500K/day and 10,000x claims, old PostgresSaver path, retrieval booking-write claim, expired June promotion and unrelated workshop QR. Dogwood remains a design discussion, not an enabled feature.

The September 21 story pass adds complete speaker talking points to all 25
slides and the Markdown transcript. SQL now names its per-traveler price unit;
the core fault is a committed hold with a lost reply. Slide 20 discusses workload
limits instead of freezing a deployment-status snapshot into the audience deck.
AgentCore Memory checkpoint support and available Dogwood temporal conditions
are distinguished from Meridian's configured Aurora saver and Cedar policies.
The UI repeats one short engineering callout per phase; deeper references stay
collapsed. The 40-minute budget and 20-minute discussion reserve are unchanged.

The updated PowerPoint and Markdown notes are synchronized. The PDF remains the
September 19 export: native PowerPoint export was blocked by the locked Mac on
September 21. Re-export after unlocking before distributing the PDF. The five
changed visible slides are 5, 17, 20, 22 and 25; the other slide layouts, masters,
media and relationships were preserved. All 25 embedded notes match the transcript.

Local validation for this story pass: frontend type checking, lint and build;
19 focused unit tests; and 12 browser checks covering both themes, projector
layouts, mobile reflow, contrast and keyboard navigation. This pass did not rerun
AWS fault injection or establish a timed human rehearsal.

The two embedded screenshots were captured during the September 19 local-build review against live AWS. The Recovery image is an ordinary pause/resume, not the separate SIGKILL proof. There is no new recording. Older captures elsewhere retain their original dates. The native PowerPoint PDF export used the local “Best for printing” option; it is a visual handout, not a certified tagged accessible PDF. Use the Markdown transcript for searchable text and speaker notes.

Update native shapes and notes in PowerPoint, then export all 25 slides to `Meridian-reInvent-chalk-talk.pdf`. Keep the transcript and runbook consistent. Recheck slide titles, reading order, alt text, text wrapping, fonts, and projector readability after edits. Actual screen-reader use and physical back-row readability still need human validation.
