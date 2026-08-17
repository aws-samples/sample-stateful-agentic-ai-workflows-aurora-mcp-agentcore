import {
  CheckCircle2,
  ChevronDown,
  Loader2,
  MessageSquareText,
} from 'lucide-react';
import { useEffect } from 'react';
import type { Message, Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import {
  deriveRecoveryEvidence,
  deriveRecoveryStage,
} from '../lib/recoveryState';
import { ShowcaseMarkdown } from './ChatTranscript';

function latestMessage(messages: Message[], role: Message['role']): Message | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === role) return messages[index];
  }
  return null;
}

function recoveryRequest(messages: Message[]): Message | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (
      message.role === 'user' &&
      /flight.+cancell?ed|cancell?ed.+flight/i.test(message.text)
    ) {
      return message;
    }
  }
  return latestMessage(messages, 'user');
}

function money(price: number): string {
  return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

function durationLabel(product: Product): string {
  return product.available_sizes?.[0] ?? 'Flexible';
}

export function RecoveryBriefing({
  state,
}: {
  state: MeridianShowcaseState;
}) {
  const userMessage = recoveryRequest(state.messages);
  const botMessage = latestMessage(state.messages, 'bot');
  const products =
    botMessage?.products?.length
      ? botMessage.products
      : state.recommendations ?? [];
  const topProduct = products[0] ?? null;
  const followUps = botMessage?.follow_ups ?? [];
  const recoveryStage = deriveRecoveryStage(state);
  const evidence = deriveRecoveryEvidence(state);
  const checkpointReady = recoveryStage === 'checkpointed';
  const visibleFollowUps = checkpointReady
    ? followUps.filter((prompt) => !/resume|checkpoint/i.test(prompt))
    : followUps;
  const { markLatestStreamComplete } = state;

  useEffect(() => {
    if (!state.isLoading && botMessage) markLatestStreamComplete();
  }, [botMessage, markLatestStreamComplete, state.isLoading]);

  return (
    <div className="mds-recovery-brief" aria-live="polite">
      {userMessage && (
        <div className="mds-recovery-request">
          <span>
            <MessageSquareText size={15} aria-hidden="true" />
            Request
          </span>
          <p title={userMessage.text}>{userMessage.text}</p>
        </div>
      )}

      {state.isLoading && (
        <div className="mds-recovery-brief-running" role="status">
          <Loader2 size={20} aria-hidden="true" />
          <span>
            <strong>Building the recovery plan</strong>
            <small>Ranking alternatives and saving workflow progress.</small>
          </span>
        </div>
      )}

      {!state.isLoading && botMessage && (
        <>
          {topProduct && (
            <div className="mds-recovery-outcome">
              <div className="mds-recovery-outcome-title">
                <CheckCircle2 size={20} aria-hidden="true" />
                <span>
                  <small>
                    {checkpointReady
                      ? 'Checkpoint ready'
                      : state.workflowStatus === 'resumed'
                        ? 'Recovery resumed'
                        : 'Best current match'}
                  </small>
                  <strong>{topProduct.name}</strong>
                </span>
              </div>
              <dl className="mds-recovery-outcome-facts">
                <div>
                  <dt>From</dt>
                  <dd>{money(topProduct.price)} / traveler</dd>
                </div>
                <div>
                  <dt>Duration</dt>
                  <dd>{durationLabel(topProduct)}</dd>
                </div>
                <div>
                  <dt>Options ranked</dt>
                  <dd>{products.length}</dd>
                </div>
              </dl>
              <p>
                {checkpointReady
                  ? 'The shortlist is saved. Resume to verify duration inventory for the leading options.'
                  : evidence.availabilityObserved
                    ? 'Duration inventory is verified for the leading options. Open the response for trade-offs and details.'
                    : 'Ranked first from the live recovery search. Open the response for trade-offs and availability details.'}
              </p>
            </div>
          )}

          <details className="mds-recovery-full-response">
            <summary>
              <span>Full agent response</span>
              <ChevronDown size={17} aria-hidden="true" />
            </summary>
            <ShowcaseMarkdown
              source={botMessage.text}
              highlightMemory={state.selectedPhase >= 4}
            />
          </details>

          {visibleFollowUps.length > 0 && (
            <div
              className="mds-recovery-brief-actions"
              aria-label="Suggested next steps"
            >
              {visibleFollowUps.slice(0, 3).map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => void state.submitPrompt(prompt)}
                >
                  {prompt}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
