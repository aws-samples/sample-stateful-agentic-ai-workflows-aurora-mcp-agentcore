import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Product } from '../../../types';
import { EMPTY_FILTERS, type MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import { DiscoveryWorkspace } from '../DiscoveryWorkspace';
import { ConciergeConversation } from '../ConciergeConversation';

const LIVE_PRODUCT: Product = {
  product_id: 'AUR-001',
  name: 'Aurora Live Fixture Trip',
  brand: 'Meridian',
  price: 1200,
  description: 'A live catalog row used only in this test.',
  image_url: '/travel/catalog/AUR-001.jpg',
  category: 'City Breaks',
  destination: 'Testville',
  region: 'Test Region',
  available_sizes: ['3 nights'],
  availability: { '3 nights': 4 },
  highlights: ['fixture'],
};

function makeState(overrides: Partial<MeridianShowcaseState> = {}): MeridianShowcaseState {
  return {
    messages: [],
    savedTrips: [],
    phaseExamples: [],
    currentPrompt: '',
    travelersCount: 2,
    recommendations: [],
    catalog: [],
    savedTripIds: new Set(),
    saveTrip: vi.fn(),
    openTripDetails: vi.fn(),
    travelerProfile: null,
    previewProfile: null,
    memoryFacts: [],
    previewFacts: [],
    isLoading: false,
    error: null,
    clearError: vi.fn(),
    backendStatus: 'checking',
    connectionIssue: null,
    connectionRefreshing: false,
    refreshConnection: vi.fn(),
    chatFilters: EMPTY_FILTERS,
    ...overrides,
  } as unknown as MeridianShowcaseState;
}

describe('DiscoveryWorkspace catalog states', () => {
  it('shows the skeleton while the live catalog has not resolved yet', () => {
    const state = makeState({ backendStatus: 'checking' });
    render(<DiscoveryWorkspace state={state} onClear={vi.fn()} greeting="morning" />);

    expect(screen.getByRole('status', { name: /loading/i })).toBeInTheDocument();
    expect(screen.queryByText(/Tuscany Wine & Wellness/i)).not.toBeInTheDocument();
  });

  it('shows an error naming the failure and a retry action when the fetch fails', () => {
    const refreshConnection = vi.fn();
    const state = makeState({
      backendStatus: 'offline',
      connectionIssue: 'Live trip data is unavailable.',
      refreshConnection,
    });
    render(<DiscoveryWorkspace state={state} onClear={vi.fn()} greeting="morning" />);

    expect(screen.getByRole('alert')).toHaveTextContent('Live trip data is unavailable.');
    fireEvent.click(screen.getByRole('button', { name: /check again/i }));
    expect(refreshConnection).toHaveBeenCalledTimes(1);
  });

  it('shows an empty state when the live catalog resolves with zero rows', () => {
    const state = makeState({ backendStatus: 'online', catalog: [] });
    render(<DiscoveryWorkspace state={state} onClear={vi.fn()} greeting="morning" />);

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('status', { name: /loading/i })).not.toBeInTheDocument();
    expect(screen.getByText(/nothing in the collection/i)).toBeInTheDocument();
  });

  it('shows the model badge on a reply a Bedrock model wrote', () => {
    const state = makeState({
      messages: [
        { role: 'user', text: 'Find a quiet retreat' },
        { role: 'bot', text: 'Here are some trips.', modelLabel: 'Claude Haiku 4.5' },
      ],
    });
    render(<ConciergeConversation state={state} onSaved={vi.fn()} onRecovery={vi.fn()} />);

    expect(screen.getByText('· Claude Haiku 4.5')).toBeInTheDocument();
  });

  it('shows no model badge on a pure tool-result reply', () => {
    const state = makeState({
      messages: [
        { role: 'user', text: 'What is the price range for Tokyo trips?' },
        { role: 'bot', text: 'Price range for Tokyo: low $1,199 · average $1,950 · high $3,299.' },
      ],
    });
    render(<ConciergeConversation state={state} onSaved={vi.fn()} onRecovery={vi.fn()} />);

    expect(screen.queryByText(/^· /)).not.toBeInTheDocument();
  });

  it('renders live catalog items once the fetch succeeds, never invented preview cards', () => {
    const state = makeState({ backendStatus: 'online', catalog: [LIVE_PRODUCT] });
    render(<DiscoveryWorkspace state={state} onClear={vi.fn()} greeting="morning" />);

    expect(screen.getByText('Aurora Live Fixture Trip')).toBeInTheDocument();
    expect(screen.queryByText(/Tuscany Wine & Wellness/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Tokyo Executive Stopover/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Tokyo Culture & Cuisine/i)).not.toBeInTheDocument();
  });
});


it('shows streamed catalog matches before completion with actions disabled', () => {
  const state = makeState({
    messages: [{ role: 'user', text: 'Find a trip' }], isLoading: true,
    streamingRecommendations: [LIVE_PRODUCT], backendStatus: 'online',
  });
  const { rerender } = render(<DiscoveryWorkspace state={state} onClear={vi.fn()} />);
  expect(screen.getByRole('heading', { name: LIVE_PRODUCT.name })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Explore this trip/ })).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('Checking the final details');
  rerender(<DiscoveryWorkspace state={{ ...state, isLoading: false, streamingRecommendations: [], recommendations: [] }} onClear={vi.fn()} />);
  expect(screen.queryByRole('heading', { name: LIVE_PRODUCT.name })).not.toBeInTheDocument();
  expect(screen.getByText('A different direction?')).toBeInTheDocument();
});
