import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SolutionBriefing } from '../SolutionBriefing';

describe('SolutionBriefing', () => {
  it('starts with only architecture open and reveals preparation and phases on demand', () => {
    const { container } = render(<SolutionBriefing onOpenLadder={() => {}} onOpenEvidence={() => {}} />);
    expect(container.querySelectorAll('details[open]')).toHaveLength(1);
    expect(screen.getByText('The architecture').closest('details')).toHaveAttribute('open');
    expect(screen.getByText('Package facts')).not.toBeVisible();
    expect(screen.getByText('Query vector → pgvector')).not.toBeVisible();
    fireEvent.click(screen.getByText('Prepare the data before the question'));
    fireEvent.click(screen.getByText('Five phases, one connected system'));
    expect(screen.getByRole('heading', { name: 'Prepare the data before the question' })).toBeVisible();
    for (const name of ['Phase 1 · SQL', 'Phase 2 · MCP', 'Phase 3 · Retrieval', 'Phase 4 · Production', 'Phase 5 · Workflow']) {
      expect(screen.getByRole('heading', { name })).toBeVisible();
    }
    expect(screen.getByText('Query vector → pgvector')).toBeVisible();
    expect(screen.getByText('Query text → tsvector')).toBeVisible();
    expect(screen.getByText('Merge candidates by package ID')).toBeVisible();
    expect(screen.getByText('Illustrative query · seeded catalog example')).toBeVisible();
    expect(screen.getByRole('img', { name: 'Green rice terraces and palms in Bali' })).toHaveAttribute('src', '/travel/catalog/BCH-003.jpg');
  });

  it('keeps the architecture available while inspecting policies and tools', () => {
    render(<SolutionBriefing onOpenLadder={() => {}} onOpenEvidence={() => {}} />);
    expect(screen.getByRole('heading', { level: 1, name: 'Solution briefing' })).toBeInTheDocument();
    expect(screen.getByText('meridian_hold_governance')).not.toBeVisible();
    fireEvent.click(screen.getByText('Tool contracts & Cedar policies'));
    expect(screen.getByText('meridian_hold_governance')).toBeVisible();
    expect(screen.getByText('meridian_booking_governance')).toBeVisible();
    expect(screen.getByText('MeridianHolds___create_courtesy_hold')).toBeVisible();
    expect(screen.getByText('MeridianHolds___confirm_booking')).toBeVisible();
    expect(screen.getAllByText(/context\.input\.totalCents <= context\.input\.budgetCeilingCents/)).toHaveLength(2);
    expect(screen.getByRole('img', { name: /Meridian request and state architecture/ })).toBeInTheDocument();
    fireEvent.click(screen.getByText('The architecture'));
    expect(screen.getByText('The architecture').closest('details')).not.toHaveAttribute('open');
    expect(screen.getByText('meridian_hold_governance')).toBeVisible();
    fireEvent.click(screen.getByText('The architecture'));
    expect(screen.getByText('The architecture').closest('details')).toHaveAttribute('open');
    fireEvent.click(screen.getByText('Tool contracts & Cedar policies'));
    expect(screen.getByText('meridian_hold_governance')).not.toBeVisible();
  });

  it('preserves preparation and distinct recovery failure windows in the reference', () => {
    render(<SolutionBriefing onOpenLadder={() => {}} onOpenEvidence={() => {}} />);
    fireEvent.click(screen.getByText('Data preparation & the five phases'));
    expect(screen.getByText('scripts/seed_data.py')).toBeVisible();
    fireEvent.click(screen.getByText('Recovery guarantees & evidence'));
    for (const text of ['Before the hold', 'Write committed, response lost', 'After the hold checkpoint']) {
      expect(screen.getByText(text)).toBeVisible();
    }
    expect(screen.getByText(/separate transactions/)).toBeVisible();
  });

  it('hands off to the capability ladder and live evidence', () => {
    const onOpenLadder = vi.fn();
    const onOpenEvidence = vi.fn();
    render(<SolutionBriefing onOpenLadder={onOpenLadder} onOpenEvidence={onOpenEvidence} />);
    fireEvent.click(screen.getByRole('button', { name: /Open the capability ladder/ }));
    expect(onOpenLadder).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /Inspect system evidence/ }));
    expect(onOpenEvidence).toHaveBeenCalledTimes(1);
  });
});
