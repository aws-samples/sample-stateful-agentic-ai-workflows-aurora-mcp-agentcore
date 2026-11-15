import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { SHOWCASE_FINALE_PROMPT } from '../../lib/showcaseAdapters';
import { WorkflowWorkspace } from '../WorkflowWorkspace';
import { makeState } from './makeState';

it('runs the saved-step recovery with the typed intent that can reach the hold', () => {
  const state = makeState();
  render(<WorkflowWorkspace state={state} onOpenRecovery={() => undefined} />);

  fireEvent.click(screen.getByRole('button', { name: 'Run to saved step' }));

  expect(state.applyPhaseExample).toHaveBeenCalledWith(
    SHOWCASE_FINALE_PROMPT, true, 5, { intent: 'recovery' },
  );
});
