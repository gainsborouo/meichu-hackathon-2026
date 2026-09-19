import { browser } from 'wxt/browser';
import { RecommendationError } from './types';
import type { CheckoutRequest, FailureReason, Recommendation } from './types';

const FAILURE_REASONS: readonly FailureReason[] = ['signed-out', 'no-cards', 'failed'];

function failureReason(value: unknown): FailureReason {
  return FAILURE_REASONS.includes(value as FailureReason) ? (value as FailureReason) : 'failed';
}

export async function requestRecommendation(
  request: CheckoutRequest,
): Promise<Recommendation> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const response = await Promise.race([
      browser.runtime.sendMessage({ type: 'recommend', request }),
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () => reject(new RecommendationError('failed', 'Recommendation timed out')),
          10_000,
        );
      }),
    ]);
    // Rebuild the typed error the background sent, so the panel can tell the
    // user which of the three things to do about it.
    if (response?.error) {
      throw new RecommendationError(failureReason(response.reason), response.error);
    }
    // requestId is echoed by the background, not the backend: it only confirms
    // this reply belongs to the checkout state we asked about.
    if (!response || response.requestId !== request.requestId
      || !response.recommendation?.card?.name) {
      throw new RecommendationError('failed', 'Invalid recommendation response');
    }
    return response.recommendation;
  } finally {
    if (timer) clearTimeout(timer);
  }
}
