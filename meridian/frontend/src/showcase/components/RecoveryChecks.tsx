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
const RUNS_AFTER_RESUME = 'Runs after you resume';
const AVAILABILITY_NODE = /^Workflow node: availability/;
const field = (span: ShowcaseTraceSpan | undefined, label: string) =>
  span?.fields.find(item => item.label === label)?.value;

/** The latest hold step: a Gateway call, or a resumed step that found the hold its saved
 *  request had already placed, and so made no call for Cedar to decide. */
function policyCheck(span: ShowcaseTraceSpan | undefined, paused: boolean): Check {
  const check = { id: 'policy', title: 'Place hold' };
  const foundIn = field(span, 'reconciled_from');
  if (span && foundIn) {
    return {
      ...check, status: 'Already placed', tone: 'observed',
      description: 'The resumed step found the hold its saved request had already placed in '
        + 'Amazon Aurora, so it did not call the Gateway again. The saved booking proves the '
        + 'hold committed. This recovery trace does not independently prove the earlier '
        + 'policy decision; correlate the action audit with the service logs.',
      facts: [['Found in', foundIn], ['Booking', field(span, 'hold_id') || 'Not returned'],
        ['Source', 'Current recovery workflow trace']],
    };
  }
  const decision = field(span, 'cedar_decision');
  const identityRefused = field(span, 'refused_by') === 'identity_check';
  const reason = field(span, 'gateway_error');
  const facts: [string, string][] = span ? [
    ...(identityRefused ? [['Refused by', 'Gateway identity check'] as [string, string]] : [
      ['Configured policy', field(span, 'configured_policy') || field(span, 'cedar_policy') || 'Not returned'],
      ['Mode', field(span, 'policy_mode') || 'Not returned'],
    ] as [string, string][]),
    ['Source', field(span, 'policy_evidence') || 'Application recovery trace'],
    ...(reason ? [['Returned reason', reason] as [string, string]] : []),
  ] : [];
  if (decision === 'allow' && span?.status === 'ok') {
    if (field(span, 'policy_mode') === 'ENFORCE') {
      return { ...check, status: 'Cedar allowed', tone: 'observed', facts,
        description: 'The Gateway runs a target only after Cedar permits the call, so the target\'s result shows Cedar allowed it. Inspect the action audit and native service logs to verify the commit.' };
    }
    return { ...check, status: 'Target returned a result', tone: 'observed', facts,
      description: 'The application infers permission from the target response. This is not a service policy receipt. Inspect the action audit and native service logs to verify the decision and commit.' };
  }
  if (decision === 'deny' && span?.status === 'denied') {
    return { ...check, status: 'Cedar denied', tone: 'denied', facts,
      description: 'The gateway refused this hold call before the target ran. The returned reason names the layer that refused it; change the request before trying again.' };
  }
  if (identityRefused) {
    return { ...check, status: 'Identity check refused', tone: 'denied', facts,
      description: 'The Gateway could not confirm who is signed in, so it refused this hold call before '
        + 'Cedar or the hold target ran. Sign in again, then resume.' };
  }
  if (paused && !span) {
    return { ...check, status: RUNS_AFTER_RESUME, tone: 'pending', facts,
      description: 'The workflow is paused at its saved step, so no hold has been requested. '
        + 'This check runs after you resume.' };
  }
  return { ...check, status: 'Decision unavailable', tone: 'pending', facts,
    description: 'Cedar checks confirmation, party size, hold duration and saved budget. No decision is reported for the latest hold attempt.' };
}

/** The places check: the availability step the trace recorded for this recovery. */
function placesCheck(span: ShowcaseTraceSpan | undefined, paused: boolean): Check {
  const check = { id: 'places', title: 'Check places' };
  if (span) {
    const checks = field(span, 'checks') || '0';
    return {
      ...check, status: `${checks} ${checks === '1' ? 'option' : 'options'} checked`, tone: 'observed',
      description: 'The workflow checked which trip lengths still have places for the party, for '
        + 'the top options, in Amazon Aurora catalog inventory. A check does not hold inventory; '
        + 'the hold is the next step.',
      facts: [['Options checked', checks], ['Rows read', field(span, 'rows') || '0'],
        ['Source', 'Current recovery workflow trace']],
    };
  }
  if (paused) {
    return { ...check, status: RUNS_AFTER_RESUME, tone: 'pending', facts: [],
      description: 'The workflow is paused at its saved step, so places are not checked yet. '
        + 'This check runs after you resume.' };
  }
  return { ...check, status: 'Not checked yet', tone: 'pending', facts: [],
    description: 'Places are checked after the review, for the top three options.' };
}

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
  const snapshot = document && isObserved(document.snapshot) ? document.snapshot : null;
  const hold = document && isObserved(document.hold) ? document.hold : null;
  // Use the latest hold attempt, including a transport error with no decision.
  const policySpan = current ? [...state.traceSpans].reverse().find(span =>
    field(span, 'gateway_tool') === 'MeridianHolds___create_courtesy_hold'
      || field(span, 'reconciled_from'),
  ) : undefined;
  const paused = current && state.workflowStatus === 'paused';
  const placesSpan = current ? [...state.traceSpans].reverse().find(span =>
    AVAILABILITY_NODE.test(span.name) && span.status === 'ok') : undefined;
  const receiptWaiting = paused && !hold;
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
      id: 'snapshot', title: 'Review and confirm',
      status: snapshot ? document?.snapshot_store.durable ? 'Saved outside the worker' : 'Worker memory only' : 'Not yet verified',
      tone: snapshot ? 'observed' : 'pending',
      description: snapshot
        ? document?.snapshot_store.durable
          ? 'The journey record shows the step was saved outside the worker. A replacement worker can resume after the previous lease clears.'
          : 'The saved step lives in worker memory only; restart recovery is not established.'
        : 'A saved shortlist needs a saved step before it can resume. Open System evidence to inspect the active journey.',
      facts: snapshot ? [['Saved step', snapshot.snapshot_id], ['Thread', snapshot.thread_id], ['Source', snapshot.source]] : [],
    },
    placesCheck(placesSpan, paused),
    policyCheck(policySpan, paused),
    {
      id: 'hold', title: 'Show results',
      status: hold ? hold.status === 'held' ? 'Hold recorded' : hold.status === 'confirmed' ? 'Booking confirmed' : `Recorded: ${hold.status}`
        : receiptWaiting ? RUNS_AFTER_RESUME : 'Receipt unavailable',
      tone: hold ? 'observed' : 'pending',
      description: hold
        ? 'The active journey returned this booking record. Its recorded status and expiry determine the next step.'
        : receiptWaiting
          ? 'The workflow is paused at its saved step, so there is no booking receipt yet. '
            + 'This check runs after you resume.'
          : 'A completed workflow or permitted call does not prove a hold. Check the active journey for its booking receipt.',
      facts: hold ? [
        ['Booking', hold.booking_id],
        ...(hold.package_id ? [['Package', hold.package_id] as [string, string]] : []),
        ['Hold records', String(hold.hold_records)],
        ['Source', hold.source],
      ] : [],
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
