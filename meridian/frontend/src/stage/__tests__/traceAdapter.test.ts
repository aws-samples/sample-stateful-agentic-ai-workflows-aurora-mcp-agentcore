import { describe, expect, it } from 'vitest'
import { adaptChatResponseToScenario, sumLatency } from '../utils/traceAdapter'
import { getStageScenarioById } from '../data/stageScenarios'
import type { ChatResponse } from '../../types'

const beach = getStageScenarioById('beach')

describe('sumLatency', () => {
  it('returns 0 for an empty span list', () => {
    expect(sumLatency([])).toBe(0)
  })

  it('sums span latencies', () => {
    const spans = [
      { id: 'a', kind: 'tool' as const, system: 'mcp' as const, name: 'x', latencyMs: 30 },
      { id: 'b', kind: 'tool' as const, system: 'mcp' as const, name: 'y', latencyMs: 66 },
    ]
    expect(sumLatency(spans)).toBe(96)
  })
})

describe('adaptChatResponseToScenario', () => {
  it('returns null for a null response', () => {
    expect(adaptChatResponseToScenario(null, beach)).toBeNull()
    expect(adaptChatResponseToScenario(undefined, beach)).toBeNull()
  })

  it('returns null when the response has no activities and no products', () => {
    const empty: ChatResponse = { message: '', activities: [] }
    expect(adaptChatResponseToScenario(empty, beach)).toBeNull()
  })

  it('uses response.message for assistantReply', () => {
    const live: ChatResponse = {
      message: '   Tuscany is held — three options under cap.   ',
      activities: [
        {
          id: 'live-1',
          timestamp: '2026-05-25T00:00:00Z',
          activity_type: 'reasoning',
          title: 'supervisor.plan',
          execution_time_ms: 30,
        },
      ],
    }
    const merged = adaptChatResponseToScenario(live, beach)
    expect(merged!.assistantReply).toBe('Tuscany is held — three options under cap.')
  })

  it('builds spans from backend activities only', () => {
    const activitiesOnly: ChatResponse = {
      message: 'live reply',
      activities: [
        {
          id: 'live-a',
          timestamp: '2026-05-25T00:00:00Z',
          activity_type: 'mcp',
          title: 'trips.hybrid_search',
          execution_time_ms: 96,
          telemetry: { category: 'tool', component: 'mcp.tools' },
        },
      ],
    }
    const merged = adaptChatResponseToScenario(activitiesOnly, beach)!
    expect(merged.spans).toHaveLength(1)
    expect(merged.spans[0].kind).toBe('tool')
    expect(merged.spans[0].system).toBe('mcp')
  })

  it('derives recommendations from products', () => {
    const productsOnly: ChatResponse = {
      message: 'live reply',
      activities: [],
      products: [
        {
          product_id: 'CTY-001',
          name: 'Tuscan Vineyards',
          brand: 'Borgo',
          price: 2840,
          description: 'Slow week.',
          image_url: '',
          category: 'City Breaks',
          similarity: 0.96,
        },
      ],
    }
    const merged = adaptChatResponseToScenario(productsOnly, beach)!
    expect(merged.recommendations).toHaveLength(1)
    expect(merged.recommendations[0].matchPct).toBe(96)
    expect(merged.recommendations[0].primary).toBe(true)
  })
})
