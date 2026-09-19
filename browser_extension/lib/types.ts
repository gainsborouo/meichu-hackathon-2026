export type Platform = 'momo' | 'shopee' | 'pchome';

// Internal extension message. Authentication is added only in the background.
export interface CheckoutRequest {
  requestId: string;
  platform: Platform;
  product: string;
  payable: number;
}

// Backend body for POST /api/v1/search (backend/app/schemas/db.py SearchRequest).
// requestId and userId are deliberately absent: the backend identifies the user
// from the verified ID token's `sub` claim, and correlating responses to the
// current checkout state stays a local concern (see lib/controller.ts).
export interface SearchRequest {
  price: number;
  platform: Platform;
  // A category name ("dining") or the item itself. The backend substring-matches
  // this against its category vocabulary and falls back to platform/general
  // rules when nothing matches.
  category: string;
  currency: 'TWD';
  include_unowned: boolean;
}

export interface Card {
  id: string;
  bank_name: string | null;
  name: string;
}

export interface EstimatedReward {
  amount: number;
  rate: number | null;
  rate_max: number | null;
  currency: string;
  unit: string | null;
  capped: boolean;
  requires_registration: boolean;
  source_text: string | null;
}

export interface Recommendation {
  user_card_id: string | null;
  owned: boolean;
  card: Card;
  // Null when the card's campaigns carry no machine-readable rate; such cards
  // are still returned, ranked last.
  estimated_reward: EstimatedReward | null;
  reason: string;
}

export interface SearchResponse {
  resolved_category: string | null;
  best: Recommendation | null;
  alternatives: Recommendation[];
  considered_card_count: number;
}

export type CheckoutContext = Omit<CheckoutRequest, 'requestId'>;
export type UnavailableReason = 'credit-unavailable' | 'product-missing' | 'amount-missing';

// Why a recommendation could not be shown. These are distinct because the user
// has to do something different about each: sign in, add a card, or just retry.
// 'failed' is anything transient (network, 5xx, malformed reply).
export type FailureReason = 'signed-out' | 'no-cards' | 'failed';

// Carries the reason as a field rather than encoding it in the message, so it
// survives runtime messaging (where only `message` would reach the caller).
export class RecommendationError extends Error {
  constructor(readonly reason: FailureReason, message: string) {
    super(message);
    this.name = 'RecommendationError';
  }
}
export type Inspection =
  | { status: 'outside' }
  | { status: 'no-credit-card' | 'incomplete'; reason: UnavailableReason }
  | { status: 'ready'; context: CheckoutContext };

export type PanelState =
  | { status: 'hidden' }
  | { status: 'unavailable'; reason: UnavailableReason }
  | { status: 'loading'; context: CheckoutContext }
  | { status: 'success'; context: CheckoutContext; result: Recommendation }
  | { status: 'error'; context: CheckoutContext; reason: FailureReason };
