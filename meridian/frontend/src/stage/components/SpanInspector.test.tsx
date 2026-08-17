import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import type { StageSpan } from '../types';
import { SpanInspector } from './SpanInspector';

const SPAN: StageSpan = {
  id: 'search',
  kind: 'data',
  system: 'aurora',
  name: 'Search trip packages',
  latencyMs: 18,
  status: 'ok',
  component: 'Aurora PostgreSQL',
  input: 'Tokyo',
  output: '3 packages',
};

function InspectorHarness() {
  const [span, setSpan] = useState<StageSpan | null>(null);
  return (
    <>
      <button type="button" onClick={() => setSpan(SPAN)}>
        Open span
      </button>
      <SpanInspector span={span} onClose={() => setSpan(null)} />
    </>
  );
}

describe('SpanInspector', () => {
  it('traps focus, closes on Escape, and restores focus to the trigger', async () => {
    render(<InspectorHarness />);

    const trigger = screen.getByRole('button', { name: 'Open span' });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = await screen.findByRole('dialog', {
      name: 'Search trip packages',
    });
    expect(dialog).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Close inspector' }))
        .toHaveFocus();
    });

    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    });
  });
});
