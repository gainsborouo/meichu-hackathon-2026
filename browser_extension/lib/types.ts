import type { Locale } from './i18n';

export type Platform = 'momo' | 'shopee' | 'pchome';

// Internal extension message. Authentication is added only in the background.
//
// `pageLocale` is the language the panel is rendering in, resolved in the content
// script and carried here. The background cannot work it out: it has no document
// to read `lang` from, and its own `navigator` belongs to the service worker, not
// the page -- resolving it in both places is what produced English labels beside
// a Chinese `reason`. Null only if nothing could be resolved at all.
export interface CheckoutRequest {
  requestId: string;
  platform: Platform;
  product: string;
  payable: number;
  pageLocale: Locale | null;
}

// Backend body for POST /api/v1/recommendations/stream
// (backend/app/schemas/recommendations.py RecommendationRequest). No requestId or
// userId: the backend identifies the user from the verified ID token's `sub`
// claim, and correlating replies to the current checkout state stays local
// (see lib/controller.ts).
export interface RecommendationRequestBody {
  product_name: string;
  // Matched against each campaign's platform aliases, so the platform slug is
  // what belongs here -- "momo"/"shopee"/"pchome" all match their own aliases.
  store_name: string;
  price: number;
  currency: 'TWD';
  locale: Locale;
}

// backend CardRef
export interface Card {
  id: string;
  bank_name: string | null;
  name: string;
  // English names, when the catalog has them. The card's own name is Chinese, so
  // without these an English recommendation reads as mixed script.
  issuer_en?: string | null;
  name_en?: string | null;
  artwork_id?: string | null;
}

export interface OfficialSource {
  title: string;
  url: string;
}

// backend BestNow: the card to use for this purchase right now.
export interface BestNow {
  // 'base_benefit' is the card's standing reward; 'campaign' a limited-time offer.
  // They read differently to a user, so the panel says which it is.
  candidate_type: 'base_benefit' | 'campaign';
  card: Card;
  // Optional because only one applies: sale_id for a campaign, benefit_id for a
  // base benefit.
  sale_id?: string | null;
  benefit_id?: string | null;
  campaign_title: string;
  estimated_reward_twd: number;
  rate_display: string;
  cap_description: string | null;
  requires_registration: boolean;
  registration_url: string | null;
  reason: string;
  // "unverified" means the campaign terms could not be confirmed against an
  // official bank page, so the panel says so rather than implying certainty.
  verification_status: 'verified' | 'unverified';
  official_sources: OfficialSource[];
}

// backend CalendarDraft. A proposal only -- nothing exists in the calendar until
// POST /api/v1/me/calendar/events is called with it.
export interface CalendarDraft {
  title: string;
  starts_at: string;
  notes: string;
}

// backend WaitSuggestion: buying later would earn more than buying now.
export interface WaitSuggestion {
  card: Card;
  sale_id: string;
  starts_at: string;
  estimated_reward_twd: number;
  // The gain over buying now -- the whole reason to consider waiting.
  estimated_extra_reward_twd: number;
  reason: string;
  official_sources: OfficialSource[];
  calendar_draft: CalendarDraft;
}

// backend RecommendationResponse. `best_now` is null when no held card has a
// campaign matching this purchase; `explanation` then says why.
export interface RecommendationResponse {
  mode: 'no_registration' | 'registration';
  best_now: BestNow | null;
  // Present when waiting for an upcoming campaign would earn more. Acting on it is
  // a separate, explicit step: the user chooses, then a calendar event is created.
  wait_suggestion: WaitSuggestion | null;
  explanation: string | null;
}

// What the panel shows on success. The backend's "best card right now" is the
// whole of it, so this is an alias rather than a second shape to keep in step.
export type Recommendation = BestNow;

// backend UserRead. Only the fields the extension uses; the response carries
// more (id, email, latest_spend_report) that the popup has no need for.
export interface UserSettings {
  // Whether the user wants offers that require registering for a campaign first.
  // A global preference: the backend only considers one kind per recommendation,
  // and undertakes to tell the user when registration is needed.
  registration_campaigns_enabled: boolean;
}

export type CheckoutContext = Omit<CheckoutRequest, 'requestId'>;
export type UnavailableReason = 'credit-unavailable' | 'product-missing' | 'amount-missing';

// Why a recommendation could not be shown. These are distinct because the user
// has to do something different about each: sign in, add a card, or just retry.
// 'failed' is anything transient (network, 5xx, malformed reply).
export type FailureReason = 'signed-out' | 'no-cards' | 'failed';

// Creating the reminder is a separate request with its own outcomes. 'not-connected'
// is the backend's 409: the user has not linked a calendar yet, which retrying
// cannot fix, so it is kept distinct from a transient failure.
export type ReminderState =
  | { status: 'idle' }
  | { status: 'saving' }
  | { status: 'saved'; alreadyNotified: boolean }
  | { status: 'error'; reason: 'not-connected' | 'signed-out' | 'failed' };

// The `stage` values of the backend's `searching` events
// (backend/app/api/v1/routes/recommendations.py). Unknown stages are ignored
// rather than shown, so a new one added server-side cannot break the panel.
export type SearchStage =
  | 'preprocessing'
  | 'live_card_lookup'
  | 'reprocessing'
  | 'official_verification';

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
  // `stage` mirrors the backend's `searching` events. The worst case is a live
  // official lookup plus a model run -- minutes, not seconds -- so the panel says
  // which part is happening rather than showing one frozen sentence.
  | { status: 'loading'; context: CheckoutContext; stage?: SearchStage }
  | {
    status: 'success';
    context: CheckoutContext;
    result: Recommendation;
    wait?: WaitSuggestion | null;
  }
  | { status: 'error'; context: CheckoutContext; reason: FailureReason };
