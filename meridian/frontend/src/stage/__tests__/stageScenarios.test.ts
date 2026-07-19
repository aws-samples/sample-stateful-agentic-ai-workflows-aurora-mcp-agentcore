import { describe, expect, it } from 'vitest'
import {
  DEFAULT_SCENARIO_ID,
  STAGE_SCENARIOS,
  getStageScenarioById,
} from '../data/stageScenarios'
import { SHOWCASE_EXAMPLE_PROMPTS } from '../../showcase/lib/showcaseAdapters'

describe('Demo Stage scenario prompts', () => {
  it('defines three keynote scenarios', () => {
    expect(STAGE_SCENARIOS).toHaveLength(3)
    const ids = STAGE_SCENARIOS.map((s) => s.id)
    expect(ids).toEqual(['tokyo', 'recall', 'plan'])
  })

  it('getStageScenarioById returns tokyo by default for unknown ids', () => {
    const scenario = getStageScenarioById('does-not-exist' as 'tokyo')
    expect(scenario.id).toBe('tokyo')
  })

  it('each scenario has a prompt and traveler id but no pre-baked trace', () => {
    for (const scenario of STAGE_SCENARIOS) {
      expect(scenario.prompt.length).toBeGreaterThan(10)
      expect(scenario.traveler.id).toBe('trv_meridian_demo')
      expect(scenario.spans).toEqual([])
      expect(scenario.recommendations).toEqual([])
    }
  })

  it('DEFAULT_SCENARIO_ID is tokyo', () => {
    expect(DEFAULT_SCENARIO_ID).toBe('tokyo')
  })

  it('stays synchronized with the three Production showcase prompts', () => {
    expect(STAGE_SCENARIOS.map((scenario) => scenario.prompt)).toEqual(
      SHOWCASE_EXAMPLE_PROMPTS[4],
    )
  })
})
