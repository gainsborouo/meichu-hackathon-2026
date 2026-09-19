---
name: spend-report-summary
description: Merge a user's last three months of per-card statement analyses into one short Traditional Chinese spending summary, written to users.latest_spend_report. Use this whenever a summary across months or across several cards is wanted — 近三個月消費、消費總結、spending summary, "how have I been spending lately", a dashboard overview, or refreshing latest_spend_report after new analyses land. Reach for it even when the request just says "總結" or "summarize my spending", and prefer it over re-reading raw statements: the per-card analyses are already computed, and this rolls them up rather than starting over.
---

# Spend report summary

Collapse a grid of per-card, per-month analyses into one thing a person reads in
ten seconds and learns something from.

`user_analyses` holds one row per (card, month). Someone with two cards and three
months has six rows; concatenating them is what the cache used to do, and it
produces a wall of text nobody reads. The job here is the opposite: find the few
things worth saying, and say them.

## The shape of the answer

A heading, two or three sentences of insight, then a month table. The table is
generated, not typed — it is the part where a transposed digit would quietly
misinform someone about their own money.

```markdown
## 近三個月消費總結

八月支出 NT$6,928，較七月增加約一倍，主要是樂天市場單筆 2,900 元與 KKTIX 售票
1,880 元；六月的交通支出在七月之後歸零，看起來是一趟日本旅行結束。
ANTHROPIC 訂閱每月固定約 640 元，三個月累計 1,924 元。

| 月份 | 支出 | 最大類別 |
|---|---|---|
| 2026-06 | TWD 5,420 | 交通（TWD 2,965） |
| 2026-07 | TWD 3,360 | 交通（TWD 990） |
| 2026-08 | TWD 6,928 | 網購（TWD 2,900） |

- 每月固定扣款：ANTHROPIC* CLAUDE SUB（3 個月共 TWD 1,924）
- 最大單筆：樂天市場 TWD 2,900（2026-08，玉山銀行 Pi 信用卡）
```

Write in Traditional Chinese, because that is what the statements and the users
are in. If every analysis in the window is in another language, follow it.

## Step 1: Collect the rows

The input is the user's analyses for their most recent three months, across all
their cards — in the backend that is `analyses_repo.latest_months_for_user(...)`.
Each row carries `analysis_month`, the card it belongs to, the written `report`,
and `analysis_data` holding the structured summary the statement skill produced.

Shape them as `{"rows": [{analysis_month, card: {bank_name, name}, report,
analysis_data}, ...]}`.

## Step 2: Aggregate

```bash
python scripts/aggregate_analyses.py rows.json -o facts.json
```

This sums across cards within each month, tracks how each category moved over the
window, finds merchants charging in more than one month, and picks out the largest
single transaction. Doing this by hand across six or nine rows is exactly where a
wrong number slips in, and a summary that misstates someone's spending is worse
than no summary.

`facts.json` also carries `data_quality.notes` — fewer than three months of data,
analyses with no structured payload, mixed currencies. Read them; they change what
you are allowed to claim.

## Step 3: Render the skeleton

```bash
python scripts/render_report.py facts.json
```

Returns the heading, the summary line, the table and the bullet facts, all
deterministic. Keep this output as the backbone of your report rather than
retyping it — every figure in it came from the aggregation.

## Step 4: Write the insight

The generated skeleton states what happened. Your sentences say what it means,
and that is the whole reason a person reads the summary rather than the table.

Aim for two or three sentences placed under the heading, above the table. What
earns a place there:

- **The movement that dominates the window.** Not "spending went up 28%" alone,
  but what drove it — one large purchase, a category that appeared, a trip.
- **A pattern that repeats.** A subscription charging every month is the finding
  people react to most, because it is the one they had forgotten.
- **Something that stopped.** A category that was large and went to zero usually
  has a story: a trip ended, a policy renewed annually, a habit changed.
- **What distorts the picture.** An annual insurance premium or a one-off
  big-ticket item makes a month look unrepresentative, and saying so is more
  useful than letting the number stand alone.

Ground every figure in `facts.json`. If you want to say something the facts do
not support — that a trip happened, that a purchase was a gift — mark it as a
reading of the data ("看起來", "可能"), not as fact. The difference matters when
someone acts on it.

Stay observational. The person asked where their money went, not for a verdict on
their habits. One concrete suggestion is welcome when the data plainly supports
it; a lecture is not.

## When the data is thin

A user with one month of analyses still deserves an answer. Say what that month
holds, note that there is not yet enough history for a trend, and stop. Padding a
single month into a three-month narrative invents a pattern that is not there.

With no analyses at all, the honest output is a short line saying so — not an
empty report and not a fabricated one.
