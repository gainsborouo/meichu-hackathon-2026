import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createController } from '../lib/controller';
import type { Outcome } from '../lib/backend';
import { RecommendationError } from '../lib/types';
import type { CheckoutRequest, Inspection, Recommendation } from '../lib/types';

const ready = (payable = 2580): Inspection => ({ status: 'ready', context: { platform: 'shopee', product: '耳機', payable, pageLocale: null } });
// Shaped like backend BestNow; sale_id carries the id so a test can tell two
// results apart the way user_card_id used to.
const result = (id: string): Recommendation => ({
  candidate_type: 'campaign',
  card: { id: 'c-1', bank_name: '玉山', name: 'Unicard' },
  sale_id: id,
  campaign_title: '一般消費回饋',
  estimated_reward_twd: 77.4,
  rate_display: '3%',
  cap_description: null,
  requires_registration: false,
  registration_url: null,
  reason: '一般消費回饋',
  verification_status: 'verified',
  official_sources: [],
});
beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it('debounces changes, deduplicates unchanged data and sends the checkout fields to background', async () => {
  let snapshot = ready();
  const request = vi.fn(async (value: CheckoutRequest) => ({ best: result(value.requestId), wait: null }));
  const controller = createController({ inspect: () => snapshot, request, render: vi.fn(), createId: () => 'id' });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(400);
  snapshot = ready(3000);
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(request).toHaveBeenCalledExactlyOnceWith({ requestId: 'id', platform: 'shopee', product: '耳機', payable: 3000, pageLocale: null });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(1000);
  expect(request).toHaveBeenCalledTimes(1);
  controller.dispose();
});

it.each([
  { status: 'outside' } as const,
  { status: 'no-credit-card', reason: 'credit-unavailable' } as const,
  { status: 'incomplete', reason: 'product-missing' } as const,
])('never requests when $status, including changes during debounce', async (unavailable) => {
  let snapshot: Inspection = unavailable;
  const request = vi.fn();
  const controller = createController({ inspect: () => snapshot, request, render: vi.fn() });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(1000);
  snapshot = ready();
  controller.refresh();
  snapshot = unavailable;
  await vi.advanceTimersByTimeAsync(1000);
  expect(request).not.toHaveBeenCalled();
  controller.dispose();
});

it('ignores responses from the previous amount and after credit disappears', async () => {
  let snapshot = ready();
  const pending: ((value: Outcome) => void)[] = [];
  const request = vi.fn(
    () => new Promise<Outcome>((resolve) => pending.push(resolve)),
  );
  const render = vi.fn();
  const controller = createController({ inspect: () => snapshot, request, render, createId: () => 'id' });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  snapshot = ready(3000);
  controller.refresh();
  pending[0]!({ best: result('id'), wait: null });
  await vi.advanceTimersByTimeAsync(700);
  expect(render.mock.calls.some(([state]) => state.status === 'success')).toBe(false);
  snapshot = { status: 'no-credit-card', reason: 'credit-unavailable' };
  pending[1]!({ best: result('id'), wait: null });
  await vi.advanceTimersByTimeAsync(0);
  expect(render).toHaveBeenLastCalledWith({ status: 'unavailable', reason: 'credit-unavailable' });
  controller.dispose();
});

it('reuses the same request after temporary DOM loss during a re-render', async () => {
  let snapshot: Inspection = ready();
  const request = vi.fn(async (value: CheckoutRequest) => ({ best: result(value.requestId), wait: null }));
  const controller = createController({ inspect: () => snapshot, request, render: vi.fn(), createId: () => 'id' });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  snapshot = { status: 'incomplete', reason: 'product-missing' };
  controller.refresh();
  snapshot = ready();
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(request).toHaveBeenCalledTimes(1);
  controller.dispose();
});

it('requests a new recommendation when the product changes at the same amount', async () => {
  let snapshot: Inspection = ready();
  let sequence = 0;
  const request = vi.fn(async (value: CheckoutRequest) => ({ best: result(value.requestId), wait: null }));
  const controller = createController({
    inspect: () => snapshot,
    request,
    render: vi.fn(),
    createId: () => `id-${++sequence}`,
  });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  snapshot = {
    status: 'ready',
    context: { platform: 'shopee', product: '保護殼', payable: 2580, pageLocale: null },
  };
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(request).toHaveBeenNthCalledWith(1, { requestId: 'id-1', platform: 'shopee', product: '耳機', payable: 2580, pageLocale: null });
  expect(request).toHaveBeenNthCalledWith(2, { requestId: 'id-2', platform: 'shopee', product: '保護殼', payable: 2580, pageLocale: null });
  controller.dispose();
});

it('supports retry after an error', async () => {
  let snapshot = ready();
  const render = vi.fn();
  const request = vi.fn(async (value: CheckoutRequest): Promise<Outcome> =>
    ({ best: result(value.requestId), wait: null }))
    .mockRejectedValueOnce(new Error('Offline'));
  const controller = createController({ inspect: () => snapshot, request, render });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(render.mock.lastCall?.[0].status).toBe('error');
  controller.retry();
  await vi.advanceTimersByTimeAsync(700);
  expect(render.mock.lastCall?.[0].status).toBe('success');
  expect(request).toHaveBeenCalledTimes(2);
  controller.dispose();
});

// --- the failure reason must reach the panel -------------------------------

it('passes the failure reason through to the panel state', async () => {
  const render = vi.fn();
  const controller = createController({
    inspect: () => ready(),
    request: async () => { throw new RecommendationError('no-cards', 'No card to recommend'); },
    render,
    createId: () => 'id',
  });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(render).toHaveBeenLastCalledWith(
    expect.objectContaining({ status: 'error', reason: 'no-cards' }),
  );
  controller.dispose();
});

it('labels a non-RecommendationError as a transient failure', async () => {
  const render = vi.fn();
  const controller = createController({
    inspect: () => ready(),
    request: async () => { throw new TypeError('Failed to fetch'); },
    render,
    createId: () => 'id',
  });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(render).toHaveBeenLastCalledWith(
    expect.objectContaining({ status: 'error', reason: 'failed' }),
  );
  controller.dispose();
});

it('retries a failed request instead of replaying the cached failure', async () => {
  // Signing in or adding a card must be able to succeed on the next attempt;
  // a cached rejection would keep showing the same message forever.
  const request = vi.fn()
    .mockRejectedValueOnce(new RecommendationError('no-cards', 'No card to recommend'))
    .mockResolvedValueOnce({ best: result('id'), wait: null });
  const render = vi.fn();
  const controller = createController({
    inspect: () => ready(), request, render, createId: () => 'id',
  });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(render).toHaveBeenLastCalledWith(
    expect.objectContaining({ status: 'error', reason: 'no-cards' }),
  );
  controller.retry();
  await vi.advanceTimersByTimeAsync(700);
  expect(request).toHaveBeenCalledTimes(2);
  expect(render).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'success' }));
  controller.dispose();
});

// --- signing in must not require a manual page reload ----------------------

it('re-requests after a retry when the earlier attempt was signed out', async () => {
  // The panel is told to retry when the popup reports a sign-in; the previous
  // rejection must not be replayed from the cache, or the page would stay stuck
  // on "not signed in" until reloaded by hand.
  const request = vi.fn()
    .mockRejectedValueOnce(new RecommendationError('signed-out', 'Authentication required'))
    .mockResolvedValueOnce({ best: result('id'), wait: null });
  const render = vi.fn();
  const controller = createController({
    inspect: () => ready(), request, render, createId: () => 'id',
  });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(render).toHaveBeenLastCalledWith(
    expect.objectContaining({ status: 'error', reason: 'signed-out' }),
  );

  controller.retry();
  await vi.advanceTimersByTimeAsync(700);
  expect(request).toHaveBeenCalledTimes(2);
  expect(render).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'success' }));
  controller.dispose();
});

// --- language changes must refetch, not just re-render ---------------------

it('treats the same purchase in another language as a different request', async () => {
  // The backend writes `reason` in the requested language, so a cached answer
  // from the previous language must not be reused -- that is exactly how a card
  // ends up showing translated labels beside stale prose.
  const request = vi.fn(async (r: CheckoutRequest) => ({ best: result(r.requestId), wait: null }));
  let snapshot: Inspection = {
    status: 'ready',
    context: { platform: 'shopee', product: '耳機', payable: 2580, pageLocale: 'zh-TW' },
  };
  let id = 0;
  const controller = createController({
    inspect: () => snapshot, request, render: vi.fn(), createId: () => `id-${++id}`,
  });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(request).toHaveBeenCalledTimes(1);

  // Same platform, product and amount; only the language differs.
  snapshot = {
    status: 'ready',
    context: { platform: 'shopee', product: '耳機', payable: 2580, pageLocale: 'en-US' },
  };
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(request).toHaveBeenCalledTimes(2);
  expect(request).toHaveBeenLastCalledWith(expect.objectContaining({ pageLocale: 'en-US' }));

  // Returning to the first language is still a distinct request, not a re-render.
  snapshot = {
    status: 'ready',
    context: { platform: 'shopee', product: '耳機', payable: 2580, pageLocale: 'zh-TW' },
  };
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(request).toHaveBeenCalledTimes(2); // served from cache: same identity as the first
  controller.dispose();
});

// --- progress reporting ----------------------------------------------------

it('shows the backend stage while a request is in flight', async () => {
  // The worst case is minutes long, so the panel has to say which part is running
  // rather than hold one frozen sentence.
  let resolveRequest: ((value: { best: Recommendation; wait: null }) => void) | undefined;
  const request = vi.fn(() => new Promise<{ best: Recommendation; wait: null }>(r => { resolveRequest = r; }));
  const render = vi.fn();
  const controller = createController({
    inspect: () => ready(), request, render, createId: () => 'id-1',
  });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);

  controller.reportStage('id-1', 'live_card_lookup');
  expect(render).toHaveBeenLastCalledWith(
    expect.objectContaining({ status: 'loading', stage: 'live_card_lookup' }),
  );

  // A stage for a superseded request must not relabel the current one.
  controller.reportStage('id-stale', 'official_verification');
  expect(render).toHaveBeenLastCalledWith(
    expect.objectContaining({ stage: 'live_card_lookup' }),
  );

  resolveRequest?.({ best: result('id-1'), wait: null });
  await vi.advanceTimersByTimeAsync(0);
  expect(render).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'success' }));
  controller.dispose();
});
