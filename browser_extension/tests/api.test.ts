import { afterEach, expect, it, vi } from 'vitest';
import { requestRecommendation } from '../lib/api';
import { getRecommendation, searchBody } from '../lib/backend';
import { SEARCH_ENDPOINT } from '../lib/backend-config';
import type { CheckoutContext, CheckoutRequest, Recommendation } from '../lib/types';

const { sendMessage } = vi.hoisted(() => ({ sendMessage: vi.fn<(...args: unknown[]) => Promise<unknown>>() }));
vi.mock('wxt/browser', () => ({ browser: { runtime: { sendMessage } } }));

const request: CheckoutRequest = { requestId: 'test-id', platform: 'momo', product: '耳機', payable: 2580 };
const context: CheckoutContext = { platform: 'momo', product: '耳機', payable: 2580 };
const recommendation: Recommendation = {
  user_card_id: 'uc-1',
  owned: true,
  card: { id: 'c-1', bank_name: '玉山', name: 'Unicard' },
  estimated_reward: {
    amount: 77.4, rate: 0.03, rate_max: null, currency: 'TWD',
    unit: 'TWD', capped: false, requires_registration: false, source_text: null,
  },
  reason: '一般消費 3% 回饋',
};

afterEach(() => { vi.clearAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('returns the recommendation the background echoed for this request', async () => {
  sendMessage.mockResolvedValue({ requestId: 'test-id', recommendation });
  await expect(requestRecommendation(request)).resolves.toEqual(recommendation);
  expect(sendMessage).toHaveBeenCalledWith({ type: 'recommend', request });
});

it('rejects a reply for a superseded request', async () => {
  sendMessage.mockResolvedValue({ requestId: 'older-request', recommendation });
  await expect(requestRecommendation(request)).rejects.toThrow('Invalid recommendation response');
});

it.each([
  { requestId: 'test-id' },
  { requestId: 'test-id', recommendation: {} },
  { requestId: 'test-id', recommendation: { card: {} } },
  null,
])('rejects a malformed background reply: %j', async (reply) => {
  sendMessage.mockResolvedValue(reply);
  await expect(requestRecommendation(request)).rejects.toThrow('Invalid recommendation response');
});

it('maps the checkout context onto the backend SearchRequest', () => {
  expect(searchBody(context)).toEqual({
    price: 2580,
    platform: 'momo',
    category: '耳機',
    currency: 'TWD',
    include_unowned: false,
  });
});

function mockFetch(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ best: recommendation, alternatives: [], resolved_category: null, considered_card_count: 1 }),
    ...response,
  } as Response);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

it('posts the search body with the Firebase ID token', async () => {
  const fetchMock = mockFetch({});

  await expect(getRecommendation(context, 'firebase-id-token')).resolves.toEqual(recommendation);

  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0]!;
  expect(url).toBe(SEARCH_ENDPOINT);
  expect(init?.method).toBe('POST');
  expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer firebase-id-token');
  // No userId in the body: the backend takes the user from the token.
  expect(JSON.parse(String(init?.body))).toEqual(searchBody(context));
  expect(init?.credentials).toBe('omit');
});

it('maps a rejected token to an authentication error', async () => {
  mockFetch({ ok: false, status: 401 });
  await expect(getRecommendation(context, 'stale-token')).rejects.toThrow('Authentication required');
});

it('reports a server failure with its status', async () => {
  mockFetch({ ok: false, status: 503 });
  await expect(getRecommendation(context, 'firebase-id-token'))
    .rejects.toThrow('Recommendation failed (503)');
});

it('treats a ranking with no best card as nothing to recommend', async () => {
  mockFetch({ json: async () => ({ best: null, alternatives: [], resolved_category: null, considered_card_count: 0 }) });
  await expect(getRecommendation(context, 'firebase-id-token')).rejects.toThrow('No card to recommend');
});

it('accepts a card whose reward could not be estimated', async () => {
  const unpriced = { ...recommendation, estimated_reward: null };
  mockFetch({ json: async () => ({ best: unpriced, alternatives: [], resolved_category: null, considered_card_count: 1 }) });
  await expect(getRecommendation(context, 'firebase-id-token')).resolves.toEqual(unpriced);
});

it.each([
  {},
  { best: { owned: true } },
  { best: { card: { bank_name: '玉山' }, reason: 'x' } },
  { best: { card: { name: '' }, reason: 'x' } },
  { best: { card: { name: 'Unicard' } } },
])('rejects a malformed backend payload: %j', async (payload) => {
  mockFetch({ json: async () => payload });
  await expect(getRecommendation(context, 'firebase-id-token'))
    .rejects.toThrow('Invalid recommendation response');
});

// --- distinguishing the three failure reasons -----------------------------
// Signed out, no cards and a transient failure need different instructions, so
// each must keep its reason all the way from the backend client to the panel.

it('reports a rejected token as signed-out', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"nope"}', { status: 401 })));
  await expect(getRecommendation(context, 'tok')).rejects.toMatchObject({ reason: 'signed-out' });
});

it('reports an empty ranking as no-cards rather than a failure', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(
    JSON.stringify({ best: null, alternatives: [], considered_card_count: 0 }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  )));
  await expect(getRecommendation(context, 'tok')).rejects.toMatchObject({ reason: 'no-cards' });
});

it('reports a server error as a transient failure', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"boom"}', { status: 500 })));
  await expect(getRecommendation(context, 'tok')).rejects.toMatchObject({ reason: 'failed' });
});

it('rebuilds the reason the background sent across the message boundary', async () => {
  sendMessage.mockResolvedValue({ error: 'No card to recommend', reason: 'no-cards' });
  await expect(requestRecommendation(request)).rejects.toMatchObject({ reason: 'no-cards' });
});

it('falls back to a transient failure when the reason is missing or unknown', async () => {
  sendMessage.mockResolvedValue({ error: 'boom', reason: 'not-a-real-reason' });
  await expect(requestRecommendation(request)).rejects.toMatchObject({ reason: 'failed' });
});
