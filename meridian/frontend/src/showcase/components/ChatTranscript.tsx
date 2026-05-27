import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';

export function ChatTranscript({ state, compact = false }: { state: MeridianShowcaseState; compact?: boolean }) {
  const visibleMessages = compact ? state.messages.slice(-3) : state.messages.slice(-6);

  return (
    <div className={`mds-chat-transcript${compact ? ' is-compact' : ''}`} aria-live="polite">
      {visibleMessages.length === 0 ? (
        <div className="mds-empty">Start with a trip idea to wake up the concierge.</div>
      ) : (
        visibleMessages.map((message, index) => (
          <div key={`${message.role}-${index}-${message.text.slice(0, 12)}`} className={`mds-message ${message.role}`}>
            <div className="mds-message-role">{message.role === 'user' ? 'Alex' : 'Meridian'}</div>
            <div className="mds-message-bubble">{message.text}</div>
          </div>
        ))
      )}
      {state.isLoading && (
        <div className="mds-message bot">
          <div className="mds-message-role">Meridian</div>
          <div className="mds-message-bubble">
            <span className="mds-running-dot" />
            Running tools and composing...
          </div>
        </div>
      )}
    </div>
  );
}
