import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createController } from '../lib/controller';
import { RecommendationError } from '../lib/types';
import type { CheckoutRequest, Inspection, Recommendation } from '../lib/types';

const ready = (payable = 2580): Inspection => ({ status: 'ready', context: { platform: 'shopee', product: '耳機', payable } });
const result = (requestId: string): Recommendation => ({
  user_card_id: requestId,
  owned: true,
  card: { id: 'c-1', bank_name: '玉山', name: 'Unicard' },
  estimated_reward: null,
  reason: '一般消費回饋',
});
beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it('debounces changes, deduplicates unchanged data and sends the checkout fields to background', async () => {
  let snapshot = ready();
  const request = vi.fn(async (value: CheckoutRequest) => result(value.requestId));
  const controller = createController({ inspect: () => snapshot, request, render: vi.fn(), createId: () => 'id' });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(400);
  snapshot = ready(3000);
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(request).toHaveBeenCalledExactlyOnceWith({ requestId: 'id', platform: 'shopee', product: '耳機', payable: 3000 });
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
  const pending: ((value: Recommendation) => void)[] = [];
  const request = vi.fn(() => new Promise<Recommendation>((resolve) => pending.push(resolve)));
  const render = vi.fn();
  const controller = createController({ inspect: () => snapshot, request, render, createId: () => 'id' });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  snapshot = ready(3000);
  controller.refresh();
  pending[0]!(result('id'));
  await vi.advanceTimersByTimeAsync(700);
  expect(render.mock.calls.some(([state]) => state.status === 'success')).toBe(false);
  snapshot = { status: 'no-credit-card', reason: 'credit-unavailable' };
  pending[1]!(result('id'));
  await vi.advanceTimersByTimeAsync(0);
  expect(render).toHaveBeenLastCalledWith({ status: 'unavailable', reason: 'credit-unavailable' });
  controller.dispose();
});

it('reuses the same request after temporary DOM loss during a re-render', async () => {
  let snapshot: Inspection = ready();
  const request = vi.fn(async (value: CheckoutRequest) => result(value.requestId));
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
  const request = vi.fn(async (value: CheckoutRequest) => result(value.requestId));
  const controller = createController({
    inspect: () => snapshot,
    request,
    render: vi.fn(),
    createId: () => `id-${++sequence}`,
  });
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  snapshot = { status: 'ready', context: { platform: 'shopee', product: '保護殼', payable: 2580 } };
  controller.refresh();
  await vi.advanceTimersByTimeAsync(700);
  expect(request).toHaveBeenNthCalledWith(1, { requestId: 'id-1', platform: 'shopee', product: '耳機', payable: 2580 });
  expect(request).toHaveBeenNthCalledWith(2, { requestId: 'id-2', platform: 'shopee', product: '保護殼', payable: 2580 });
  controller.dispose();
});

it('supports retry after an error', async () => {
  let snapshot = ready();
  const render = vi.fn();
  const request = vi.fn(async (value: CheckoutRequest) => result(value.requestId))
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
    .mockResolvedValueOnce(result('id'));
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
    .mockResolvedValueOnce(result('id'));
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
