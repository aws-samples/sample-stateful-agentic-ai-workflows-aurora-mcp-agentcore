import type { FormEvent } from 'react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';

export function ChatComposer({ state, compact = false }: { state: MeridianShowcaseState; compact?: boolean }) {
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void state.submitPrompt();
  };

  const quickActions = compact
    ? []
    : [
        { label: 'Add travelers', prompt: 'Add two travelers to this itinerary.' },
        { label: 'Change dates', prompt: 'Shift the trip dates by one week.' },
        { label: 'Add spa', prompt: 'Add a spa day to the itinerary.' },
        { label: 'Direct flights', prompt: 'Prefer direct flights only.' },
      ];

  return (
    <div className={`mds-chat-composer-wrap${compact ? ' is-compact' : ''}`}>
      <form className={`mds-chat-composer${compact ? ' is-compact' : ''}`} onSubmit={onSubmit}>
        <input
          value={state.currentPrompt}
          onChange={(event) => state.setCurrentPrompt(event.target.value)}
          placeholder="Ask Meridian anything..."
          disabled={state.isLoading}
          aria-label="Ask Meridian anything"
        />
        <button type="submit" disabled={state.isLoading || !state.currentPrompt.trim()} aria-label="Send message">
          {state.isLoading ? '...' : 'Send'}
        </button>
      </form>
      {quickActions.length > 0 && (
        <div className="mds-chat-quick-actions" aria-label="Quick concierge actions">
          {quickActions.map((action) => (
            <button
              key={action.label}
              type="button"
              disabled={state.isLoading}
              onClick={() => state.setCurrentPrompt(action.prompt)}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
