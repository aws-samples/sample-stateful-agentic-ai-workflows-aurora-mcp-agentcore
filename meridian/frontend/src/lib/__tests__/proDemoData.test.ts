import { describe, expect, it } from 'vitest'
import {
  DEMO_PROMPT,
  DEMO_TRAVELER,
  DEMO_TRAVELER_ID,
  DEMO_TRAVELER_TAGS,
  MCP_TOOL_CATALOG,
} from '../proDemoData'

describe('proDemoData constants', () => {
  it('DEMO_TRAVELER_ID matches the seeded id', () => {
    expect(DEMO_TRAVELER_ID).toBe('trv_meridian_demo')
  })

  it('DEMO_TRAVELER has a complete profile for the workspace header', () => {
    expect(DEMO_TRAVELER.full_name).toBeTruthy()
    expect(DEMO_TRAVELER.home_airport).toBe('BOS')
    expect(DEMO_TRAVELER.party_size).toBeGreaterThan(0)
    expect(DEMO_TRAVELER.budget_max).toBeGreaterThan(DEMO_TRAVELER.budget_min ?? 0)
  })

  it('DEMO_TRAVELER_TAGS is non-empty and unique', () => {
    expect(DEMO_TRAVELER_TAGS.length).toBeGreaterThan(0)
    expect(new Set(DEMO_TRAVELER_TAGS).size).toBe(DEMO_TRAVELER_TAGS.length)
  })

  it('MCP_TOOL_CATALOG lists postgres.run_query and trips.hybrid_search', () => {
    const names = MCP_TOOL_CATALOG.map((t) => t.name)
    expect(names).toContain('postgres.run_query')
    expect(names).toContain('trips.hybrid_search')
  })

  it('MCP_TOOL_CATALOG marks workshop tools healthy (no fake degraded rows)', () => {
    const availability = MCP_TOOL_CATALOG.find((t) => t.name === 'availability.lookup')
    expect(availability?.health).toBe('healthy')
    expect(MCP_TOOL_CATALOG.every((t) => t.health === 'healthy')).toBe(true)
  })

  it('DEMO_PROMPT mentions both the wine intent and the red-eye constraint', () => {
    expect(DEMO_PROMPT.toLowerCase()).toContain('wine')
    expect(DEMO_PROMPT.toLowerCase()).toContain('red-eye')
  })
})
