import { BACKEND_URL, RECOMMENDATION_ENDPOINT } from './backend-config';
import { currentLocale } from './i18n';
import { RecommendationError } from './types';
import type {
  BestNow,
  CheckoutContext,
  OfficialSource,
  RecommendationRequestBody,
  RecommendationResponse,
  SearchStage,
  UserSettings,
  WaitSuggestion,
} from './types';

/** What one recommendation request yields: the pick, plus an optional "wait" option. */
export interface Outcome {
  best: BestNow;
  wait: WaitSuggestion | null;
}

const STAGES: readonly SearchStage[] = [
  'preprocessing',
  'live_card_lookup',
  'reprocessing',
  'official_verification',
];

// The backend's worst case is a live official lookup followed by the model run:
// 150s (live_refresh.LOOKUP_TIMEOUT_SECONDS) + 120s
// (recommendation_agent.DEFAULT_TIMEOUT_SECONDS). Both report their own failure as
// an `error` event, which is far more useful than a client-side abort, so this sits
// past their sum and only fires when the backend has stopped answering altogether.
// lib/api.ts waits longer still, so whichever fires, the reason is not a bare
// messaging timeout.
const REQUEST_TIMEOUT_MS = 285_000;

export async function getRecommendation(
  context: CheckoutContext,
  idToken: string,
  // Called as the backend reports progress. The whole request can take minutes,
  // so the panel needs to say which part is running.
  onStage?: (stage: SearchStage) => void,
): Promise<Outcome> {
  const response = await fetch(RECOMMENDATION_ENDPOINT, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      // The backend verifies this and takes the user from the token's `sub`
      // claim, so the body carries no user identifier to forge.
      Authorization: `Bearer ${idToken}`,
    },
    body: JSON.stringify(await requestBody(context)),
    // No cookies: this endpoint is authenticated by the ID token only, and
    // sending ambient credentials to it would be pointless and CSRF-prone.
    credentials: 'omit',
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });

  if (!response.ok) {
    // 401/403 means the ID token was rejected or expired, so signing in again
    // is what fixes it -- distinct from a transient server error.
    if (response.status === 401 || response.status === 403) {
      throw new RecommendationError('signed-out', 'Authentication required');
    }
    throw new RecommendationError('failed', `Recommendation failed (${response.status})`);
  }

  return readRecommendation(response, onStage);
}

export async function requestBody(context: CheckoutContext): Promise<RecommendationRequestBody> {
  return {
    // The checkout page gives us product names, joined when there are several.
    product_name: truncate(context.product, 200),
    // The backend matches this against each campaign's platform aliases, and
    // every platform slug we send is an alias of itself.
    store_name: context.platform,
    price: context.payable,
    currency: 'TWD',
    // Exactly what the panel renders in, decided in the content script and carried
    // here. Not re-derived: this runs in the background, whose `navigator` is the
    // service worker's and whose document does not exist, so resolving it again
    // produced a different answer -- English labels beside a Chinese `reason`.
    // Falls back only when the message carried nothing, which validation prevents.
    locale: context.pageLocale ?? await currentLocale(),
  };
}

// product_name/store_name are capped at 200 chars by the backend schema, and a
// multi-item checkout can exceed that -- a 422 would be our fault, not the
// user's, so trim rather than let the request be rejected.
function truncate(value: string, max: number): string {
  return value.length <= max ? value : value.slice(0, max);
}

/**
 * Reads the SSE stream and returns the recommendation it carries.
 *
 * The stream is a progress channel: `searching` events report stages we do not
 * surface, `recommendation` carries the answer, `error` carries a server-side
 * failure, and `done` closes it. Only the outcome matters to the panel, so this
 * collapses the stream into one value.
 */
async function readRecommendation(
  response: Response,
  onStage?: (stage: SearchStage) => void,
): Promise<Outcome> {
  const body = response.body;
  if (!body) throw new RecommendationError('failed', 'Recommendation stream unavailable');

  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  let result: BestNow | null | undefined;
  let explanation: string | null = null;
  let wait: WaitSuggestion | null = null;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += value;

      // Events are separated by a blank line. Anything after the last separator
      // is an incomplete event and stays in the buffer.
      let split = buffer.indexOf('\n\n');
      while (split !== -1) {
        const event = parseEvent(buffer.slice(0, split));
        buffer = buffer.slice(split + 2);
        split = buffer.indexOf('\n\n');

        if (event?.name === 'searching') {
          // Unknown stages are dropped rather than shown: the backend may add
          // one, and a raw identifier in the UI would be worse than silence.
          const stage = event.data?.stage;
          if (STAGES.includes(stage as SearchStage)) onStage?.(stage as SearchStage);
        }
        if (event?.name === 'error') {
          throw new RecommendationError('failed',
            typeof event.data?.message === 'string' ? event.data.message : 'Recommendation failed');
        }
        if (event?.name === 'recommendation') {
          const payload = event.data as unknown;
          if (!isRecommendationResponse(payload)) {
            throw new RecommendationError('failed', 'Invalid recommendation response');
          }
          result = payload.best_now;
          explanation = payload.explanation;
          // Kept only when well-formed: a malformed suggestion is dropped rather
          // than shown, since the recommendation itself is still usable.
          wait = isWaitSuggestion(payload.wait_suggestion) ? payload.wait_suggestion : null;
        }
      }
    }
  } finally {
    // Releasing the lock lets the connection be torn down when we stop early,
    // e.g. after an error event with the stream still open.
    reader.releaseLock();
    void body.cancel().catch(() => undefined);
  }

  // No recommendation event at all: the stream ended without answering.
  if (result === undefined) {
    throw new RecommendationError('failed', 'Recommendation stream ended without a result');
  }
  // A valid answer of "no held card fits this purchase". The user acts on it by
  // adding a card, not by retrying, and the backend's explanation says why.
  if (result === null) {
    throw new RecommendationError('no-cards', explanation || 'No card to recommend');
  }
  return { best: result, wait };
}

function parseEvent(chunk: string): { name: string; data: Record<string, unknown> } | null {
  let name = 'message';
  const dataLines: string[] = [];
  for (const line of chunk.split('\n')) {
    if (line.startsWith(':')) continue; // comment/keep-alive
    if (line.startsWith('event:')) name = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    const data: unknown = JSON.parse(dataLines.join('\n'));
    return { name, data: (data ?? {}) as Record<string, unknown> };
  } catch {
    // A malformed frame is not worth failing the whole stream over; the missing
    // recommendation is caught after the loop.
    return null;
  }
}

function isRecommendationResponse(value: unknown): value is RecommendationResponse {
  if (!value || typeof value !== 'object') return false;
  const data = value as Record<string, unknown>;
  if (!('best_now' in data)) return false;
  return data.best_now === null || isBestNow(data.best_now);
}

function isBestNow(value: unknown): value is BestNow {
  if (!value || typeof value !== 'object') return false;
  const data = value as Record<string, unknown>;
  const card = data.card as Record<string, unknown> | undefined;
  return !!card && typeof card === 'object'
    && typeof card.name === 'string' && card.name.length > 0
    && (card.bank_name === null || typeof card.bank_name === 'string')
    && (data.candidate_type === 'base_benefit' || data.candidate_type === 'campaign')
    && typeof data.reason === 'string'
    && typeof data.estimated_reward_twd === 'number'
    && typeof data.rate_display === 'string'
    && typeof data.requires_registration === 'boolean'
    && Array.isArray(data.official_sources)
    && (data.official_sources as unknown[]).every(isOfficialSource);
}

function isOfficialSource(value: unknown): value is OfficialSource {
  if (!value || typeof value !== 'object') return false;
  const data = value as Record<string, unknown>;
  return typeof data.title === 'string' && typeof data.url === 'string';
}

// --- user settings ---------------------------------------------------------

const ME_ENDPOINT = new URL('/api/v1/me', BACKEND_URL).href;

// A plain read/write, so the generous streaming timeout does not apply.
const SETTINGS_TIMEOUT_MS = 10_000;

/** GET or PATCH /api/v1/me (backend UserRead / UserUpdate). */
async function me(idToken: string, update?: { registration_campaigns_enabled: boolean }) {
  const response = await fetch(ME_ENDPOINT, {
    method: update ? 'PATCH' : 'GET',
    headers: {
      Authorization: `Bearer ${idToken}`,
      ...(update ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(update ? { body: JSON.stringify(update) } : {}),
    credentials: 'omit',
    signal: AbortSignal.timeout(SETTINGS_TIMEOUT_MS),
  });
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      throw new RecommendationError('signed-out', 'Authentication required');
    }
    throw new RecommendationError('failed', `Settings request failed (${response.status})`);
  }
  const payload: unknown = await response.json();
  if (!isUserSettings(payload)) {
    throw new RecommendationError('failed', 'Invalid settings response');
  }
  return payload;
}

export const getUserSettings = (idToken: string) => me(idToken);

// The backend answers with the updated user, so the caller never has to guess
// what was stored -- it renders what came back.
export const setRegistrationCampaigns = (idToken: string, enabled: boolean) =>
  me(idToken, { registration_campaigns_enabled: enabled });

function isUserSettings(value: unknown): value is UserSettings {
  if (!value || typeof value !== 'object') return false;
  return typeof (value as Record<string, unknown>).registration_campaigns_enabled === 'boolean';
}


function isWaitSuggestion(value: unknown): value is WaitSuggestion {
  if (!value || typeof value !== 'object') return false;
  const data = value as Record<string, unknown>;
  const card = data.card as Record<string, unknown> | undefined;
  const draft = data.calendar_draft as Record<string, unknown> | undefined;
  return !!card && typeof card.name === 'string' && card.name.length > 0
    && typeof data.sale_id === 'string' && data.sale_id.length > 0
    && typeof data.starts_at === 'string'
    && typeof data.estimated_extra_reward_twd === 'number'
    && typeof data.reason === 'string'
    // Without a usable draft there is nothing to put in the calendar, so an
    // offer to remind the user would be one we could not honour.
    && !!draft && typeof draft.title === 'string' && draft.title.length > 0
    && typeof draft.starts_at === 'string';
}

// --- calendar reminders ----------------------------------------------------

const CALENDAR_EVENTS_ENDPOINT = new URL('/api/v1/me/calendar/events', BACKEND_URL).href;

export type ReminderOutcome = { alreadyNotified: boolean };

/**
 * Creates the reminder for a "buy it later" choice.
 *
 * `googleAccessToken` is forwarded so the backend can act on the user's behalf; it
 * carries the calendar.events scope from sign-in. Sent in its own header, never in
 * the body, and only to our own backend origin.
 */
export async function createReminder(
  wait: WaitSuggestion,
  idToken: string,
  googleAccessToken: string | null,
): Promise<ReminderOutcome> {
  const response = await fetch(CALENDAR_EVENTS_ENDPOINT, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${idToken}`,
      ...(googleAccessToken ? { 'X-Google-Access-Token': googleAccessToken } : {}),
    },
    body: JSON.stringify({
      title: wait.calendar_draft.title,
      starts_at: wait.calendar_draft.starts_at,
      notes: wait.calendar_draft.notes,
      // Links the reminder to the campaign, which is what lets the backend record
      // the notification and never send a second one for the same offer.
      sale_id: wait.sale_id,
    }),
    credentials: 'omit',
    signal: AbortSignal.timeout(SETTINGS_TIMEOUT_MS),
  });

  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      throw new ReminderError('signed-out', 'Authentication required');
    }
    // 409: no calendar linked yet. Retrying cannot fix it, so it is its own reason.
    if (response.status === 409) {
      throw new ReminderError('not-connected', 'No calendar connected');
    }
    throw new ReminderError('failed', `Reminder failed (${response.status})`);
  }

  const payload: unknown = await response.json();
  const already = (payload as { already_notified?: unknown } | null)?.already_notified;
  return { alreadyNotified: already === true };
}

export class ReminderError extends Error {
  constructor(readonly reason: 'not-connected' | 'signed-out' | 'failed', message: string) {
    super(message);
    this.name = 'ReminderError';
  }
}
