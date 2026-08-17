/**
 * SpanInspector - drawer that opens when an audience-level trace row is
 * clicked. Shows input, output, latency, tokens, SQL/tool/model, and related
 * Aurora table.
 */
import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { useRef } from 'react';
import type { StageSpan } from '../types';

interface SpanInspectorProps {
  span: StageSpan | null;
  onClose: () => void;
}

function relatedTableForSpan(span: StageSpan): string | null {
  if (span.kind === 'memory') return 'traveler_preferences';
  if (span.kind === 'data') return 'trip_packages / bookings';
  if (span.kind === 'tool') return span.source ?? 'mcp.tools';
  if (span.kind === 'synthesis') return 'agent_traces';
  if (span.kind === 'security') return 'agent_audit_log';
  if (span.kind === 'model') return 'bedrock.model';
  return null;
}

export function SpanInspector({ span, onClose }: SpanInspectorProps) {
  const returnFocusRef = useRef<HTMLElement | null>(null);

  if (!span) return null;

  const related = relatedTableForSpan(span);
  const restoreFocus = () => {
    const target = returnFocusRef.current;
    if (!target) return;
    queueMicrotask(() => target.focus());
  };

  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) {
          onClose();
          restoreFocus();
        }
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="ds-inspector-backdrop" />
        <Dialog.Content
          className="ds-inspector"
          asChild
          onOpenAutoFocus={() => {
            returnFocusRef.current = document.activeElement as HTMLElement;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            restoreFocus();
          }}
        >
          <aside>
            <header className="ds-inspector-head">
              <div>
                <Dialog.Title className="ds-inspector-title">
                  {span.name}
                </Dialog.Title>
                <Dialog.Description className="ds-inspector-sub">
                  {span.component ?? span.source ?? span.kind} / {span.latencyMs}ms
                </Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <button
                  type="button"
                  className="ds-inspector-close"
                  aria-label="Close inspector"
                >
                  <X size={15} aria-hidden="true" />
                </button>
              </Dialog.Close>
            </header>

            <div className="ds-inspector-body">
              <div className="ds-inspector-meta">
                <div>
                  <span>Latency</span>
                  <b>{span.latencyMs}ms</b>
                </div>
                <div>
                  <span>Status</span>
                  <b>{span.status ?? 'ok'}</b>
                </div>
                <div>
                  <span>Tokens</span>
                  <b>
                    {span.tokensIn != null || span.tokensOut != null
                      ? `${span.tokensIn ?? '-'} / ${span.tokensOut ?? '-'}`
                      : '-'}
                  </b>
                </div>
              </div>

              <div className="ds-inspector-section">
                <h4>Input</h4>
                <pre className="ds-inspector-code">{span.input ?? '-'}</pre>
              </div>

              <div className="ds-inspector-section">
                <h4>Output</h4>
                <pre className="ds-inspector-code">{span.output ?? '-'}</pre>
              </div>

              <div className="ds-inspector-section">
                <h4>Source</h4>
                <pre className="ds-inspector-code">
                  {span.source ?? span.component ?? '-'}
                </pre>
              </div>

              {related && (
                <div className="ds-inspector-section">
                  <h4>Related</h4>
                  <pre className="ds-inspector-code">{related}</pre>
                </div>
              )}
            </div>
          </aside>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
