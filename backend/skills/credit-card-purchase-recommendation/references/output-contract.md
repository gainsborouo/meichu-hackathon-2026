# Output contract

Reply with one raw JSON object. No code fences.

```
{
  "best_now": {
    "candidate_id": "id from now_candidates",
    "reason": "why this card wins for this purchase",
    "official_sources": [{"title": "官方活動頁", "url": "https://..."}]
  } | null,
  "best_future": {
    "candidate_id": "id from future_candidates",
    "reason": "why waiting for this campaign could pay more",
    "official_sources": [{"title": "...", "url": "https://..."}]
  } | null,
  "explanation": "short overall note; required when best_now is null"
}
```

## Rules

- Write `reason` and `explanation` in `request.locale`: Traditional Chinese for `zh-TW`
  and concise American English for `en-US`. Keep source titles in their original language.
- `best_now.candidate_id` must be in `now_candidates`; `best_future.candidate_id` must be
  in `future_candidates`. Anything else is rejected and the whole answer is discarded.
- `best_now` is `null` only when `now_candidates` is empty or none plainly applies. A base
  benefit that fits the purchase is a valid pick: do not return `null` just because no
  campaign matches. When you do return `null`, `explanation` says why, without
  recommending anything else.
- `best_future` is `null` when `future_candidates` is empty or none is worth considering.
- `official_sources` holds only the final URLs that `open_official_page` reported after
  `OPENED`. The
  backend discards any URL it did not open itself. An empty
  list is valid and correct when you found none.
- Do not include reward amounts, rates, caps, or dates. The backend attaches its own,
  and any numbers you add are ignored.

## What the backend does with your answer

- Attaches rate, cap, estimated reward, registration data and `candidate_type` from the
  candidate.
- Sets `verification_status` to `verified` only if at least one of your
  `official_sources` is on the bank's official domain **and** was opened successfully by the
  backend during this run; otherwise `unverified`.
- Turns `best_future` into `wait_suggestion` only if its estimated reward is higher than
  `best_now`'s, it is verified, and it has an explicit start date. Otherwise
  `wait_suggestion` is `null`, whatever you picked.
- Stamps `official_verified_at` on each picked campaign that was verified.
- Builds the Calendar draft. Nothing is written to any calendar.
