import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  BOUNDARY_CHECKS, LEASE_NOTE, PHASE_PATHS, SCENARIOS, WORKFLOW_NODES, WORKFLOW_STEPS,
} from '../briefingSteps';
import { BoundaryScenarios } from '../BoundaryScenarios';
import { StepPath } from '../StepPath';
import { WorkflowSteps } from '../WorkflowSteps';

const allCaptions = [
  ...Object.values(PHASE_PATHS).flatMap(path => path.steps.map(step => step.caption)),
  ...WORKFLOW_STEPS.map(step => step.caption),
  ...SCENARIOS.flatMap(scenario => [scenario.title, scenario.text]),
];

describe('briefing step data', () => {
  it('points every step at a hop that exists', () => {
    for (const [phase, path] of Object.entries(PHASE_PATHS)) {
      for (const step of path.steps) {
        expect(step.hop, `phase ${phase}: ${step.caption}`).toBeGreaterThanOrEqual(0);
        expect(step.hop, `phase ${phase}: ${step.caption}`).toBeLessThan(path.hops.length);
      }
    }
    for (const scenario of SCENARIOS) {
      if (scenario.stopAt !== null) expect(scenario.stopAt).toBeLessThan(BOUNDARY_CHECKS.length);
    }
  });

  it('writes every caption without dashes or middle dots', () => {
    for (const caption of allCaptions) expect(caption).not.toMatch(/[–—·]/u);
  });

  it('keeps one idea per step: a caption is at most two short sentences', () => {
    for (const caption of allCaptions.filter(text => !SCENARIOS.some(s => s.text === text))) {
      expect(caption.split(/(?<=[.!?])\s/u).length, caption).toBeLessThanOrEqual(2);
    }
  });

  it('says no model runs in Phases 1 and 2', () => {
    expect(PHASE_PATHS[1].steps.some(step => /No model runs/u.test(step.caption))).toBe(true);
    expect(PHASE_PATHS[2].steps.some(step => /in code, with no model/u.test(step.caption))).toBe(true);
  });

  it('keeps the Phase 3 search branches and the merge', () => {
    const search = PHASE_PATHS[3].hops.find(hop => hop.branches);
    expect(search?.branches).toEqual([['Meaning', 'Query vector → pgvector'], ['Words', 'Query text → tsvector']]);
    expect(search?.detail).toBe('Merge candidates by package ID');
    expect(PHASE_PATHS[3].hops[PHASE_PATHS[3].hops.length - 1].image).toEqual({ src: '/travel/catalog/BCH-003.jpg', alt: 'Green rice terraces and palms in Bali' });
  });

  it('drops ineligible trips before reranking and calls the result an example', () => {
    const captions = PHASE_PATHS[3].steps.map(step => step.caption);
    const gate = captions.findIndex(caption => /no places left/u.test(caption));
    const rerank = captions.findIndex(caption => /Rerank 3\.5/u.test(caption));
    expect(gate).toBeGreaterThan(-1);
    expect(gate).toBeLessThan(rerank);
    expect(captions[captions.length - 1]).toMatch(/^In this example, BCH-003 ranks first/u);
  });

  it('names what the traveler confirms in Phase 4, in the deck words, not an undefined "terms"', () => {
    for (const step of Object.values(PHASE_PATHS).flatMap(path => path.steps)) {
      expect(`${step.chip} ${step.caption}`).not.toMatch(/\bterms?\b|receipt/iu);
    }
    const captions = PHASE_PATHS[4].steps.map(step => step.caption).join(' ');
    expect(captions).toMatch(/clicks Hold to confirm the trip shown: package, nights, party size and price/u);
    expect(captions).toMatch(/The Runtime, not the model, calls the hold tool/u);
  });

  it('says the MCP server converts currency from sample rates, not Aurora', () => {
    expect(PHASE_PATHS[2].steps.map(step => step.caption).join(' ')).toMatch(/server .*fixed table of sample rates/u);
  });

  it('ends Phase 5 with the booking ID saved before the write, not a replayed expiry', () => {
    const last = WORKFLOW_STEPS[WORKFLOW_STEPS.length - 1].caption;
    expect(last).toMatch(/booking ID saved before the write/u);
    expect(last).not.toMatch(/expiry/u);
  });

  it('releases the lease when the run pauses and claims a new one on resume', () => {
    const pause = WORKFLOW_STEPS.findIndex(step => step.paused);
    const release = WORKFLOW_STEPS.findIndex(step => /gives up its lease/u.test(step.caption));
    const resume = WORKFLOW_STEPS.findIndex(step => step.session2);
    expect(pause).toBeGreaterThan(0);
    expect(release).toBeGreaterThan(pause);
    expect(WORKFLOW_STEPS[release].lease).toBe('Free while paused');
    expect(WORKFLOW_STEPS.slice(0, resume).every(step => !step.lease.includes('worker 2'))).toBe(true);
    expect(WORKFLOW_STEPS[resume].lease).toBe('Held by worker 2');
    expect(WORKFLOW_STEPS[resume].saved).toBe(WORKFLOW_STEPS[pause].saved);
    expect(WORKFLOW_STEPS[WORKFLOW_STEPS.length - 1].saved).toBe(WORKFLOW_NODES.length);
  });

  it('stops the over-budget hold at Cedar and the second traveler at the grant check', () => {
    const byId = Object.fromEntries(SCENARIOS.map(scenario => [scenario.id, scenario]));
    expect(byId.within.stopAt).toBeNull();
    expect(BOUNDARY_CHECKS[byId.budget.stopAt ?? -1]).toMatchObject({ verb: 'Decide', service: 'AgentCore Gateway' });
    expect(BOUNDARY_CHECKS[byId.other.stopAt ?? -1]).toMatchObject({ verb: 'Execute', service: 'AWS Lambda' });
    expect(byId.other.text).toMatch(/passes Cedar/u);
  });

  it('names the five checks, each with its control', () => {
    expect(BOUNDARY_CHECKS.map(check => check.verb)).toEqual(['Verify', 'Bind', 'Decide', 'Execute', 'Enforce']);
    expect(BOUNDARY_CHECKS.map(check => check.pill))
      .toEqual(['Cognito JWT', 'JWT authorizer', 'Cedar policy', 'Grant table', 'RLS policies']);
  });

  it('names each Phase 5 step plainly, with its graph node in code', () => {
    expect(WORKFLOW_NODES.map(node => [node.title, node.code])).toEqual([
      ['Classify', 'classify'], ['Find alternatives', 'search'], ['Check places', 'availability'],
      ['Save hold IDs', 'prepare_hold'], ['Place hold', 'hold'], ['Show results', 'synthesize'],
    ]);
  });

  it('introduces no Phase 5 terms the path does not explain', () => {
    const text = WORKFLOW_STEPS.map(step => `${step.caption} ${step.lease}`).join(' ');
    expect(text).not.toMatch(/\b(execution|attempt|intent|thread|journey|fenced|session [12])\b/iu);
    expect(LEASE_NOTE).toMatch(/one worker at a time .* 60 seconds without a heartbeat/u);
  });

  it('says Classify picks the recovery route, since recovery skips the keyword rules', () => {
    const classify = WORKFLOW_STEPS.find(step => step.caption.startsWith('Classify'));
    expect(classify?.caption).toMatch(/recovery route/u);
    expect(classify?.caption).not.toMatch(/keyword/u);
  });
});

describe('StepPath', () => {
  const data = PHASE_PATHS[1];

  it('opens on the first step with its caption in a polite live region', () => {
    render(<StepPath data={data} />);
    const caption = screen.getByText(data.steps[0].caption).closest('[aria-live]');
    expect(caption).toHaveAttribute('aria-live', 'polite');
    expect(caption).toHaveTextContent(`Step 1 of ${data.steps.length}`);
    expect(screen.getByRole('list', { name: data.label }).querySelector('[aria-current="step"]'))
      .toHaveTextContent(data.hops[data.steps[0].hop].title);
    expect(screen.getByRole('button', { name: 'Back' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('moves one step per click, back again, and starts over', () => {
    render(<StepPath data={data} />);
    fireEvent.click(screen.getByRole('button', { name: 'Next step' }));
    expect(screen.getByText(data.steps[1].caption)).toBeInTheDocument();
    expect(screen.getByText(`Step 2 of ${data.steps.length}`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByText(data.steps[0].caption)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next step' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start over' }));
    expect(screen.getByText(data.steps[0].caption)).toBeInTheDocument();
  });

  it('keeps focus on Next at the last step and ignores further clicks', () => {
    render(<StepPath data={data} />);
    const next = screen.getByRole('button', { name: 'Next step' });
    next.focus();
    for (let i = 0; i < data.steps.length + 2; i += 1) fireEvent.click(next);
    expect(next).toHaveAttribute('aria-disabled', 'true');
    expect(next).toHaveFocus();
    expect(screen.getByText(data.steps[data.steps.length - 1].caption)).toBeInTheDocument();
    expect(screen.getAllByRole('listitem').every(item => item.classList.contains('is-done'))).toBe(true);
  });

  it('marks finished hops with a check, not color alone', () => {
    const { container } = render(<StepPath data={data} />);
    expect(container.querySelectorAll('.mds-step-hop.is-done .mds-step-mark')).toHaveLength(0);
    for (let i = 0; i < data.steps.length; i += 1) fireEvent.click(screen.getByRole('button', { name: 'Next step' }));
    expect(container.querySelectorAll('.mds-step-hop.is-done .mds-step-mark')).toHaveLength(data.hops.length);
  });

  it('shows a note when given one', () => {
    render(<StepPath data={PHASE_PATHS[4]} note="Signed-in release shown." />);
    expect(screen.getByText('Signed-in release shown.')).toBeInTheDocument();
  });
});

describe('WorkflowSteps', () => {
  const stepTo = (n: number) => {
    for (let i = 0; i < n; i += 1) fireEvent.click(screen.getByRole('button', { name: 'Next step' }));
  };

  it('keeps the six nodes and their wording', () => {
    render(<WorkflowSteps />);
    expect(screen.getByText('Save hold IDs', { exact: true })).toBeInTheDocument();
    expect(screen.getByText('prepare_hold', { exact: true })).toBeInTheDocument();
    expect(screen.getByText(LEASE_NOTE)).toBeInTheDocument();
    expect(within(screen.getByRole('list', { name: 'Phase 5 durable workflow path' })).getAllByRole('listitem')).toHaveLength(6);
  });

  it('pauses for review, frees the lease, then resumes on a new session from the saved step', () => {
    render(<WorkflowSteps />);
    const pause = WORKFLOW_STEPS.findIndex(step => step.paused);
    stepTo(pause);
    expect(screen.getByText('Paused for review')).toBeInTheDocument();
    stepTo(1);
    expect(screen.getByText('Free while paused')).toBeInTheDocument();
    const resume = WORKFLOW_STEPS.findIndex(step => step.restored);
    stepTo(resume - pause - 1);
    expect(screen.getByText('Worker 2, new microVM')).toBeInTheDocument();
    const store = screen.getByRole('complementary', { name: 'Saved steps in Amazon Aurora' });
    expect(within(store).getByText('Saved step: Find alternatives').closest('li')).toHaveClass('is-restored');
  });

  it('marks finished nodes with a check and the microVM that saved them, not color alone', () => {
    const { container } = render(<WorkflowSteps />);
    stepTo(2);
    expect(container.querySelectorAll('.mds-step-node.is-done .mds-step-mark')).toHaveLength(1);
    expect(container.querySelector('.mds-step-node.is-done .mds-step-node-pill')).toHaveTextContent('microVM 1');
    stepTo(WORKFLOW_STEPS.length);
    const pills = [...container.querySelectorAll('.mds-step-node-pill')].map(pill => pill.textContent);
    expect(pills).toEqual([...Array(2).fill('microVM 1, saved'), ...Array(4).fill('microVM 2, saved')]);
  });

  it('ends with all six steps saved and the lease released', () => {
    render(<WorkflowSteps />);
    stepTo(WORKFLOW_STEPS.length);
    const store = screen.getByRole('complementary', { name: 'Saved steps in Amazon Aurora' });
    expect(within(store).getAllByText(/^Saved step:/u)).toHaveLength(6);
    expect(screen.getByText('Released, run finished')).toBeInTheDocument();
  });
});

describe('BoundaryScenarios', () => {
  it('stops each scenario at the right check and says so', () => {
    render(<BoundaryScenarios />);
    const lane = screen.getByRole('list', { name: 'Checks a hold request passes' });
    const stopped = () => lane.querySelector('.is-stopped')?.textContent ?? 'none';
    expect(screen.getByRole('button', { name: 'Hold within budget' })).toHaveAttribute('aria-pressed', 'true');
    expect(stopped()).toBe('none');
    expect(screen.getByText('Committed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Over the saved budget' }));
    expect(stopped()).toContain('AgentCore Gateway');
    expect(screen.getByText('Refused by Cedar')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Second traveler' }));
    expect(stopped()).toContain('AWS Lambda');
    expect(stopped()).toContain('Stopped here');
    expect(lane.querySelector('.is-stopped .mds-check-mark')).toHaveTextContent('\u2715');
    expect(lane.querySelectorAll('.mds-check-row.is-done')).toHaveLength(3);
    expect(screen.getByText('Refused by the grant check')).toBeInTheDocument();
  });

  it('labels every check, the configuration shown and that the scenarios are illustrative', () => {
    render(<BoundaryScenarios />);
    for (const check of BOUNDARY_CHECKS) {
      expect(screen.getByText(check.verb)).toBeInTheDocument();
      expect(screen.getByText(check.pill)).toBeInTheDocument();
    }
    expect(screen.getByText(/^Signed-in release shown\. Illustrative scenarios/u)).toBeInTheDocument();
  });
});
