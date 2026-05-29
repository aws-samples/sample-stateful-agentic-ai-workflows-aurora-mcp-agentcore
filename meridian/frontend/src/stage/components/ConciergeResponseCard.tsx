/**
 * ConciergeResponseCard — natural-language reply card that lives at the
 * bottom of the trace hero panel.
 *
 * Reveal model:
 *   - before the model span fires → typing indicator (skeleton)
 *   - while the model span is active → token-by-token typewriter reveal
 *   - after the model span completes → full markdown-rendered reply
 *
 * The reply is rendered through react-markdown (same stack as the live
 * showcase) so **bold**, lists, and other markdown the concierge emits
 * format properly instead of showing literal asterisks. Text streams in
 * left-to-right at a ChatGPT-ish cadence so the kiosk reads as a live
 * compose, not a hard pop.
 *
 * Keeping the response inside the same panel as the trace is the keynote
 * payoff: the trace literally *produces* the reply the audience sees.
 */
import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { StageRecommendation } from '../types';

type ReplyPhase = 'pending' | 'composing' | 'composed';

interface ConciergeResponseCardProps {
  reply: string;
  reasoning: string;
  phase: ReplyPhase;
  primary?: StageRecommendation | null;
  /** Fired when the typewriter finishes revealing the reply, so the parent
   *  can reveal the product deck only after the stream completes. */
  onStreamComplete?: () => void;
}

// Strip emoji / pictographs the model may have slipped in — the product is
// premium-minimalist and the system prompt asks for none. Mirrors the
// showcase's stripEmojis so both surfaces render identically.
function stripEmojis(source: string): string {
  try {
    return source
      .replace(/[\p{Extended_Pictographic}\p{Emoji_Presentation}]/gu, '')
      .replace(/‍|️/g, '')
      .replace(/  +/g, ' ');
  } catch {
    return source;
  }
}

// Reveal text left-to-right at ~100 cps with a 6s ceiling, matching the
// showcase typewriter. When `enabled`, the reveal types the text out once
// (left-to-right, ~100 cps) and fires `onComplete` when it lands. When
// disabled, it shows the full text immediately and reports complete. The
// reveal keys off the text itself, so a reply that's already "composed"
// on first render still types out — the regression we hit when the phase
// jumped straight to composed because the model span was last.
function useTypewriterReveal(
  text: string,
  enabled: boolean,
  onComplete?: () => void,
): string {
  const [visible, setVisible] = useState(enabled ? text.slice(0, 2) : text);
  const doneRef = useRef(false);

  useEffect(() => {
    doneRef.current = false;
    if (!text) {
      setVisible('');
      return undefined;
    }
    if (!enabled) {
      // Not streaming yet — this is the pending phase, where the reply text
      // is already known but the trace hasn't reached the model span. Show
      // nothing (the card renders its skeleton here) and crucially do NOT
      // report complete: firing onComplete now would reveal the product deck
      // before the response has streamed in. The deck must land AFTER the
      // typewriter finishes, which only happens once `enabled` flips true.
      setVisible('');
      return undefined;
    }
    const seed = text.slice(0, Math.min(2, text.length));
    setVisible(seed);
    if (seed.length >= text.length) {
      onComplete?.();
      return undefined;
    }

    const stepMs = 30;
    const charsPerStep = 3;
    const ceilingMs = 6000;
    const finish = () => {
      if (doneRef.current) return;
      doneRef.current = true;
      setVisible(text);
      onComplete?.();
    };
    let cursor = seed.length;
    const id = window.setInterval(() => {
      cursor = Math.min(text.length, cursor + charsPerStep);
      setVisible(text.slice(0, cursor));
      if (cursor >= text.length) {
        window.clearInterval(id);
        finish();
      }
    }, stepMs);

    const naturalMs = Math.ceil(text.length / charsPerStep) * stepMs;
    const failsafe = window.setTimeout(() => {
      window.clearInterval(id);
      finish();
    }, Math.min(naturalMs + 200, ceilingMs));

    return () => {
      window.clearInterval(id);
      window.clearTimeout(failsafe);
    };
    // onComplete intentionally omitted — we only want to re-run on text change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, enabled]);

  return visible;
}

// While streaming, a half-revealed `**bold` shows a literal `**` until the
// closing marker is typed. On an always-on booth that flickers on every
// loop, so we close any dangling bold/italic marker for the partial string
// — the markdown renders balanced at every frame, and the real close just
// replaces our temporary one when it arrives.
function balanceMarkdown(partial: string): string {
  const boldCount = (partial.match(/\*\*/g) || []).length;
  let out = partial;
  if (boldCount % 2 === 1) out += '**';
  // Single-asterisk italics, ignoring the bold pairs already handled.
  const singles = (out.replace(/\*\*/g, '').match(/\*/g) || []).length;
  if (singles % 2 === 1) out += '*';
  return out;
}

export function ConciergeResponseCard({
  reply,
  reasoning,
  phase,
  primary,
  onStreamComplete,
}: ConciergeResponseCardProps) {
  const cleaned = stripEmojis(reply || '');
  // Type the reply out whenever we have text to show (composing OR
  // composed) — the player often jumps straight to composed when the model
  // span is last, so keying off phase alone skips the animation entirely.
  const shouldStream = phase !== 'pending' && cleaned.length > 0;
  const revealed = useTypewriterReveal(cleaned, shouldStream, onStreamComplete);
  // Balance dangling markdown markers until the full text has landed.
  const stillTyping = shouldStream && revealed.length < cleaned.length;
  const visible = stillTyping ? balanceMarkdown(revealed) : revealed;

  return (
    <div className={`ds-response phase-${phase}`} role="region" aria-label="Concierge response">
      <div className="ds-response-avatar" aria-hidden="true">
        <span>M</span>
      </div>

      <div className="ds-response-body">
        <div className="ds-response-eyebrow">
          <span className="ds-response-name">Meridian concierge</span>
          {phase === 'pending' && <span className="ds-response-status pending">awaiting trace</span>}
          {phase === 'composing' && (
            <span className="ds-response-status composing">
              <span className="ds-response-dot" /> streaming · claude.compose
            </span>
          )}
          {phase === 'composed' && (
            <span className="ds-response-status composed">composed · grounded reply</span>
          )}
        </div>

        {phase === 'pending' ? (
          <div className="ds-response-skeleton" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        ) : (
          <div className="ds-response-text ds-response-md">
            <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
              {visible || ' '}
            </ReactMarkdown>
          </div>
        )}

        <div className="ds-response-foot">
          <span className="ds-response-reasoning" title={reasoning}>
            <span className="ds-response-reasoning-label">trace reads</span>
            {reasoning}
          </span>
          {phase === 'composed' && primary && (
            <span className="ds-response-match">
              top match · <b>{primary.title}</b> · {primary.matchPct}% · ${primary.priceUsd.toLocaleString()}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
