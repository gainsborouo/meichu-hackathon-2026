---
name: credit-card-statement-analysis
description: Turn images of credit-card statements or transaction history into a structured spending breakdown (categories, totals, top merchants, recurring charges) plus a short written analysis of consumption habits. Use this whenever someone sends a screenshot or photo of a card statement, bank transaction list, 信用卡帳單 or 消費明細 — including when they send the image with no text at all, which is the normal case. Handles several months at once and reports the trend between them. Also use when building an agent, tool, or API endpoint that turns statement images into spending data.
---

# Credit-card statement analysis

Read statement images, turn them into trustworthy structured data, then say something
useful about how the person spends.

The output is two things, in this order:

1. **`summary.json`** — the machine-readable breakdown an API or UI consumes.
2. **A short narrative** — 3-5 sentences a person actually reads, grounded in that JSON.

## The image is the whole request

People send a statement photo with no message attached. That is not an ambiguous request
waiting on clarification — it means "tell me about this." Do the full analysis and lead
with what they most likely want to know: where the money went, and anything surprising in
it. Don't open by asking what they'd like done with the image.

If several images arrive, work out what they are before transcribing:

- **Pages of one statement** (`續下頁`, `2/3`) — one statement, transcribed across images.
- **Different months** (each with its own period and its own 本期新增款項 total) — analyze
  each separately, then compare them. The comparison is usually the most valuable part:
  a single month says what someone bought, several months say what they *keep* buying.

## Why this is structured as transcribe → aggregate → interpret

The failure mode that matters is a confident summary built on wrong numbers. Someone
acting on "you spent NT$8,420 on dining" deserves that to be the real figure. So the work
splits by what each part is good at: read the image carefully (vision), do the arithmetic
in a script (deterministic), then interpret the result (judgment). Summing forty amounts
in your head is where silent errors creep in — don't.

## Step 1: Transcribe every row

Go row by row and write each transaction down verbatim. Resist categorizing or totalling
while reading; transcription and interpretation are separate passes, and mixing them is
how rows get skipped.

Write one `transactions.json` per statement:

```json
{
  "currency": "TWD",
  "statement": {
    "issuer": "玉山銀行",
    "card_last4": "8223",
    "period_start": "2026-08-01",
    "period_end": "2026-08-31",
    "printed_new_charges": 5728
  },
  "transactions": [
    {
      "date": "2026-08-03",
      "description": "POVO CHARGES JPN TOKYO",
      "merchant": "POVO CHARGES",
      "amount": 184,
      "category": "utilities_telecom",
      "type": "purchase",
      "foreign": {"amount": 900, "currency": "JPY"},
      "mobile_payment": false,
      "confidence": "high"
    }
  ]
}
```

### Rows that are not consumption

Taiwanese statements open with balance and payment lines that look like transactions and
are not. Counting them roughly doubles the reported spend, which is the single worst error
this skill can make:

- **`上期應繳金額`** (previous balance due) — an opening balance, not a purchase. Leave it
  out of `transactions` entirely.
- **`感謝您辦理本行自動轉帳繳款！`** or similar — the cardholder paying their bill, usually
  printed as a negative amount. Record it with `type: "payment"` and a positive `amount`;
  the script excludes it from spend and reports it separately.
- **Promotional banners** at the top of the page (card offers, 回饋 percentages, event
  ads). These are advertising, never transactions.

### Field rules that prevent specific mistakes

- **`amount` is always a positive magnitude.** Direction comes from `type`, never a minus
  sign. Statements mark money-back inconsistently (`-3,360`, `500 CR`, `退款`), and letting
  each row carry its own sign convention is how totals end up backwards. The script
  rejects negative amounts for this reason.
- **`type`** is one of `purchase`, `installment`, `fee`, `interest`, `refund`, `payment`.
- **Use 消費日 (transaction date), not 入帳日 (posting date).** The posting date is the
  bank's bookkeeping; when someone actually spent the money is what describes behavior.
  Note a transaction dated after the period end (a 08/02 row on a July statement) is
  normal — keep its real date.
- **Foreign currency: `amount` is the settled TWD figure** from the 繳款幣別 column, and
  `foreign` holds the original from the 幣別 column. Totalling mixed currencies produces
  nonsense, so the settled column is the one that aggregates.
- **`國外交易服務費` rows** follow their parent transaction, one per foreign purchase. They
  are real charges: `type: "fee"`, category `financial_fees`. They add up — five of them
  in a travel month is worth mentioning.
- **`mobile_payment: true`** when the 行動支付 column is marked (often `A`). It says
  something about how someone pays, which the amounts alone don't.
- **`confidence": "low"`** on anything blurry, cut off, or ambiguous. Flagged uncertainty
  is useful; silent guessing is not.
- **`printed_new_charges`** is the statement's own total (`本期新增款項` / `本期合計`).
  Transcribe it whenever visible. It is the best check available: the script compares it
  against the computed total and tells you if a row was missed.

### Dates without a year

Statements often print `08/03` with no year anywhere on the page. Take the year from the
statement period if it's shown. If it isn't, choose the year that places the statement in
the recent past, use it consistently, and say in the narrative that the year was inferred.
A consistent assumed year costs nothing for a within-statement analysis; an inconsistent
one silently scrambles the ordering.

### Merchant names

`merchant` is the cleaned name, `description` keeps the raw text. Two conventions worth
knowing, because grouping depends on getting them right:

- **Payment-gateway prefixes.** `藍新－ＫＫＴＩＸ售票報名平台` is KKTIX billing through
  NewebPay. The merchant is `ＫＫＴＩＸ`; 藍新 is plumbing.
- **Legal entity after `＊`.** `樂天市場 ＊台灣樂天國際貿易股份有限公司` is 樂天市場. Keep
  the brand, drop the company registration.

If the image is too low-resolution to read amounts, say so and ask for a clearer one
rather than transcribing guesses. Refusing to invent numbers beats coverage.

## Step 2: Categorize each row

Assign `category` while transcribing, using the taxonomy in `references/categories.md` —
read it now if you haven't. Use `uncategorized` for genuinely opaque merchants; the script
counts those, so a weak summary looks weak instead of falsely confident.

## Step 3: Aggregate

```bash
python scripts/analyze_transactions.py transactions.json -o summary.json
```

It computes totals (gross, refunds, net, fees), per-category amounts and shares, merchants
ranked by spend, repeat merchants and likely subscriptions, weekday/weekend split, mobile
payment share, largest transaction, a per-day series for charting, a foreign-currency
rollup, and the reconciliation check. It exits non-zero with a specific message when the
input is malformed — fix the transcription and rerun rather than working around it.

**If `reconciliation.matches` is false, go back to the image.** The gap is almost always a
row you missed or misread, and it's the one signal that catches errors you can't see.

## Step 4: Compare, when there is more than one statement

```bash
python scripts/compare_statements.py 2026-06.json 2026-07.json 2026-08.json -o trend.json
```

This reports spend per period, category movement, merchants new or gone since the earliest
month, and `recurring_across_periods` — merchants charging in several months, flagged
`every_period` and `stable_amount` when the charge is near-identical each time. That
combination is what a subscription looks like from the outside, and it's the finding
people react to most, because it's the one they'd forgotten.

## Step 5: Write the analysis

Fill the `insights` array and write the narrative from it. Use only numbers the scripts
produced — no figure should appear that isn't in the JSON.

What makes this useful rather than a restatement of the table:

- Lead with the largest category and its share, in the person's own currency.
- Name one behavioral pattern the raw table doesn't show: a run of small transport
  top-ups, a weekend skew, nearly everything paid by phone, a month dominated by one
  purchase.
- Surface recurring and subscription charges explicitly. People forget these.
- Across months, say what *changed* and offer the likely reason the data supports —
  transport and foreign fees appearing together for one month then stopping looks like a
  trip, not a new habit.
- Note anything that distorts the picture: an annual fee, insurance, a one-off big-ticket
  purchase, a large uncategorized row.
- Mention data-quality problems first if there are any. A total that doesn't reconcile
  needs that caveat up front, not buried.

Stay observational. The person asked where their money went, not whether they should feel
bad about it — describe the pattern and let them draw the conclusion. One concrete,
non-judgmental suggestion is fine when the data clearly supports it; a lecture is not.

Keep card numbers out of the output beyond the last four digits, and don't repeat account
numbers visible in the image.

## Using this from Google ADK

The split maps onto an ADK agent directly: the model handles the images and produces rows,
function tools wrap the scripts for the arithmetic. See `references/adk-integration.md`.
