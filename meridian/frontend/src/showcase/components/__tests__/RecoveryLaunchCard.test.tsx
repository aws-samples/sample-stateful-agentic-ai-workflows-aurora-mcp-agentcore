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
    <RecoveryLaunchCard stage="checkpointed" steps={steps} live compact disabled
      onStart={() => {}} />
  );
}

const checkpointRow = () => screen.getByText('Save an Aurora checkpoint').closest('li')!;

describe('RecoveryLaunchCard', () => {
  it('keys the Aurora glow on the checkpoint step, wherever it sits in the list', () => {
    const { container, rerender } = render(card([
      pending('verify'), pending('checkpoint'), pending('search'), pending('understand'),
    ]));
    // Search is confirmed and happens to sit third; the checkpoint is not confirmed.
    rerender(card([
      pending('checkpoint'), done('understand', 'LangGraph'), done('search', 'Bedrock'),
      pending('verify'),
    ]));
    expect(container.querySelector('.mds-aurora-glow')).toBeNull();
    expect(checkpointRow()).toHaveClass('is-pending');
    expect(screen.getByText('Search and rank').closest('li')).toHaveClass('is-visited');

    rerender(card([
      done('checkpoint', 'Aurora Data API'), done('understand', 'LangGraph'),
      done('search', 'Bedrock'), pending('verify'),
    ]));
    expect(container.querySelectorAll('.mds-aurora-glow')).toHaveLength(1);
    expect(checkpointRow().querySelector('.mds-aurora-glow')).not.toBeNull();
    expect(checkpointRow()).toHaveTextContent('Aurora Data API');
  });
});
