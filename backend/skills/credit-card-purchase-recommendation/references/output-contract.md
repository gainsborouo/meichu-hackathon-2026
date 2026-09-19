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

- `best_now.candidate_id` must be in `now_candidates`; `best_future.candidate_id` must be
  in `future_candidates`. Anything else is rejected and the whole answer is discarded.
- `best_now` is `null` when `now_candidates` is empty or none plainly applies. Then
  `explanation` says why, without recommending anything else.
- `best_future` is `null` when `future_candidates` is empty or none is worth considering.
- `official_sources` holds only URLs you actually saw on the bank's own domain. An empty
  list is valid and correct when you found none.
- Do not include reward amounts, rates, caps, or dates. The backend attaches its own,
  and any numbers you add are ignored.

## What the backend does with your answer

- Attaches rate, cap, estimated reward, registration data from the candidate.
- Sets `verification_status` to `verified` only if at least one of your
  `official_sources` is on the bank's official domain; otherwise `unverified`.
- Turns `best_future` into `wait_suggestion` only if its estimated reward is higher than
  `best_now`'s, it is verified, and it has an explicit start date. Otherwise
  `wait_suggestion` is `null`, whatever you picked.
- Stamps `official_verified_at` on each picked campaign that was verified.
- Builds the Calendar draft. Nothing is written to any calendar.
