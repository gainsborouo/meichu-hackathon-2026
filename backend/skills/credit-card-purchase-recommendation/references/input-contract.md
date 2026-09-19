# Input contract

The user message is one JSON object built by the backend.

```
{
  "today": "2026-09-19",
  "mode": "no_registration" | "registration",
  "request": {"product_name": "AirPods Pro", "store_name": "momo", "price": 7490, "currency": "TWD", "locale": "zh-TW" | "en-US"},
  "held_cards": [{"id": "uuid", "bank_name": "玉山銀行", "name": "Unicard"}],
  "now_candidates": [Candidate],
  "future_candidates": [Candidate],
  "spend_context": {"latest_spend_report": "text or null", "recent_analyses": [...]}
}
```

`locale` controls the language of `reason` and `explanation`. It does not translate card,
bank, campaign, or official-source titles.

## mode

Strict and mutually exclusive.

- `no_registration`: every candidate is a campaign that needs **no** registration.
- `registration`: every candidate is a campaign that **requires** registration.

There is no fallback between modes. Never mention the other kind of campaign.

## Candidate

One candidate is one reward rule of one campaign for one held card.

| Field | Meaning |
|---|---|
| `candidate_id` | The only handle you may select by. |
| `card` | `{id, bank_name, name, artwork_id}` of a held card. |
| `sale_id`, `title` | The campaign. |
| `campaign_start`, `campaign_end` | ISO dates or null. Null means the source gave none. |
| `is_future` | True when `campaign_start` is after today. |
| `rate`, `rate_display` | Reward rate used for the estimate, e.g. `0.03` / `"3%"`. |
| `rate_max_display` | The headline "up to" rate, if different. Marketing figure, not the estimate. |
| `cap_description`, `cap_applied` | Cap text, and whether the estimate was reduced by it. |
| `min_spend` | Minimum spend already satisfied by this price. |
| `estimated_reward_twd` | Precomputed estimate for this exact price. Quote as is. |
| `requires_registration`, `registration_url` | Registration facts. |
| `conditions` | The campaign clause the rule was extracted from. |
| `categories`, `platforms`, `platform_match` | Scope tags; `platform_match` is true when the store matches a platform the rule names. |
| `source_url` | The crawler's URL for this campaign. Not verification. |
| `official_verified_at` | Set only after a past official check; usually null. |

Candidates are unranked and in arbitrary order.

## spend_context

`latest_spend_report` is a text summary of the last three months. `recent_analyses` holds
structured per-month analysis. Both are background only.
