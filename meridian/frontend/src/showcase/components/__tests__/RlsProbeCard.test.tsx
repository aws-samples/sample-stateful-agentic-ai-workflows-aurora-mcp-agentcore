import { render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchRlsProbe, type RlsProbeResponse } from '../../../api/client';
import { RlsProbeCard } from '../RlsProbeCard';

vi.mock('../../../api/client', () => ({ fetchRlsProbe: vi.fn() }));

function probe(decision: RlsProbeResponse['negative_control']['decision']): RlsProbeResponse {
  return {
    traveler_id: 'trv_meridian_demo',
    authorization: {
      provider: 'cognito',
      subject_id: 'sub-1',
      principal: 'jordan',
      requested_traveler_id: 'trv_meridian_demo',
      decision: 'allow',
    },
    negative_control: {
      requested_traveler_id: 'trv_decoy',
      display_name: 'Decoy Traveler',
      decision,
      reason: 'no active identity binding',
    },
    tables: [],
    policies: [],
  };
}

async function negativeControlStep(decision: RlsProbeResponse['negative_control']['decision']) {
  vi.mocked(fetchRlsProbe).mockResolvedValue(probe(decision));
  render(<RlsProbeCard travelerId="trv_meridian_demo" />);
  const label = await screen.findByText('Negative control');
  const step = label.closest('.mds-authz-step') as HTMLElement;
  const text = decision === 'not_applicable' ? 'NOT APPLICABLE' : decision.toUpperCase();
  return { badge: within(step).getByText(text), step };
}

describe('RlsProbeCard negative control', () => {
  beforeEach(() => vi.mocked(fetchRlsProbe).mockReset());

  it('renders not_applicable with a neutral state, not the deny styling', async () => {
    const { badge, step } = await negativeControlStep('not_applicable');
    expect(badge).toHaveClass('is-neutral');
    expect(badge).not.toHaveClass('is-deny');
    expect(badge).not.toHaveClass('is-allow');
    expect(step).toHaveClass('is-neutral');
    expect(step).not.toHaveClass('is-deny');
  });

  it('keeps deny styling for DENY', async () => {
    const { badge, step } = await negativeControlStep('deny');
    expect(badge).toHaveClass('is-deny');
    expect(step).toHaveClass('is-deny');
  });

  it('keeps allow styling for ALLOW', async () => {
    const { badge, step } = await negativeControlStep('allow');
    expect(badge).toHaveClass('is-allow');
    expect(step).toHaveClass('is-allow');
    expect(step).not.toHaveClass('is-deny');
  });
});

describe('RlsProbeCard traveler', () => {
  beforeEach(() => {
    vi.mocked(fetchRlsProbe).mockReset();
    vi.mocked(fetchRlsProbe).mockResolvedValue({
      ...probe('deny'),
      traveler_id: 'trv_demo_decoy',
      authorization: { ...probe('deny').authorization, requested_traveler_id: 'trv_demo_decoy' },
    });
  });

  it('names the signed-in traveler in the grant step', async () => {
    render(<RlsProbeCard travelerId="trv_demo_decoy" travelerName="Jordan Lee" />);
    await waitFor(() => expect(screen.getByText('2. Traveler grant')).toBeInTheDocument());
    expect(screen.getByText('2. Traveler grant').parentElement).toHaveTextContent('Jordan Lee');
    expect(screen.queryByText(/Jordan Morgan/)).not.toBeInTheDocument();
  });

  it('probes the signed-in traveler when the page does not know the id', async () => {
    render(<RlsProbeCard />);
    await waitFor(() => expect(fetchRlsProbe).toHaveBeenCalledWith(undefined));
  });
});
