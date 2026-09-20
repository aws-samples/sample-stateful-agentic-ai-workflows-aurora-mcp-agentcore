import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { useDialogA11y } from '../useDialogA11y';

function DialogJourney({ removeTrigger }: { removeTrigger: boolean }) {
  const [open, setOpen] = useState(false);
  const [opened, setOpened] = useState(false);
  const ref = useDialogA11y(open, () => setOpen(false));
  return <>
    <div tabIndex={0} data-dialog-focus-fallback aria-label="Workspace" />
    {(!opened || !removeTrigger) && <button onClick={() => {
      setOpened(true);
      setOpen(true);
    }}>Open details</button>}
    {open && <section ref={ref} role="dialog" tabIndex={-1}>
      <button onClick={() => setOpen(false)}>Close</button>
    </section>}
  </>;
}

describe('dialog return focus', () => {
  it('returns to the original trigger when it is still present', () => {
    render(<DialogJourney removeTrigger={false} />);
    const trigger = screen.getByRole('button', { name: 'Open details' });
    trigger.focus();
    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.activeElement).toBe(trigger);
  });

  it('returns to the workspace after navigation removes the trigger', () => {
    render(<DialogJourney removeTrigger />);
    const trigger = screen.getByRole('button', { name: 'Open details' });
    trigger.focus();
    fireEvent.click(trigger);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(screen.getByLabelText('Workspace'));
  });
});
