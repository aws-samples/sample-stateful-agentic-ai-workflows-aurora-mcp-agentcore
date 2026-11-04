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

const snapshotRow = () => screen.getByText('Review and confirm').closest('li')!;

describe('RecoveryLaunchCard', () => {
  it('keys the Aurora glow on the snapshot step, wherever it sits in the list', () => {
    const { container, rerender } = render(card([
      pending('verify'), pending('snapshot'), pending('search'), pending('hold'), pending('result'),
    ]));
    // Search is confirmed and happens to sit third; the snapshot is not confirmed.
    rerender(card([
      pending('snapshot'), pending('hold'), done('search', 'Bedrock'),
      pending('verify'), pending('result'),
    ]));
    expect(container.querySelector('.mds-aurora-glow')).toBeNull();
    expect(snapshotRow()).toHaveClass('is-pending');
    expect(screen.getByText('Find alternatives').closest('li')).toHaveClass('is-visited');

    rerender(card([
      done('snapshot', 'Aurora Data API'), pending('hold'),
      done('search', 'Bedrock'), pending('verify'), pending('result'),
    ]));
    expect(container.querySelectorAll('.mds-aurora-glow')).toHaveLength(1);
    expect(snapshotRow().querySelector('.mds-aurora-glow')).not.toBeNull();
    expect(snapshotRow()).toHaveTextContent('Aurora Data API');
  });

  it('shows the five recovery steps in the order the talk names them', () => {
    render(card(['search', 'snapshot', 'verify', 'hold', 'result'].map(
      id => pending(id as RecoveryStepView['id']),
    )));
    const list = screen.getByRole('list', { name: 'Recovery workflow progress' });
    expect(Array.from(list.querySelectorAll('li strong'), item => item.textContent)).toEqual([
      'Find alternatives', 'Review and confirm', 'Check places', 'Place hold', 'Show results',
    ]);
  });
});
