import type { FormEvent } from 'react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';

export function ChatComposer({ state, compact = false }: { state: MeridianShowcaseState; compact?: boolean }) {
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void state.submitPrompt();
  };

  const quickActions = compact ? [] : state.phaseExamples.slice(0, 3);

  return (
    <div className={`mds-chat-composer-wrap${compact ? ' is-compact' : ''}`}>
      <form className={`mds-chat-composer${compact ? ' is-compact' : ''}`} onSubmit={onSubmit}>
        <input
          value={state.currentPrompt}
          onChange={(event) => state.setCurrentPrompt(event.target.value)}
          placeholder={`Ask Meridian (${state.phaseLabel})...`}
          disabled={state.isLoading}
          aria-label="Ask Meridian anything"
        />
        <button type="submit" disabled={state.isLoading || !state.currentPrompt.trim()} aria-label="Send message">
          {state.isLoading ? '...' : 'Send'}
        </button>
      </form>
      {quickActions.length > 0 && (
        <div className="mds-chat-quick-actions" aria-label="Quick concierge actions">
          {quickActions.map((prompt) => (
            <button
              key={prompt}
              type="button"
              disabled={state.isLoading}
              onClick={() => void state.applyPhaseExample(prompt)}
              title={prompt}
            >
              {prompt}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
