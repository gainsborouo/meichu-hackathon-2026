import { SEARCH_ENDPOINT } from './backend-config';
import { RecommendationError } from './types';
import type { CheckoutContext, Recommendation, SearchRequest, SearchResponse } from './types';


const REQUEST_TIMEOUT_MS = 8_000;

// The caller (lib/api.ts) gives up at 10s, so fail first to surface a real
// reason instead of a generic timeout.
export async function getRecommendation(
  context: CheckoutContext,
  idToken: string,
): Promise<Recommendation> {
  const response = await fetch(SEARCH_ENDPOINT, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      // The backend verifies this and takes the user from the token's `sub`
      // claim, so the body carries no user identifier to forge.
      Authorization: `Bearer ${idToken}`,
    },
    body: JSON.stringify(searchBody(context)),
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

  const payload: unknown = await response.json();
  if (!isSearchResponse(payload)) {
    throw new RecommendationError('failed', 'Invalid recommendation response');
  }
  // A ranking over zero owned cards is a valid response, but there is nothing
  // to recommend, so treat it as unavailable rather than showing an empty card.
  // A ranking over zero owned cards: the request succeeded and the answer is
  // "you hold no cards", which the user fixes by adding one, not by retrying.
  if (!payload.best) throw new RecommendationError('no-cards', 'No card to recommend');
  return payload.best;
}

export function searchBody(context: CheckoutContext): SearchRequest {
  return {
    price: context.payable,
    platform: context.platform,
    // The backend substring-matches this against its category vocabulary. The
    // checkout page gives us product names, not a taxonomy, so a multi-item
    // string may match nothing and fall back to platform/general rules.
    category: context.product,
    currency: 'TWD',
    // Only rank cards the user actually holds: recommending a card they would
    // have to apply for cannot help them at a checkout page.
    include_unowned: false,
  };
}

function isSearchResponse(value: unknown): value is SearchResponse {
  if (!value || typeof value !== 'object') return false;
  const data = value as Record<string, unknown>;
  if (!('best' in data)) return false;
  return data.best === null || isRecommendation(data.best);
}

function isRecommendation(value: unknown): value is Recommendation {
  if (!value || typeof value !== 'object') return false;
  const data = value as Record<string, unknown>;
  const card = data.card as Record<string, unknown> | undefined;
  return !!card && typeof card === 'object'
    && typeof card.name === 'string' && card.name.length > 0
    && (card.bank_name === null || typeof card.bank_name === 'string')
    && typeof data.reason === 'string';
}
