import { ArrowRight, ChevronDown } from 'lucide-react';
import { useState } from 'react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { isObserved, type JourneyDocument } from '../journey/types';
import type { ShowcaseTraceSpan } from '../lib/showcaseAdapters';

type Check = {
  id: string;
  title: string;
  status: string;
  tone: 'observed' | 'pending' | 'denied';
  description: string;
  facts: [string, string][];
};
const field = (span: ShowcaseTraceSpan | undefined, label: string) =>
  span?.fields.find(item => item.label === label)?.value;

/** Status is backed by records or structured fields, never assistant prose. */
export function RecoveryChecks({ state, journeyDocument, onOpenProof }: {
  state: MeridianShowcaseState;
  journeyDocument?: JourneyDocument | null;
  onOpenProof: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const current = state.selectedPhase === 5;
  const document = current && state.conversationId && journeyDocument?.active_thread_id === state.conversationId
    ? journeyDocument : null;
  const checkpoint = document && isObserved(document.checkpoint) ? document.checkpoint : null;
  const hold = document && isObserved(document.hold) ? document.hold : null;
  // Use the latest hold attempt, including a transport error with no decision.
  const policySpan = current ? [...state.traceSpans].reverse().find(span =>
    field(span, 'gateway_tool') === 'MeridianHolds___create_courtesy_hold',
  ) : undefined;
  const decision = field(policySpan, 'cedar_decision');
  const allowed = decision === 'allow' && policySpan?.status === 'ok';
  const denied = decision === 'deny' && policySpan?.status === 'denied';
  const recommendations = current ? state.recommendations ?? [] : [];
  const checks: Check[] = [
    {
      id: 'shortlist', title: 'Find alternatives',
      status: recommendations.length ? `${recommendations.length} returned` : 'Awaiting search',
      tone: recommendations.length ? 'observed' : 'pending',
      description: recommendations.length
        ? 'Returned candidates for this recovery. Ranking alone does not reserve inventory.'
        : 'Start recovery to search the catalog using the request and traveler context.',
      facts: recommendations.length ? [['Leading option', recommendations[0].name], ['Source', 'Current recovery response']] : [],
    },
    {
      id: 'checkpoint', title: 'Save progress',
      status: checkpoint ? document?.checkpoint_backend.durable ? 'Durable checkpoint' : 'In-process checkpoint' : 'Not yet verified',
      tone: checkpoint ? 'observed' : 'pending',
      description: checkpoint
        ? document?.checkpoint_backend.durable
          ? 'The journey record reports a durable checkpoint. A replacement worker can resume after the previous lease clears.'
          : 'The observed checkpoint is in process memory; restart recovery is not established.'
        : 'A saved shortlist needs a checkpoint record. Open System evidence to inspect the active journey.',
      facts: checkpoint ? [['Checkpoint', checkpoint.checkpoint_id], ['Thread', checkpoint.thread_id], ['Source', checkpoint.source]] : [],
    },
    {
      id: 'policy', title: 'Check the hold',
      status: allowed ? 'Cedar allowed' : denied ? 'Cedar denied' : 'Decision unavailable',
      tone: allowed ? 'observed' : denied ? 'denied' : 'pending',
      description: allowed
        ? 'The gateway permitted this hold call. Inspect the booking record separately to verify the write.'
        : denied ? 'The gateway refused this hold call before the target ran. Review the reason before changing the request.'
          : 'Cedar checks confirmation, party size, hold duration and saved budget. No decision is reported for the latest hold attempt.',
      facts: policySpan ? [
        ['Policy', field(policySpan, 'cedar_policy') || 'Not returned'],
        ['Mode', field(policySpan, 'policy_mode') || 'Not returned'],
        ['Source', 'Current recovery gateway trace'],
        ...(field(policySpan, 'gateway_error') ? [['Returned reason', field(policySpan, 'gateway_error')!] as [string, string]] : []),
      ] : [],
    },
    {
      id: 'hold', title: 'Verify the receipt',
      status: hold ? hold.status === 'held' ? 'Hold recorded' : hold.status === 'confirmed' ? 'Booking confirmed' : `Recorded: ${hold.status}` : 'Receipt unavailable',
      tone: hold ? 'observed' : 'pending',
      description: hold
        ? 'The active journey returned this booking record. Its recorded status and expiry determine the next step.'
        : 'A completed workflow or permitted call does not prove a hold. Check the active journey for its booking receipt.',
      facts: hold ? [['Booking', hold.booking_id], ['Hold records', String(hold.hold_records)], ['Source', hold.source]] : [],
    },
  ];
  const active = checks.find(check => check.id === selected);
  return <section className="mc-recovery-checks" aria-label="Recovery checks">
    <div className="mc-recovery-checks-head"><h2>From request to a recorded result</h2><span>Inspect each step</span></div>
    <ol className="mc-recovery-check-list">{checks.map((check, index) => <li key={check.id}>
      <button type="button" className={`is-${check.tone}${selected === check.id ? ' is-selected' : ''}`}
        aria-expanded={selected === check.id} aria-controls={`recovery-check-${check.id}`}
        onClick={() => setSelected(selected === check.id ? null : check.id)}>
        <span className="mc-recovery-check-index" aria-hidden="true">{index + 1}</span>
        <span><strong>{check.title}</strong><small>{check.status}</small></span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>
    </li>)}</ol>
    {checks.map(check => <div key={check.id} id={`recovery-check-${check.id}`} hidden={active?.id !== check.id} className="mc-recovery-check-detail">
      <p>{check.description}</p>
      {check.facts.length > 0 && <dl>{check.facts.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>}
      <button type="button" onClick={onOpenProof}>Open System evidence <ArrowRight size={16} aria-hidden="true" /></button>
    </div>)}
  </section>;
}
