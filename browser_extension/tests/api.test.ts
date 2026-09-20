import { afterEach, expect, it, vi } from 'vitest';
import { requestRecommendation } from '../lib/api';
import {
  createReminder,
  getRecommendation,
  getUserSettings,
  requestBody,
  setRegistrationCampaigns,
} from '../lib/backend';
import { RECOMMENDATION_ENDPOINT } from '../lib/backend-config';
import { messages, resolveLocale } from '../lib/i18n';
import type { CheckoutContext, CheckoutRequest, Recommendation } from '../lib/types';

const { sendMessage, storageGet } = vi.hoisted(() => ({
  sendMessage: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  // No stored preference by default, so requestBody falls back to the browser's.
  storageGet: vi.fn(async () => ({})),
}));
vi.mock('wxt/browser', () => ({
  browser: {
    runtime: { sendMessage },
    storage: { local: { get: storageGet, set: vi.fn(), remove: vi.fn() } },
  },
}));

const request: CheckoutRequest = {
  requestId: 'test-id', platform: 'momo', product: '耳機', payable: 2580, pageLocale: null,
};
const context: CheckoutContext = { platform: 'momo', product: '耳機', payable: 2580, pageLocale: null };

// Shaped like backend BestNow (backend/app/schemas/recommendations.py).
const recommendation: Recommendation = {
  candidate_type: 'campaign',
  card: { id: 'c-1', bank_name: '玉山', name: 'Unicard', issuer_en: 'E.SUN Bank', name_en: 'Unicard' },
  sale_id: 'sale-1',
  campaign_title: '一般消費回饋',
  estimated_reward_twd: 77.4,
  rate_display: '3%',
  cap_description: '上限 500 點',
  requires_registration: false,
  registration_url: null,
  reason: '一般消費 3% 回饋',
  verification_status: 'verified',
  official_sources: [{ title: '玉山銀行', url: 'https://www.esunbank.com/' }],
};

afterEach(() => { vi.clearAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals(); });

/** Builds a Response carrying an SSE stream, the way the backend answers. */
function sseResponse(frames: string[], init: ResponseInit = {}) {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
    ...init,
  });
}

const frame = (event: string, data: unknown) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

function mockStream(frames: string[], init?: ResponseInit) {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(sseResponse(frames, init));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const okFrames = [
  frame('searching', { stage: 'preprocessing', mode: 'no_registration' }),
  frame('recommendation', {
    mode: 'no_registration', best_now: recommendation, wait_suggestion: null, explanation: null,
  }),
  frame('done', {}),
];

// --- internal messaging ----------------------------------------------------

it('returns the recommendation the background echoed for this request', async () => {
  sendMessage.mockResolvedValue({ requestId: 'test-id', recommendation });
  await expect(requestRecommendation(request)).resolves.toMatchObject({ best: recommendation });
  expect(sendMessage).toHaveBeenCalledWith({ type: 'recommend', request });
});

it.each([
  { requestId: 'other-id', recommendation },
  { requestId: 'test-id' },
  { requestId: 'test-id', recommendation: { card: {} } },
  null,
])('rejects a malformed background reply: %j', async (reply) => {
  sendMessage.mockResolvedValue(reply);
  await expect(requestRecommendation(request)).rejects.toThrow('Invalid recommendation response');
});

// --- request shape ---------------------------------------------------------

it('maps the checkout context onto the backend RecommendationRequest', async () => {
  await expect(requestBody(context)).resolves.toEqual({
    product_name: '耳機',
    // The platform slug is itself an alias the backend matches campaigns on.
    store_name: 'momo',
    price: 2580,
    currency: 'TWD',
    // Whatever the panel renders in; asserted against the resolver rather than a
    // literal, because the test machine's language decides it.
    locale: resolveLocale(),
  });
});

it('follows the page language when the user has made no choice', async () => {
  // A shopper on a Chinese store page wants Chinese advice even if their browser
  // is English, so the page wins over navigator.languages.
  await expect(requestBody({ ...context, pageLocale: 'zh-TW' }))
    .resolves.toMatchObject({ locale: 'zh-TW' });
  await expect(requestBody({ ...context, pageLocale: 'en-US' }))
    .resolves.toMatchObject({ locale: 'en-US' });
});

it('falls back to the browser when the page declares no language', async () => {
  await expect(requestBody({ ...context, pageLocale: null }))
    .resolves.toMatchObject({ locale: resolveLocale() });
});

it('sends exactly the locale the content script resolved', async () => {
  // The decision is made once, where the document is. The background must not
  // re-derive it: its navigator is the service worker's, and resolving twice is
  // what produced English labels beside a Chinese reason.
  storageGet.mockResolvedValue({ locale: 'zh-TW' });
  await expect(requestBody({ ...context, pageLocale: 'en-US' }))
    .resolves.toMatchObject({ locale: 'en-US' });
  await expect(requestBody({ ...context, pageLocale: 'zh-TW' }))
    .resolves.toMatchObject({ locale: 'zh-TW' });
  storageGet.mockResolvedValue({});
});

it('asks the backend for the same language the panel renders in', () => {
  // The backend writes `reason` and `explanation` in this locale and the panel
  // shows them verbatim, so the two must never disagree.
  for (const [languages, expected] of [
    [['zh-TW'], 'zh-TW'],
    [['zh-CN'], 'zh-TW'],
    [['en-GB'], 'en-US'],
    [['fr-FR', 'en-US'], 'en-US'],
    [['ja-JP'], 'en-US'],
  ] as const) {
    expect(resolveLocale(languages)).toBe(expected);
    expect(messages(resolveLocale(languages)).brandName)
      .toBe(expected === 'zh-TW' ? '最佳一刷' : 'Swipe Right');
  }
});

it('truncates a product name past the backend 200-char limit', async () => {
  const long = 'x'.repeat(250);
  const body = await requestBody({ ...context, product: long });
  expect(body.product_name).toHaveLength(200);
});

it('posts to the streaming endpoint with the Firebase ID token', async () => {
  const fetchMock = mockStream(okFrames);

  await expect(getRecommendation(context, 'firebase-id-token'))
    .resolves.toMatchObject({ best: recommendation });

  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0]!;
  expect(url).toBe(RECOMMENDATION_ENDPOINT);
  expect(init?.method).toBe('POST');
  const headers = init?.headers as Record<string, string>;
  expect(headers.Authorization).toBe('Bearer firebase-id-token');
  expect(headers.Accept).toBe('text/event-stream');
  // No userId in the body: the backend takes the user from the token.
  expect(JSON.parse(String(init?.body))).toEqual(await requestBody(context));
  expect(init?.credentials).toBe('omit');
});

// --- reading the stream ----------------------------------------------------

it('ignores progress events and returns the recommendation', async () => {
  mockStream([
    frame('searching', { stage: 'preprocessing' }),
    frame('searching', { stage: 'official_verification' }),
    ...okFrames.slice(1),
  ]);
  await expect(getRecommendation(context, 'tok')).resolves.toMatchObject({ best: recommendation });
});

it('reads an event split across stream chunks', async () => {
  // The transport may break a frame anywhere; the parser must buffer.
  const whole = okFrames.join('');
  mockStream([whole.slice(0, 40), whole.slice(40, 120), whole.slice(120)]);
  await expect(getRecommendation(context, 'tok')).resolves.toMatchObject({ best: recommendation });
});

it('surfaces a server-sent error event', async () => {
  mockStream([
    frame('searching', { stage: 'preprocessing' }),
    frame('error', { message: 'Recommendation failed.' }),
  ]);
  await expect(getRecommendation(context, 'tok'))
    .rejects.toMatchObject({ reason: 'failed', message: 'Recommendation failed.' });
});

it('fails when the stream ends without a recommendation', async () => {
  mockStream([frame('searching', { stage: 'preprocessing' }), frame('done', {})]);
  await expect(getRecommendation(context, 'tok'))
    .rejects.toThrow('Recommendation stream ended without a result');
});

it('skips a keep-alive comment and a malformed frame', async () => {
  mockStream([': keep-alive\n\n', 'event: searching\ndata: not-json\n\n', ...okFrames.slice(1)]);
  await expect(getRecommendation(context, 'tok')).resolves.toMatchObject({ best: recommendation });
});

// --- failure reasons -------------------------------------------------------
// Signed out, no cards and a transient failure need different instructions, so
// each must keep its reason all the way from the backend client to the panel.

it('reports a rejected token as signed-out', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"nope"}', { status: 401 })));
  await expect(getRecommendation(context, 'tok')).rejects.toMatchObject({ reason: 'signed-out' });
});

it('reports a server failure with its status', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"boom"}', { status: 503 })));
  await expect(getRecommendation(context, 'tok'))
    .rejects.toMatchObject({ reason: 'failed', message: 'Recommendation failed (503)' });
});

it('treats best_now: null as nothing to recommend, keeping the explanation', async () => {
  mockStream([frame('recommendation', {
    mode: 'no_registration', best_now: null, wait_suggestion: null,
    explanation: '目前持有的卡片中，沒有符合此購物條件的有效優惠。',
  }), frame('done', {})]);
  await expect(getRecommendation(context, 'tok')).rejects.toMatchObject({
    reason: 'no-cards',
    message: '目前持有的卡片中，沒有符合此購物條件的有效優惠。',
  });
});

it.each([
  {},
  { best_now: { card: { bank_name: '玉山' }, reason: 'x' } },
  { best_now: { card: { name: '' }, reason: 'x' } },
  { best_now: { ...recommendation, estimated_reward_twd: 'lots' } },
  { best_now: { ...recommendation, official_sources: [{ title: 'x' }] } },
])('rejects a malformed recommendation payload: %j', async (payload) => {
  mockStream([frame('recommendation', payload), frame('done', {})]);
  await expect(getRecommendation(context, 'tok'))
    .rejects.toThrow('Invalid recommendation response');
});

it('rebuilds the reason the background sent across the message boundary', async () => {
  sendMessage.mockResolvedValue({ error: 'No card to recommend', reason: 'no-cards' });
  await expect(requestRecommendation(request)).rejects.toMatchObject({ reason: 'no-cards' });
});

it('falls back to a transient failure when the reason is missing or unknown', async () => {
  sendMessage.mockResolvedValue({ error: 'boom', reason: 'not-a-real-reason' });
  await expect(requestRecommendation(request)).rejects.toMatchObject({ reason: 'failed' });
});

// --- new backend fields ----------------------------------------------------

it('accepts a base benefit, which carries benefit_id instead of sale_id', async () => {
  const baseBenefit = {
    ...recommendation,
    candidate_type: 'base_benefit' as const,
    sale_id: null,
    benefit_id: 'benefit-1',
  };
  mockStream([frame('recommendation', {
    mode: 'no_registration', best_now: baseBenefit, wait_suggestion: null, explanation: null,
  }), frame('done', {})]);
  await expect(getRecommendation(context, 'tok')).resolves.toMatchObject({ best: baseBenefit });
});

it('rejects a payload whose candidate_type is missing or unknown', async () => {
  for (const candidate_type of [undefined, 'something-else']) {
    mockStream([frame('recommendation', {
      best_now: { ...recommendation, candidate_type },
    }), frame('done', {})]);
    await expect(getRecommendation(context, 'tok'))
      .rejects.toThrow('Invalid recommendation response');
  }
});

it('reports each known search stage, and ignores unknown ones', async () => {
  // The backend may add a stage; a raw identifier in the UI would be worse than
  // silence, so unknown stages must not reach the panel.
  mockStream([
    frame('searching', { stage: 'preprocessing' }),
    frame('searching', { stage: 'live_card_lookup', cards: 3 }),
    frame('searching', { stage: 'reprocessing' }),
    frame('searching', { stage: 'a_stage_we_do_not_know' }),
    frame('searching', { stage: 'official_verification' }),
    ...okFrames.slice(1),
  ]);
  const stages: string[] = [];
  await expect(getRecommendation(context, 'tok', stage => stages.push(stage)))
    .resolves.toMatchObject({ best: recommendation });
  expect(stages).toEqual([
    'preprocessing', 'live_card_lookup', 'reprocessing', 'official_verification',
  ]);
});

// --- user settings (GET/PATCH /api/v1/me) ----------------------------------

function jsonOnce(body: unknown, status = 200) {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status, headers: { 'Content-Type': 'application/json' },
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

it('reads the registration-campaigns preference', async () => {
  const fetchMock = jsonOnce({
    id: 'u-1', email: 'a@b.c', registration_campaigns_enabled: true, latest_spend_report: null,
  });
  await expect(getUserSettings('tok'))
    .resolves.toMatchObject({ registration_campaigns_enabled: true });
  const [, init] = fetchMock.mock.calls[0]!;
  expect(init?.method).toBe('GET');
  expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok');
});

it('patches the preference and returns what the server stored', async () => {
  // The popup renders the response rather than the requested value, so a server
  // that stored something else is reflected instead of silently diverging.
  const fetchMock = jsonOnce({ registration_campaigns_enabled: false });
  await expect(setRegistrationCampaigns('tok', true))
    .resolves.toMatchObject({ registration_campaigns_enabled: false });
  const [, init] = fetchMock.mock.calls[0]!;
  expect(init?.method).toBe('PATCH');
  expect(JSON.parse(String(init?.body))).toEqual({ registration_campaigns_enabled: true });
});

it('maps a rejected token on settings to signed-out', async () => {
  jsonOnce({ detail: 'nope' }, 401);
  await expect(getUserSettings('stale')).rejects.toMatchObject({ reason: 'signed-out' });
});

it('rejects a settings payload without the boolean', async () => {
  // Never report success for a value we did not actually receive.
  jsonOnce({ id: 'u-1', email: 'a@b.c' });
  await expect(getUserSettings('tok')).rejects.toThrow('Invalid settings response');
  jsonOnce({ registration_campaigns_enabled: 'yes' });
  await expect(getUserSettings('tok')).rejects.toThrow('Invalid settings response');
});

// --- wait suggestion and reminders -----------------------------------------

const waitSuggestion = {
  card: { id: 'c-1', bank_name: '玉山', name: 'Unicard' },
  sale_id: 'sale-9',
  starts_at: '2026-10-01',
  estimated_reward_twd: 300,
  estimated_extra_reward_twd: 42,
  reason: '10 月起有加碼活動',
  official_sources: [],
  calendar_draft: { title: '記得用 Unicard 買', starts_at: '2026-10-01T09:00:00+08:00', notes: '加碼 5%' },
};

it('surfaces a wait suggestion alongside the pick', async () => {
  mockStream([frame('recommendation', {
    mode: 'no_registration', best_now: recommendation,
    wait_suggestion: waitSuggestion, explanation: null,
  }), frame('done', {})]);
  await expect(getRecommendation(context, 'tok'))
    .resolves.toMatchObject({ best: recommendation, wait: waitSuggestion });
});

it.each([
  { ...waitSuggestion, calendar_draft: { notes: 'x' } },
  { ...waitSuggestion, sale_id: '' },
  { ...waitSuggestion, estimated_extra_reward_twd: 'lots' },
])('drops a malformed wait suggestion but keeps the pick: %j', async (bad) => {
  // A suggestion we cannot act on must not produce a button that would fail; the
  // recommendation itself is still useful.
  mockStream([frame('recommendation', {
    best_now: recommendation, wait_suggestion: bad,
  }), frame('done', {})]);
  await expect(getRecommendation(context, 'tok'))
    .resolves.toMatchObject({ best: recommendation, wait: null });
});

it('creates a reminder from the draft and forwards the Google token', async () => {
  const fetchMock = jsonOnce({ already_notified: false }, 201);
  await expect(createReminder(waitSuggestion, 'id-tok', 'google-tok'))
    .resolves.toEqual({ alreadyNotified: false });
  const [, init] = fetchMock.mock.calls[0]!;
  const headers = init?.headers as Record<string, string>;
  expect(headers.Authorization).toBe('Bearer id-tok');
  expect(headers['X-Google-Access-Token']).toBe('google-tok');
  expect(JSON.parse(String(init?.body))).toEqual({
    title: waitSuggestion.calendar_draft.title,
    starts_at: waitSuggestion.calendar_draft.starts_at,
    notes: waitSuggestion.calendar_draft.notes,
    // Links the reminder to the campaign so the backend can de-duplicate.
    sale_id: 'sale-9',
  });
});

it('omits the Google token header when none is held', async () => {
  const fetchMock = jsonOnce({ already_notified: false }, 201);
  await createReminder(waitSuggestion, 'id-tok', null);
  const [, init] = fetchMock.mock.calls[0]!;
  expect((init?.headers as Record<string, string>)['X-Google-Access-Token']).toBeUndefined();
});

it('reports an already-notified campaign rather than failing', async () => {
  jsonOnce({ already_notified: true }, 200);
  await expect(createReminder(waitSuggestion, 'id-tok', 'g'))
    .resolves.toEqual({ alreadyNotified: true });
});

it.each([
  [409, 'not-connected'],
  [401, 'signed-out'],
  [502, 'failed'],
])('maps reminder status %i to %s', async (status, reason) => {
  jsonOnce({ detail: 'x' }, status);
  await expect(createReminder(waitSuggestion, 'id-tok', 'g'))
    .rejects.toMatchObject({ reason });
});
