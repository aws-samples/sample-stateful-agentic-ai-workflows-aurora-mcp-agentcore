import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  BOUNDARY_HOPS, BOUNDARY_LABELS, PHASE_PATHS, SCENARIOS, WORKFLOW_NODES, WORKFLOW_STEPS,
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
      if (scenario.stopAt !== null) expect(scenario.stopAt).toBeLessThan(BOUNDARY_HOPS.length);
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
    const release = WORKFLOW_STEPS.findIndex(step => /releases its lease/u.test(step.caption));
    const resume = WORKFLOW_STEPS.findIndex(step => step.session2);
    expect(pause).toBeGreaterThan(0);
    expect(release).toBeGreaterThan(pause);
    expect(WORKFLOW_STEPS[release].lease).toBe('Free, run paused');
    expect(WORKFLOW_STEPS.slice(0, resume).every(step => !step.lease.includes('session 2'))).toBe(true);
    expect(WORKFLOW_STEPS[resume].lease).toBe('Held by session 2');
    expect(WORKFLOW_STEPS[resume].saved).toBe(WORKFLOW_STEPS[pause].saved);
    expect(WORKFLOW_STEPS[WORKFLOW_STEPS.length - 1].saved).toBe(WORKFLOW_NODES.length);
  });

  it('stops the over-budget hold at Cedar and the second traveler at the grant check', () => {
    const byId = Object.fromEntries(SCENARIOS.map(scenario => [scenario.id, scenario]));
    expect(byId.within.stopAt).toBeNull();
    expect(BOUNDARY_HOPS[byId.budget.stopAt ?? -1].title).toBe('Gateway and Cedar');
    expect(BOUNDARY_HOPS[byId.other.stopAt ?? -1].title).toBe('Holds Lambda');
    expect(BOUNDARY_LABELS).toEqual(['Identity', 'Permission', 'Grant', 'Outcome']);
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
    expect(screen.getByText('Prepare hold', { exact: true })).toBeInTheDocument();
    expect(screen.getByText('Confirm, then save intent')).toBeInTheDocument();
    expect(within(screen.getByRole('list', { name: 'Phase 5 durable workflow path' })).getAllByRole('listitem')).toHaveLength(6);
  });

  it('pauses for review, frees the lease, then resumes on a new session from the saved step', () => {
    render(<WorkflowSteps />);
    const pause = WORKFLOW_STEPS.findIndex(step => step.paused);
    stepTo(pause);
    expect(screen.getByText('Paused for review')).toBeInTheDocument();
    stepTo(1);
    expect(screen.getByText('Free, run paused')).toBeInTheDocument();
    const resume = WORKFLOW_STEPS.findIndex(step => step.restored);
    stepTo(resume - pause - 1);
    expect(screen.getByText('Runtime session 2, new microVM')).toBeInTheDocument();
    const store = screen.getByRole('complementary', { name: 'Saved steps in Amazon Aurora' });
    expect(within(store).getByText('Saved step: Search').closest('li')).toHaveClass('is-restored');
  });

  it('ends with all six steps saved and the lease released', () => {
    render(<WorkflowSteps />);
    stepTo(WORKFLOW_STEPS.length);
    const store = screen.getByRole('complementary', { name: 'Saved steps in Amazon Aurora' });
    expect(within(store).getAllByText(/^Saved step:/u)).toHaveLength(6);
    expect(screen.getByText('Released, run succeeded')).toBeInTheDocument();
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
    expect(stopped()).toContain('Gateway and Cedar');
    expect(screen.getByText('Refused by Cedar')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Second traveler' }));
    expect(stopped()).toContain('Holds Lambda');
    expect(screen.getByText('Refused by the grant check')).toBeInTheDocument();
  });

  it('labels every check, the configuration shown and that the scenarios are illustrative', () => {
    render(<BoundaryScenarios />);
    for (const label of BOUNDARY_LABELS) expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText(/^Signed-in release shown\. Illustrative scenarios/u)).toBeInTheDocument();
  });
});
