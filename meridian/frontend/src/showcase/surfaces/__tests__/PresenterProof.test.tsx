/**
 * Presenter proof renders evidence, and says so when there is none.
 *
 * The surface exists to make a claim checkable, so the one thing it must never
 * do is present a plausible value it did not read. Absent evidence has to look
 * absent.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PresenterProof } from '../PresenterProof';
import type { JourneyDocument } from '../../journey/types';

const unavailable = (reason: string) => ({ status: 'unavailable' as const, reason });

function makeDocument(overrides: Partial<JourneyDocument> = {}): JourneyDocument {
  return {
    journey_id: 'jrn_test',
    traveler_id: 'trv_meridian_demo',
    status: 'active',
    checkpoint_backend: { kind: 'AuroraDataApiSaver', durable: true },
    active_thread_id: 'thread_test',
    executions: {
      status: 'observed',
      source: 'journey_executions',
      items: [
        {
          execution_id: 'exe_01',
          attempt: 1,
          worker_id: 'worker_01',
          status: 'abandoned',
          started_at: null,
          ended_at: null,
          lease_expires_at: null,
        },
        {
          execution_id: 'exe_02',
          attempt: 2,
          worker_id: 'worker_02',
          status: 'running',
          started_at: null,
          ended_at: null,
          lease_expires_at: null,
        },
      ],
    },
    checkpoint: {
      status: 'committed',
      source: 'checkpoints',
      thread_id: 'thread_test',
      checkpoint_id: 'cp_01',
      parent_checkpoint_id: null,
      checkpoint_ns: '',
      committed_at: '2026-09-06T02:13:41+00:00',
    },
    selected_plan: {
      status: 'observed',
      source: 'checkpoint:x#y',
      package_id: 'TKY-003',
    },
    recommendations: unavailable('none'),
    pending_decision: unavailable('none'),
    conversation: unavailable('none'),
    hold: {
      status: 'held',
      source: 'hold_requests + bookings',
      label: 'one hold for this request',
      hold_request_id: 'hrq_01',
      booking_id: 'BKG-1',
      created_by_execution_id: 'exe_02',
      hold_expires_at: null,
      package_id: 'TKY-003',
      duration: '3 nights',
      travelers_count: 2,
      hold_records: 1,
    },
    authorization: unavailable('none'),
    rls: unavailable('none'),
    ...overrides,
  } as JourneyDocument;
}

const noop = () => {};

describe('Presenter proof', () => {
  it('labels the retained observation when a refresh fails', () => {
    render(<PresenterProof document={makeDocument()} loading={false}
      error="Connection interrupted" onRefresh={noop} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Showing the last successful observation');
    expect(screen.getByRole('alert')).toHaveTextContent('Connection interrupted');
  });

  it('reads the headline off whether a worker was actually replaced', () => {
    render(
      <PresenterProof document={makeDocument()} loading={false} error={null} onRefresh={noop} />,
    );
    expect(screen.getByText('The checkpoint remains.')).toBeInTheDocument();
  });

  it('names both the stopped worker and the one holding the lease', () => {
    render(
      <PresenterProof document={makeDocument()} loading={false} error={null} onRefresh={noop} />,
    );
    expect(screen.getByText('worker_01')).toBeInTheDocument();
    expect(screen.getByText('worker_02')).toBeInTheDocument();
    expect(screen.getByText(/attempt 1 · abandoned/)).toBeInTheDocument();
    expect(screen.getByText(/attempt 2 · running/)).toBeInTheDocument();
  });

  it('distinguishes a hold created after replacement from a surviving hold', () => {
    render(
      <PresenterProof document={makeDocument()} loading={false} error={null} onRefresh={noop} />,
    );
    fireEvent.click(screen.getByRole('tab', { name: 'Business result' }));
    const panel = screen.getByRole('tabpanel');
    expect(within(panel).getByText('Replacement execution')).toBeInTheDocument();
    expect(within(panel).getByText(/does not yet prove an existing hold survived/)).toBeInTheDocument();
    expect(within(panel).getByText('1')).toBeInTheDocument();
  });

  it('reports the record count without treating distinct requests as duplicate holds', () => {
    const doc = makeDocument();
    render(
      <PresenterProof
        document={
          { ...doc, hold: { ...(doc.hold as object), hold_records: 2 } } as JourneyDocument
        }
        loading={false}
        error={null}
        onRefresh={noop}
      />,
    );
    fireEvent.click(screen.getByRole('tab', { name: 'Business result' }));
    expect(within(screen.getByRole('tabpanel')).getByText('2')).toBeInTheDocument();
  });

  it('shows a dash where the database holds no evidence', () => {
    render(
      <PresenterProof
        document={makeDocument({ checkpoint: unavailable('no checkpoint yet') })}
        loading={false}
        error={null}
        onRefresh={noop}
      />,
    );
    const panel = screen.getByRole('tabpanel');
    expect(within(panel).getAllByText('—').length).toBeGreaterThan(0);
    expect(screen.getByText('Awaiting checkpoint')).toBeInTheDocument();
  });

  it('explains itself when there is no journey at all', () => {
    const onRefresh = vi.fn();
    render(
      <PresenterProof
        document={null}
        loading={false}
        error="No journey has been recorded yet."
        onRefresh={onRefresh}
      />,
    );
    expect(screen.getByText('Journey evidence is unavailable.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Check again/ }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });
});


it('does not call an expired worker live or a second attempt a successful resume', () => {
  const doc = makeDocument();
  render(<PresenterProof document={doc} loading={false} error={null} onRefresh={noop} />);
  expect(screen.queryByText('Holding the lease')).not.toBeInTheDocument();
  expect(screen.getByText('Lease not verified')).toBeInTheDocument();
  expect(screen.getByText('Successful resume not verified')).toBeInTheDocument();
});
