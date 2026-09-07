# Meridian

<!-- impeccable:product-schema 1 -->

## Platform

web

## Product Purpose

Meridian is a travel concierge with destination discovery, conversational trip planning, saved trips, comparisons, and disruption recovery. The existing showcase also demonstrates five stages of agent capabilities and their supporting evidence.

## Users

The traveler experience follows Alex Morgan, the repository's sample traveler. Workshop presenters use the capability ladder and proof surfaces to demonstrate the implementation. These roles and capabilities are established by the existing code.

## Capabilities and Constraints

Preserve the running React application, live backend integration, traveler context, saved-trip persistence, comparison tools, and recovery workflow. Catalog content and pricing are sample travel inventory, not a live airline booking service. Do not invent airline partnerships, flight status, reservations, or traveler preferences. Preserve existing uncommitted work while enhancing it.

## Brand Commitments

The user confirmed that the name stays Meridian and requested airline-level polish, referencing United Airlines and Delta Airlines. Color changes are authorized. Photography, travel-specific icons, and practical service interactions should create a credible concierge experience.

## Evidence on Hand

The catalog and traveler profile are served through the existing API. The repository bundles destination photography in meridian/frontend/public/travel, with provenance in its README and image manifest. Brand and sample traveler assets already exist.

## Product Principles

- Give travelers clear next actions and legible trip details.
- Ground personalization in the returned traveler profile and preferences.
- Make offline, loading, saved, and empty states explicit.
- Keep presenter controls available without making technical infrastructure the traveler experience.

## Presentation context

The user supplied a level-300 session abstract, “Build stateful agentic AI workflows with Aurora, MCP, and AgentCore.” The intended walkthrough is Concierge (what good looks like), Capability ladder (SQL, MCP, retrieval, identity and memory, workflow), Recovery desk (disruption and resumability), then System evidence (observed records). Statefulness means recalled traveler context and persisted, resumable execution. Governance should name the observed controls: workload identity, traveler authorization, row-level security, and audit records. All four views need readable type for a large projector and the back of the room.
