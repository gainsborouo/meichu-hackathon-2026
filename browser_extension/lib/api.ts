import { browser } from 'wxt/browser';
import { RecommendationError } from './types';
import type {
  CheckoutRequest,
  FailureReason,
  Recommendation,
  ReminderState,
  WaitSuggestion,
} from './types';

// Must outlast lib/backend.ts's fetch abort (285s), which itself outlasts the
// backend's live lookup plus model run -- otherwise a recommendation still being
// computed is reported as a timeout here and the real reason never surfaces.
const MESSAGE_TIMEOUT_MS = 295_000;

const FAILURE_REASONS: readonly FailureReason[] = ['signed-out', 'no-cards', 'failed'];

function failureReason(value: unknown): FailureReason {
  return FAILURE_REASONS.includes(value as FailureReason) ? (value as FailureReason) : 'failed';
}

export async function requestRecommendation(
  request: CheckoutRequest,
): Promise<{ best: Recommendation; wait: WaitSuggestion | null }> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const response = await Promise.race([
      browser.runtime.sendMessage({ type: 'recommend', request }),
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () => reject(new RecommendationError('failed', 'Recommendation timed out')),
          MESSAGE_TIMEOUT_MS,
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
    return { best: response.recommendation, wait: response.wait ?? null };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * Asks the background to create the "buy it later" reminder.
 *
 * Returns the resulting panel state rather than throwing: the caller renders it, and
 * every outcome here is something the user should see -- including `not-connected`,
 * which needs a different instruction from a transient failure.
 */
export async function createReminder(wait: WaitSuggestion): Promise<ReminderState> {
  try {
    const response = await browser.runtime.sendMessage({ type: 'reminder:create', wait });
    if (response?.error) {
      const reason = response.reason;
      return {
        status: 'error',
        reason: reason === 'not-connected' || reason === 'signed-out' ? reason : 'failed',
      };
    }
    return { status: 'saved', alreadyNotified: response?.alreadyNotified === true };
  } catch {
    return { status: 'error', reason: 'failed' };
  }
}
