import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { ShowcaseSheet } from '../ShowcaseSheet';
import { IconTooltip } from '../ShowcaseTooltip';

function SheetHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open preferences
      </button>
      <ShowcaseSheet
        open={open}
        onOpenChange={setOpen}
        title="Traveler memory"
        subtitle="Alex Morgan"
        description="Review traveler preferences"
        closeLabel="Close preferences"
      >
        <button type="button">Save preference</button>
      </ShowcaseSheet>
    </>
  );
}

describe('showcase Radix primitives', () => {
  it('closes the sheet on Escape and returns focus to its trigger', async () => {
    render(<SheetHarness />);

    const trigger = screen.getByRole('button', { name: 'Open preferences' });
    trigger.focus();
    fireEvent.click(trigger);

    expect(await screen.findByRole('dialog', { name: 'Traveler memory' }))
      .toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    });
  });

  it('exposes tooltip guidance to keyboard users', async () => {
    render(
      <IconTooltip label="Replay trace">
        <button type="button" aria-label="Replay trace">
          Replay
        </button>
      </IconTooltip>,
    );

    fireEvent.focus(screen.getByRole('button', { name: 'Replay trace' }));

    expect(await screen.findByRole('tooltip')).toHaveTextContent('Replay trace');
  });
});
