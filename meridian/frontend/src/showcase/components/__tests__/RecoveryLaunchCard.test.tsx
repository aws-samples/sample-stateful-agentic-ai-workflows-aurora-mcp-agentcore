import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { RecoveryStepView } from '../../lib/recoveryState';
import { RecoveryLaunchCard } from '../RecoveryDecisionCards';

const pending = (id: RecoveryStepView['id']): RecoveryStepView => (
  { id, state: 'is-pending', source: null });
const done = (id: RecoveryStepView['id'], source: string): RecoveryStepView => (
  { id, state: 'is-visited', source });

function card(steps: RecoveryStepView[]) {
  return (
    <RecoveryLaunchCard stage="saved" steps={steps} live compact disabled
      onStart={() => {}} />
  );
}

const snapshotRow = () => screen.getByText('Save a step in Aurora').closest('li')!;

describe('RecoveryLaunchCard', () => {
  it('keys the Aurora glow on the snapshot step, wherever it sits in the list', () => {
    const { container, rerender } = render(card([
      pending('verify'), pending('snapshot'), pending('search'), pending('understand'),
    ]));
    // Search is confirmed and happens to sit third; the snapshot is not confirmed.
    rerender(card([
      pending('snapshot'), done('understand', 'Strands Graph'), done('search', 'Bedrock'),
      pending('verify'),
    ]));
    expect(container.querySelector('.mds-aurora-glow')).toBeNull();
    expect(snapshotRow()).toHaveClass('is-pending');
    expect(screen.getByText('Search and rank').closest('li')).toHaveClass('is-visited');

    rerender(card([
      done('snapshot', 'Aurora Data API'), done('understand', 'Strands Graph'),
      done('search', 'Bedrock'), pending('verify'),
    ]));
    expect(container.querySelectorAll('.mds-aurora-glow')).toHaveLength(1);
    expect(snapshotRow().querySelector('.mds-aurora-glow')).not.toBeNull();
    expect(snapshotRow()).toHaveTextContent('Aurora Data API');
  });
});
