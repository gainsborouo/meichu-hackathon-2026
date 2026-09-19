# Official source policy

A pick is `verified` only when backed by a page on the **issuing bank's own domain**.

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

When a pick has at least one allow-listed URL, the backend stamps that campaign's
`official_verified_at` (later requests see it in the candidate). No allow-listed URL
means no stamp and `unverified`.

Live search only verifies and supplements campaigns that already exist. It never creates
new campaigns; the crawler plus `import_sales` remain the source of truth for what
campaigns exist.

## Practice

- Search with product / store / card name / bank / campaign terms only. No email, no card
  list, no spend data.
- Open the official result and check the campaign is current and matches the candidate's
  `conditions`. If the official page contradicts the candidate, prefer `null` for that pick
  and say so in the reason.
- No official page found: return `official_sources: []`. Do not lower the bar.
- Verification is about the campaign existing as described, not about your ranking.
