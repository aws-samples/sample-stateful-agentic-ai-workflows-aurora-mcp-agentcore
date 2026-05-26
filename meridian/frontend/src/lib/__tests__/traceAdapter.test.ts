import { describe, expect, it } from 'vitest'
import {
  activityTraceToSpans,
  chatResponseToMessages,
  memoryResponseToFacts,
  packagesResponseToTripCards,
} from '../traceAdapter'
import type { ActivityEntry, ChatResponse, Message, Product } from '../../types'

const baseActivity = (overrides: Partial<ActivityEntry>): ActivityEntry => ({
  id: 'act-1',
  timestamp: '2026-05-25T00:00:00Z',
  activity_type: 'reasoning',
  title: 'supervisor.plan',
  ...overrides,
})

describe('activityTraceToSpans', () => {
  it('returns an empty array for undefined / empty input', () => {
    expect(activityTraceToSpans(undefined)).toEqual([])
    expect(activityTraceToSpans([])).toEqual([])
  })

  it('maps the common fields and falls back to runtime category', () => {
    const spans = activityTraceToSpans([
      baseActivity({
        id: 'span-a',
        title: 'supervisor.plan',
        agent_name: 'ProductionAgent',
        execution_time_ms: 28,
      }),
    ])
    expect(spans).toHaveLength(1)
    expect(spans[0]).toMatchObject({
      id: 'span-a',
      title: 'supervisor.plan',
      agent: 'ProductionAgent',
      latencyMs: 28,
      category: 'runtime',
      status: 'ok',
    })
  })

  it('preserves telemetry tokens, sql and explicit category', () => {
    const spans = activityTraceToSpans([
      baseActivity({
        id: 'span-b',
        title: 'claude.compose',
        execution_time_ms: 132,
        sql_query: 'SELECT 1',
        telemetry: {
          category: 'model',
          component: 'Bedrock · Claude',
          status: 'streaming',
          tokens: { input: 1820, output: 312 },
        },
      }),
    ])
    expect(spans[0]).toMatchObject({
      category: 'model',
      status: 'streaming',
      sql: 'SELECT 1',
      tokensIn: 1820,
      tokensOut: 312,
    })
  })

  it('accepts camelCase aliases from the backend (executionTimeMs, sqlQuery)', () => {
    const spans = activityTraceToSpans([
      // The backend ActivityEntry type allows both snake_case (real shape) and
      // camelCase aliases — this happens occasionally with serializers.
      baseActivity({
        id: 'span-c',
        title: 'memory.recall',
        executionTimeMs: 42,
        sqlQuery: 'SELECT * FROM traveler_preferences',
      } as ActivityEntry),
    ])
    expect(spans[0].latencyMs).toBe(42)
    expect(spans[0].sql).toContain('traveler_preferences')
  })

  it('synthesizes ids for activities missing one (so React keys are stable)', () => {
    const spans = activityTraceToSpans([
      baseActivity({ id: '' as unknown as string, title: 'orphan' }),
    ])
    expect(spans[0].id).toMatch(/span-\d+/)
  })
})

describe('chatResponseToMessages', () => {
  const userText = 'A slow week somewhere we can drink good wine.'
  const prior: Message[] = []

  it('returns user-only history when response is null', () => {
    const msgs = chatResponseToMessages(prior, userText, null)
    expect(msgs).toHaveLength(1)
    expect(msgs[0]).toEqual({ role: 'user', text: userText })
  })

  it('appends a text bot reply when no products / order present', () => {
    const response: ChatResponse = { message: 'Tuscany fits.', activities: [] }
    const msgs = chatResponseToMessages(prior, userText, response)
    expect(msgs).toHaveLength(2)
    expect(msgs[1]).toEqual({ role: 'bot', type: 'text', text: 'Tuscany fits.' })
  })

  it('preserves products on a products-style reply', () => {
    const product: Product = {
      product_id: 'CTY-001',
      name: 'Tuscan Vineyards',
      brand: 'Borgo San Felice',
      price: 2840,
      description: 'Boutique stay in Chianti',
      image_url: '',
      category: 'City Breaks',
    }
    const msgs = chatResponseToMessages(prior, userText, {
      message: 'I held three options.',
      activities: [],
      products: [product],
    })
    expect(msgs[1]).toMatchObject({ role: 'bot', type: 'products', products: [product] })
  })

  it('carries follow_ups through onto the reply', () => {
    const msgs = chatResponseToMessages(prior, userText, {
      message: 'Sure.',
      activities: [],
      follow_ups: ['Add a cooking class?'],
    })
    expect(msgs[1].follow_ups).toEqual(['Add a cooking class?'])
  })
})

describe('memoryResponseToFacts', () => {
  it('returns an empty array when response is null / missing facts', () => {
    expect(memoryResponseToFacts(null)).toEqual([])
    expect(memoryResponseToFacts(undefined)).toEqual([])
    expect(
      memoryResponseToFacts({ traveler_id: 'trv_meridian_demo', facts: [] }),
    ).toEqual([])
  })

  it('returns backend facts verbatim when present', () => {
    const facts = [{ key: 'no_red_eye', value: 'true', confidence: 0.99 }]
    expect(
      memoryResponseToFacts({ traveler_id: 'trv_meridian_demo', facts }),
    ).toEqual(facts)
  })
})

describe('packagesResponseToTripCards', () => {
  it('returns [] for undefined / empty input', () => {
    expect(packagesResponseToTripCards(undefined)).toEqual([])
    expect(packagesResponseToTripCards([])).toEqual([])
  })

  it('derives default tags by category and falls back to "Refundable"', () => {
    const cards = packagesResponseToTripCards([
      {
        product_id: 'CTY-001',
        name: 'Tuscan Vineyards',
        brand: 'Borgo',
        price: 2840,
        description: 'Slow wine country.',
        image_url: '',
        category: 'City Breaks',
      },
      {
        product_id: 'UNK-001',
        name: 'Mystery package',
        brand: 'Unknown',
        price: 1000,
        description: '',
        image_url: '',
        category: 'Made-up category that has no rule',
      },
    ])
    expect(cards[0].tags).toEqual(['Walkable', 'Refundable'])
    expect(cards[1].tags).toEqual(['Refundable'])
  })

  it('passes similarity through unchanged when present', () => {
    const cards = packagesResponseToTripCards([
      {
        product_id: 'CTY-002',
        name: 'Provence Slow Week',
        brand: 'Domaine',
        price: 2610,
        description: '',
        image_url: '',
        category: 'City Breaks',
        similarity: 0.91,
      },
    ])
    expect(cards[0].similarity).toBeCloseTo(0.91, 5)
  })
})
