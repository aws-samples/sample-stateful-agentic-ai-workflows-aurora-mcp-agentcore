import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import { NavPanelDrawer } from '../NavPanelDrawer';
import { ALEX_IDENTITY, UNKNOWN_IDENTITY } from '../../../test/signedIn';

function state(traveler: MeridianShowcaseState['traveler']): MeridianShowcaseState {
  return {
    traveler,
    messages: [
      { role: 'user', text: 'Find me a quiet beach week.' },
      { role: 'bot', text: 'Here are three options.' },
    ],
  } as unknown as MeridianShowcaseState;
}

describe('NavPanelDrawer messages', () => {
  it('labels the traveler turn with their first name and never with anyone else', () => {
    render(<NavPanelDrawer state={state(ALEX_IDENTITY)} panel="messages" onClose={vi.fn()} />);
    expect(screen.getByText('Alex')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('Jordan');
  });

  it('says "You" when the account is unnamed', () => {
    render(<NavPanelDrawer state={state(UNKNOWN_IDENTITY)} panel="messages" onClose={vi.fn()} />);
    expect(screen.getByText('You')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('Jordan');
  });
});
