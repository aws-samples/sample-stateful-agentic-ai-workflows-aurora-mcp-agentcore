import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import type { JourneyDocument } from '../../journey/types';
import type { ShowcaseTraceSpan } from '../../lib/showcaseAdapters';
import { RecoveryChecks } from '../RecoveryChecks';

function state(overrides: Partial<MeridianShowcaseState> = {}) {
  return { selectedPhase: 5, conversationId: 'thread-current', recommendations: [], traceSpans: [], ...overrides } as MeridianShowcaseState;
}
function span(decision: string, status: string): ShowcaseTraceSpan {
  return { id: decision, name: 'Hold', type: 'tool', category: 'gateway', status, latencyMs: null, fields: [
    { label: 'gateway_tool', value: 'MeridianHolds___create_courtesy_hold' },
    { label: 'cedar_decision', value: decision },
    { label: 'cedar_policy', value: 'meridian_hold_governance' },
    { label: 'policy_mode', value: 'ENFORCE' },
  ] };
}
function journey(thread = 'thread-current') {
  return {
    active_thread_id: thread, snapshot_store: { kind: 'Aurora workflow_snapshots', durable: true },
    snapshot: { status: 'observed', source: 'Aurora', snapshot_id: 'snapshot-1', thread_id: thread },
    hold: { status: 'held', source: 'Aurora', booking_id: 'booking-1', hold_records: 1 },
  } as JourneyDocument;
}

describe('RecoveryChecks evidence boundaries', () => {
  it('does not turn model narration or a completed workflow into policy or hold proof', () => {
    render(<RecoveryChecks state={state({ workflowStatus: 'complete', messages: [{ role: 'bot', text: 'Cedar allowed the hold and saved it in Aurora.' }] })} onOpenProof={vi.fn()} />);
    expect(screen.getByText('Decision unavailable')).toBeVisible();
    expect(screen.getByText('Receipt unavailable')).toBeVisible();
    expect(screen.getByText('Not yet verified')).toBeVisible();
  });
  it('shows the observed denial and opens the real evidence surface', () => {
    const onOpenProof = vi.fn();
    render(<RecoveryChecks state={state({ traceSpans: [span('deny', 'denied')] })} onOpenProof={onOpenProof} />);
    fireEvent.click(screen.getByRole('button', { name: /Place hold/ }));
    expect(screen.getByText('Cedar denied')).toBeVisible();
    expect(screen.getByText('meridian_hold_governance')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Open System evidence' }));
    expect(onOpenProof).toHaveBeenCalledOnce();
  });
  it('shows the fixed reason the backend returns for a Cedar denial', () => {
    const denied = span('deny', 'denied');
    denied.fields.push({ label: 'gateway_error', value: 'the Gateway policy denied the call' });
    render(<RecoveryChecks state={state({ traceSpans: [denied] })} onOpenProof={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Place hold/ }));
    expect(screen.getByText('the Gateway policy denied the call')).toBeVisible();
    expect(screen.getByText(/The returned reason names the layer that refused it/)).toBeVisible();
  });
  it('reports an identity-check refusal as such, never as a Cedar decision', () => {
    const refused: ShowcaseTraceSpan = {
      id: 'identity', name: 'Workflow node: hold not placed', type: 'security', category: 'security',
      status: 'denied', latencyMs: null, component: 'AgentCore Gateway request interceptor', fields: [
        { label: 'gateway_tool', value: 'MeridianHolds___create_courtesy_hold' },
        { label: 'gateway_error', value: 'the Gateway identity check refused the call (no_token)' },
        { label: 'refused_by', value: 'identity_check' },
      ],
    };
    render(<RecoveryChecks state={state({ traceSpans: [refused] })} onOpenProof={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Place hold/ }));
    expect(screen.getByText('Identity check refused')).toBeVisible();
    expect(screen.queryByText('Cedar denied')).not.toBeInTheDocument();
    expect(screen.getByText(/before Cedar or the hold target ran/)).toBeVisible();
    expect(screen.getByText('the Gateway identity check refused the call (no_token)')).toBeVisible();
    expect(screen.queryByText('meridian_hold_governance')).not.toBeInTheDocument();
  });
  it('says a resumed step found its earlier hold instead of reporting a missing decision', () => {
    const found: ShowcaseTraceSpan = {
      id: 'found', name: 'Workflow node: hold', type: 'database', category: 'database',
      status: 'ok', latencyMs: null, component: 'Aurora hold_requests', fields: [
        { label: 'hold_id', value: 'booking-1' },
        { label: 'replayed', value: 'yes' },
        { label: 'reconciled_from', value: 'Aurora hold_requests' },
      ],
    };
    render(<RecoveryChecks state={state({ workflowStatus: 'resumed', traceSpans: [found] })}
      journeyDocument={journey()} onOpenProof={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Place hold/ }));
    expect(screen.getByText('Already placed')).toBeVisible();
    expect(screen.queryByText('Decision unavailable')).not.toBeInTheDocument();
    expect(screen.queryByText('Target returned a result')).not.toBeInTheDocument();
    expect(screen.getByText(/did not call the Gateway again/)).toBeVisible();
    expect(screen.getByText('Aurora hold_requests')).toBeVisible();
    expect(within(document.getElementById('recovery-check-policy')!).getByText('booking-1')).toBeVisible();
    expect(screen.getByText('Hold recorded')).toBeVisible();
  });
  it('does not reuse a prior allow when the latest attempt has no decision', () => {
    render(<RecoveryChecks state={state({ traceSpans: [span('allow', 'ok'), span('', 'error')] })} onOpenProof={vi.fn()} />);
    expect(screen.getByText('Decision unavailable')).toBeVisible();
    expect(screen.queryByText('Target returned a result')).not.toBeInTheDocument();
  });
  it('keeps policy permission separate from a persisted hold', () => {
    render(<RecoveryChecks state={state({ traceSpans: [span('allow', 'ok')] })} onOpenProof={vi.fn()} />);
    expect(screen.getByText('Target returned a result')).toBeVisible();
    expect(screen.getByText('Receipt unavailable')).toBeVisible();
  });
  it('names the package the recorded hold is for', () => {
    const document = journey();
    document.hold = { ...document.hold, package_id: 'CTY-002' } as JourneyDocument['hold'];
    render(<RecoveryChecks state={state()} journeyDocument={document} onOpenProof={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Show results/ }));
    expect(screen.getByText('Package')).toBeVisible();
    expect(screen.getByText('CTY-002')).toBeVisible();
  });
  it('uses matching journey records and discards a previous thread or phase', () => {
    const { rerender } = render(<RecoveryChecks state={state()} journeyDocument={journey()} onOpenProof={vi.fn()} />);
    expect(screen.getByText('Saved outside the worker')).toBeVisible();
    expect(screen.getByText('Hold recorded')).toBeVisible();
    rerender(<RecoveryChecks state={state()} journeyDocument={journey('thread-old')} onOpenProof={vi.fn()} />);
    expect(screen.getByText('Receipt unavailable')).toBeVisible();
    expect(screen.getByText('Not yet verified')).toBeVisible();
    rerender(<RecoveryChecks state={state({ selectedPhase: 4, traceSpans: [span('allow', 'ok')] })} journeyDocument={journey()} onOpenProof={vi.fn()} />);
    expect(screen.getByText('Decision unavailable')).toBeVisible();
    expect(screen.getByText('Receipt unavailable')).toBeVisible();
  });
  it('says the hold steps run after resume while the workflow is paused', () => {
    render(<RecoveryChecks state={state({ workflowStatus: 'paused' })} onOpenProof={vi.fn()} />);
    expect(screen.getAllByText('Runs after you resume')).toHaveLength(3);
    expect(screen.queryByText('Decision unavailable')).not.toBeInTheDocument();
    expect(screen.queryByText('Receipt unavailable')).not.toBeInTheDocument();
  });
  it('keeps an observed decision visible while the workflow is paused', () => {
    render(<RecoveryChecks state={state({ workflowStatus: 'paused', traceSpans: [span('allow', 'ok')] })}
      onOpenProof={vi.fn()} />);
    expect(screen.getByText('Target returned a result')).toBeVisible();
    expect(screen.getAllByText('Runs after you resume')).toHaveLength(2);
  });
  it('lists the five recovery steps in the order the talk names them', () => {
    render(<RecoveryChecks state={state()} onOpenProof={vi.fn()} />);
    const list = screen.getByRole('list');
    expect(Array.from(list.querySelectorAll('strong'), item => item.textContent)).toEqual([
      'Find alternatives', 'Review and confirm', 'Check places', 'Place hold', 'Show results',
    ]);
  });
  it('reports the places check from the availability step the trace recorded', () => {
    const checked: ShowcaseTraceSpan = {
      id: 'availability', name: 'Workflow node: availability fan-out', type: 'tool_call',
      category: 'orchestration', status: 'ok', latencyMs: 54, fields: [
        { label: 'node', value: 'availability' }, { label: 'checks', value: '3' },
        { label: 'rows', value: '3' },
      ],
    };
    render(<RecoveryChecks state={state({ workflowStatus: 'resumed', traceSpans: [checked] })}
      onOpenProof={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Check places/ }));
    expect(screen.getByText('3 options checked')).toBeVisible();
    const detail = within(document.getElementById('recovery-check-places')!);
    expect(detail.getByText('Options checked')).toBeVisible();
    expect(detail.getByText(/does not hold inventory/)).toBeVisible();
  });
  it('does not report a places check before one is recorded', () => {
    render(<RecoveryChecks state={state()} onOpenProof={vi.fn()} />);
    expect(screen.getByText('Not checked yet')).toBeVisible();
  });
});
