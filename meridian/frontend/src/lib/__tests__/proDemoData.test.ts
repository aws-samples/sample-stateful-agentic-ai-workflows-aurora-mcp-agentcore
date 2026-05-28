import { describe, expect, it } from 'vitest'
import {
  AURORA_TABLES,
  DEMO_PROMPT,
  DEMO_TRAVELER_ID,
  MCP_TOOL_CATALOG,
} from '../proDemoData'

describe('proDemoData static config', () => {
  it('DEMO_TRAVELER_ID matches the seeded id', () => {
    expect(DEMO_TRAVELER_ID).toBe('trv_meridian_demo')
  })

  it('DEMO_PROMPT is a non-empty multi-intent example query', () => {
    expect(DEMO_PROMPT.length).toBeGreaterThan(0)
    expect(DEMO_PROMPT.toLowerCase()).toContain('tokyo')
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

  it('AURORA_TABLES includes the core travel schema tables', () => {
    expect(AURORA_TABLES).toContain('trip_packages')
    expect(AURORA_TABLES).toContain('traveler_preferences')
    expect(AURORA_TABLES).toContain('agent_traces')
  })
})
