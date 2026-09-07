import type { JourneyDocument } from '../../journey/types';
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMeridianShowcase } from '../useMeridianShowcase';
import { fetchHealth, fetchMemoryProfile, fetchProducts, processOrder, sendChatMessage } from '../../../api/client';

vi.mock('../../../api/client', () => ({
  fetchHealth: vi.fn(), fetchMemoryProfile: vi.fn(), fetchProducts: vi.fn(), sendChatMessage: vi.fn(),
  processOrder: vi.fn(), deleteMemoryFact: vi.fn(), updateMemoryFact: vi.fn(),
}));

vi.mock('../../lib/tripWorkspace', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/tripWorkspace')>()),
  loadTripWorkspace: () => ({ savedTrips: [], compareTrips: [] }),
  saveTripWorkspace: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'healthy' });
  vi.mocked(fetchMemoryProfile).mockResolvedValue({ traveler_id: 'trv_meridian_demo', profile: { home_airport: 'JFK' }, facts: [] });
  vi.mocked(fetchProducts).mockResolvedValue([]);
  vi.mocked(sendChatMessage).mockResolvedValue({ message: 'Here are your options.', conversation_id: 'production-thread', products: [], activities: [] });
});

describe('Concierge request context', () => {
  it('uses recalled context and the same production conversation without advancing the ladder', async () => {
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.previewProfile?.home_airport).toBe('JFK'));
    await act(async () => { await result.current.submitPrompt('Plan Tokyo', 4); });
    expect(sendChatMessage).toHaveBeenLastCalledWith(expect.objectContaining({ phase: 4, memory_enabled: true }), expect.any(AbortSignal));
    expect(result.current.selectedPhase).toBe(1);
    expect(result.current.memoryEnabled).toBe(false);
    await act(async () => { await result.current.submitPrompt('Make it quieter', 4); });
    expect(sendChatMessage).toHaveBeenLastCalledWith(expect.objectContaining({ conversation_id: 'production-thread', phase: 4 }), expect.any(AbortSignal));
  });

  it('keeps a normal SQL ladder request free of traveler memory', async () => {
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.previewProfile).not.toBeNull());
    await act(async () => { await result.current.submitPrompt('City trips under $2000'); });
    const request = vi.mocked(sendChatMessage).mock.calls[0][0];
    expect(request.phase).toBe(1);
    expect(request).not.toHaveProperty('customer_id');
    expect(request).not.toHaveProperty('memory_enabled');
    expect(request).not.toHaveProperty('conversation_id');
  });
});


it('uses the same party for estimates and holds before and after clearing per-turn filters', async () => {
  vi.mocked(fetchMemoryProfile).mockResolvedValue({ traveler_id: 'trv_meridian_demo', profile: { party_size: 2 }, facts: [] });
  vi.mocked(processOrder).mockResolvedValue({ message: 'Held', activities: [] });
  const { result } = renderHook(() => useMeridianShowcase());
  await waitFor(() => expect(result.current.travelersCount).toBe(2));
  const product = { product_id: 'tokyo', name: 'Tokyo', price: 100, brand: 'Meridian', category: 'city', description: '', image_url: '' };
  await act(async () => { await result.current.holdTrip(product); });
  expect(processOrder).toHaveBeenLastCalledWith(expect.objectContaining({ quantity: 2 }));
  act(() => result.current.setChatFilters({ ...result.current.chatFilters, travelers: 3 }));
  await act(async () => { await result.current.submitPrompt('Plan Tokyo', 5); });
  expect(sendChatMessage).toHaveBeenLastCalledWith(expect.objectContaining({ travelers_count: 3 }), expect.any(AbortSignal));
  expect(result.current.chatFilters.travelers).toBe(0);
  expect(result.current.travelersCount).toBe(3);
  await act(async () => { await result.current.holdTrip(product); });
  expect(processOrder).toHaveBeenLastCalledWith(expect.objectContaining({ quantity: 3 }));
});

it('retains a confirmed hold and workflow evidence without placing it again on reopen', async () => {
  const product = { product_id: 'tokyo', name: 'Tokyo', price: 100, brand: 'Meridian', category: 'city', description: '', image_url: '' };
  const order = { order_id: 'HLD-existing', items: [{ product_id: 'tokyo', name: 'Tokyo', quantity: 2, unit_price: 100 }], subtotal: 200, tax: 0, shipping: 0, total: 200, status: 'held', hold_expires_at: new Date(Date.now() + 43200000).toISOString() };
  vi.mocked(processOrder).mockResolvedValue({ message: 'Held', order, activities: [] });
  vi.mocked(sendChatMessage).mockResolvedValue({ message: 'Paused', workflow_status: 'paused', conversation_id: 'same-thread', activities: [{ id: 'saved', timestamp: new Date().toISOString(), activity_type: 'tool_call', title: 'Checkpoint · AuroraDataApiSaver.put', telemetry: { category: 'memory_short', component: 'AuroraDataApiSaver', status: 'ok', fields: [{ label: 'checkpoint_durable', value: 'true' }] } }] });
  const { result } = renderHook(() => useMeridianShowcase());
  await waitFor(() => expect(result.current.previewProfile).not.toBeNull());
  act(() => result.current.setSelectedPhase(5));
  await act(async () => { await result.current.submitPrompt('My flight was canceled. Rework the trip.', 5); });
  const checkpointTrace = result.current.traceSpans;
  await act(async () => { await result.current.holdTrip(product); });
  expect(result.current.traceSpans).toEqual(expect.arrayContaining(checkpointTrace));
  expect(result.current.conversationId).toBe('same-thread');
  act(() => result.current.closeTripDetails());
  act(() => result.current.openTripDetails(product));
  await act(async () => { await result.current.holdTrip(product); });
  expect(processOrder).toHaveBeenCalledTimes(1);
  act(() => result.current.clearChat());
  expect(result.current.tripHolds).toEqual([{ productId: 'tokyo', order }]);
});

it('keeps a late hold receipt without overwriting a new phase conversation', async () => {
  const product = { product_id: 'tokyo', name: 'Tokyo', price: 100, brand: 'Meridian', category: 'city', description: '', image_url: '' };
  let resolveHold!: (value: Awaited<ReturnType<typeof processOrder>>) => void;
  vi.mocked(processOrder).mockImplementation(() => new Promise(resolve => { resolveHold = resolve; }));
  const { result } = renderHook(() => useMeridianShowcase());
  await waitFor(() => expect(result.current.previewProfile).not.toBeNull());
  let pending!: Promise<void>;
  act(() => { pending = result.current.holdTrip(product); });
  act(() => result.current.setSelectedPhase(3));
  await act(async () => { await result.current.submitPrompt('A new question'); });
  const messages = result.current.messages;
  const traces = result.current.traceSpans;
  await act(async () => {
    resolveHold({ message: 'Old hold reply', activities: [], order: { order_id: 'HLD-late', items: [], subtotal: 100, tax: 0, shipping: 0, total: 100, status: 'held', hold_expires_at: new Date(Date.now() + 43200000).toISOString() } });
    await pending;
  });
  expect(result.current.tripHolds[0].order.order_id).toBe('HLD-late');
  expect(result.current.messages).toEqual(messages);
  expect(result.current.traceSpans).toEqual(traces);
});


it('restores a saved recovery and sends resume to its original thread', async () => {
  const { result } = renderHook(() => useMeridianShowcase());
  await waitFor(() => expect(result.current.previewProfile).not.toBeNull());
  const document = {
    traveler_id: result.current.travelerId, active_thread_id: 'saved-thread',
    workflow: { status: 'observed', source: 'checkpoint', conversation_id: 'saved-thread',
      query: 'My Tokyo flight was canceled. Rework the trip.', message: 'Shortlist saved.',
      workflow_status: 'paused', next_nodes: ['availability'], activities: [], travelers_count: 3 },
    recommendations: { status: 'observed', source: 'checkpoint', items: [] },
  } as unknown as JourneyDocument;
  act(() => result.current.restoreJourney(document));
  expect(result.current.selectedPhase).toBe(5);
  expect(result.current.workflowStatus).toBe('paused');
  expect(result.current.travelersCount).toBe(3);
  await act(async () => { await result.current.submitPrompt('Resume workflow from checkpoint'); });
  expect(sendChatMessage).toHaveBeenLastCalledWith(expect.objectContaining({ resume: true, conversation_id: 'saved-thread', travelers_count: 3 }), expect.any(AbortSignal));
});

it.each(['phase', 'clear'])('ignores late responses after %s and leaves newer loading state alone', async (action) => {
  const { result } = renderHook(() => useMeridianShowcase());
  await waitFor(() => expect(result.current.previewProfile).not.toBeNull());
  let resolveOld!: (value: Awaited<ReturnType<typeof sendChatMessage>>) => void;
  vi.mocked(sendChatMessage).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
  let oldRequest!: Promise<void>;
  act(() => { oldRequest = result.current.submitPrompt('Old SQL question'); });
  const oldSignal = vi.mocked(sendChatMessage).mock.calls[0][1];
  act(() => action === 'phase' ? result.current.setSelectedPhase(3) : result.current.clearChat());
  expect(oldSignal?.aborted).toBe(true);
  expect(result.current.isLoading).toBe(false);
  await act(async () => { await result.current.submitPrompt('Current question'); });
  await act(async () => { resolveOld({ message: 'Stale SQL result', activities: [], conversation_id: 'old-thread' }); await oldRequest; });
  expect(result.current.messages.map(message => message.text).join(' ')).not.toContain('Stale SQL result');
  expect(result.current.conversationId).toBe('production-thread');
  expect(result.current.isLoading).toBe(false);
});

const healthyService = {
  status: 'healthy', bedrock_model_id: 'configured-model',
  embedding_model_id: 'configured-embedding', checkpoint_backend: 'AuroraDataApiSaver',
};

describe('Live connection readiness', () => {
  it.each(['catalog', 'profile'] as const)('does not report live when the %s read fails', async (dependency) => {
    vi.mocked(fetchHealth).mockResolvedValue(healthyService);
    if (dependency === 'catalog') vi.mocked(fetchProducts).mockRejectedValueOnce(new Error('Unavailable'));
    else vi.mocked(fetchMemoryProfile).mockRejectedValueOnce(new Error('Unavailable'));
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.connectionRefreshing).toBe(false));
    expect(result.current.backendStatus).toBe('offline');
    expect(result.current.connectionIssue).toContain('unavailable');
  });

  it('recovers failed opening reads without losing the current draft or enabling ladder memory', async () => {
    vi.mocked(fetchHealth).mockResolvedValue(healthyService);
    vi.mocked(fetchProducts).mockRejectedValueOnce(new Error('Session expired'));
    vi.mocked(fetchMemoryProfile).mockRejectedValueOnce(new Error('Session expired'));
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.connectionIssue).not.toBeNull());
    act(() => result.current.setCurrentPrompt('Keep my draft'));
    await act(async () => { await result.current.refreshConnection(); });
    expect(result.current.backendStatus).toBe('online');
    expect(result.current.connectionIssue).toBeNull();
    expect(result.current.previewProfile?.home_airport).toBe('JFK');
    expect(result.current.currentPrompt).toBe('Keep my draft');
    expect(result.current.memoryEnabled).toBe(false);
  });

  it('ignores a delayed failure from an older connection check', async () => {
    vi.mocked(fetchHealth).mockResolvedValue(healthyService);
    let rejectOld!: (reason: Error) => void;
    vi.mocked(fetchProducts).mockImplementationOnce(() => new Promise((_, reject) => { rejectOld = reject; }));
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(fetchProducts).toHaveBeenCalled());
    await act(async () => { await result.current.refreshConnection(); });
    expect(result.current.backendStatus).toBe('online');
    await act(async () => { rejectOld(new Error('Old failure')); });
    expect(result.current.backendStatus).toBe('online');
    expect(result.current.connectionIssue).toBeNull();
  });
});
