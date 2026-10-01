# Meridian

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Meridian depicts a traveler planning a trip with an agentic concierge. Its primary current presentation context is a re:Invent chalk talk for about 90 people, with projected UI, code walkthroughs, slides and five live checkpoints.

## Product Purpose

Show a finished travel product, then explain how SQL, MCP tools, retrieval, managed agent execution and durable workflows make its behavior possible. The prepared story occupies about 40 minutes of a 60-minute session.

## Operating Context

The supported application route is `/showcase`. The story moves through Concierge, Capability ladder, Recovery desk, System evidence and Solution briefing. The approved Destination Studio redesign leads the Concierge surface, with its blue/white action styling shared across the app and a simplified, projector-friendly Solution Briefing.

## Capabilities and Constraints

Preserve live catalog and recommendation data, conversation, traveler context, saved trips, comparison, trip details, confirmation and hold receipts. Preserve the five demo phases and their evidence. Runtime status must describe the actual connection. Empty, loading and unavailable states must be honest. Never substitute invented availability, transactions, prices, activity or successful agent actions for live evidence.

The demo's fictional traveler is Jordan Morgan. A courtesy hold affects demo catalog inventory; it is not a supplier reservation or payment. Business state and conversation memory have distinct roles.

## Brand Commitments

The user requested clean black backgrounds, white labels and blue action buttons. On October 1, 2026 they explicitly selected an Apple-like design system: polished system typography and rounded blue buttons with white text. This supersedes preserving the pale-blue button fill and black button text exactly. Retain Meridian's name and supplied mark. Support light mode without compromising the dark presentation experience. Keep descriptions concise and readable on a projector. Red means error, denied or canceled; green means observed success; amber means caution or pending attention. Unrun and unknown remain neutral. Pair state colors with labels and icons. Prior user-approved typography direction is a system font.

## Evidence on Hand

The existing React application and its tests live under `meridian/frontend`. Source-controlled travel photography, a fictional male traveler portrait and image provenance are available under its public assets. September 29 captures and deck evidence are dated historical proof, not a claim about a current live operation.

## Product Principles

- Capability query pills use a green check and green fill for “Works here,”
  and yellow with a forward arrow for a query solved by the next capability.
  Red remains reserved for actual errors, denials, and cancellations.
- The traveler avatar and Profile action open the inline travel brief with
  remembered preferences, regardless of the selected teaching phase.
- Show the finished traveler experience before the implementation.
- Concierge is the product front door: it recalls authorized traveler context,
  carries the conversation across turns, presents live trips, and leads into a
  saved recovery for review. The capability ladder explains those behaviors;
  its teaching toggles do not disable memory in Concierge.
- Use a calm, direct voice. No stock applause such as “Great!” or “Perfect!”;
  lead with the answer, a relevant remembered detail, or the next decision.
- Let the trip and the next decision lead; make supporting context available as needed.
- Make agent actions and their evidence understandable without exposing invented reasoning.
- Require explicit confirmation before a hold and preserve truthful recovery states.
- Design for a large projected screen and a usable narrow viewport.

## Accessibility & Inclusion

Preserve semantic controls, accessible names, keyboard navigation, visible focus, readable contrast, reduced-motion support and responsive behavior. The audience must be able to understand the primary action and agent result from the back of a room.
