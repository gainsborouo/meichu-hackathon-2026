# Official source policy

A pick is `verified` only when backed by a page on the **issuing bank's own domain**
**that the backend opened successfully** through `open_official_page`. Search results
only prove a URL was listed, and a URL you cite from memory proves nothing: the backend
compares your `official_sources` with the pages it really opened and drops the rest.

`open_official_page(url)` performs a real GET from the backend. It succeeds only for an
https URL on a supported bank domain (redirects included; a redirect off the allow-list
fails), for a public host, with a 2xx text/HTML response that has readable text. It
returns `OPENED <final url>` plus the page text, or `NOT OPENED: <reason>`.

## Accepted

The hostname equals, or is a subdomain of, a domain the backend lists for that bank
(`app/services/official_sources.py`), served over https. Currently:

| Bank | Domains |
|---|---|
| 中國信託 | ctbcbank.com |
| 玉山銀行 | esunbank.com, esunbank.com.tw |
| 台北富邦 | fubon.com, taipeifubon.com.tw |
| 國泰世華 | cathaybk.com.tw |
| 台新銀行 | taishinbank.com.tw |

## Not accepted as verification

News sites, blogs, PTT, Dcard, forums, comparison or coupon aggregators, the store's
own page (momo, PChome, Shopee), social media, search-result snippets, and URLs on look-alike
domains such as `ctbcbank.com.evil.example`.

Third-party pages may help you understand a campaign, but never list them in
`official_sources`.

## What verification does

When a pick has at least one allow-listed URL that the backend opened, it stamps that campaign's
`official_verified_at` (later requests see it in the candidate). No opened
official page means no stamp and `unverified`.

Live search only verifies and supplements campaigns that already exist. It never creates
new campaigns; the crawler plus `import_sales` remain the source of truth for what
campaigns exist.

## Practice

- Search with product / store / card name / bank / campaign terms only. No email, no card
  list, no spend data, and no prices or amounts. The search tool refuses such queries
  (and text copied from the spending summary) instead of sending them.
- Call `open_official_page` on the official result and check the campaign is current and
  matches the candidate's `conditions`. Opening proves the page is reachable and readable;
  whether it says what the candidate says is your judgement, so read it. If the official page contradicts the candidate, prefer `null` for that pick
  and say so in the reason.
- No official page found or `NOT OPENED`: return `official_sources: []`. Do not lower the
  bar.
- Verification is about the campaign existing as described, not about your ranking.
