# Recommendation demo scenarios

These scenarios use campaign data generated on 2026-09-19. Campaigns are time-sensitive; run the local setup in the root [README](README.md) before the demo.

## Prerequisites

Use a test account. For deterministic results, the account must hold **only the card listed for the current scenario**. Do not remove a card that has statement analyses attached to it; use a separate test account instead.

Open `http://localhost/cards`, sign in, and add the listed card. The API commands below use fish syntax:

```fish
set API http://localhost:8000/api/v1
set AUTH "Authorization: Bearer $FIREBASE_ID_TOKEN"
```

Each response is an SSE stream. Inspect the `event: recommendation` JSON payload.

## 1. No registration: Fubon momo Card on momo

In **Card Management**, add only **Fubon momo Card** (`台北富邦 momo 卡`).

Set the no-registration preference:

```fish
curl -s -X PATCH "$API/me" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"registration_campaigns_enabled":false}' | jq
```

Request a recommendation:

```fish
curl -sN -X POST "$API/recommendations/stream" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "store_name": "momo",
    "product_name": "AirPods Pro",
    "price": 7490,
    "currency": "TWD"
  }'
```

Expected result:

- `mode` is `no_registration`.
- `best_now.card.name` is `momo 卡`.
- `campaign_title` is `momo通路消費享最高3% mo幣回饋`.
- `rate_display` is `3%`, `estimated_reward_twd` is `224.7`.
- `requires_registration` is `false`.

## 2. Registration required: Fubon Costco Card for Taiwan HSR

In **Card Management**, add only **Fubon Costco Card** (`台北富邦 Costco 聯名卡`).

Set the registration-only preference:

```fish
curl -s -X PATCH "$API/me" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"registration_campaigns_enabled":true}' | jq
```

Request a recommendation:

```fish
curl -sN -X POST "$API/recommendations/stream" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "store_name": "台灣高鐵",
    "product_name": "高鐵車票",
    "price": 3000,
    "currency": "TWD"
  }'
```

Expected result:

- `mode` is `registration`.
- `best_now.card.name` is `Costco 聯名卡`.
- The selected offer is `高鐵/臺鐵購票最高8%回饋`.
- `requires_registration` is `true`.
- The response must not select a base benefit or a no-registration offer.

The real offer requires prior-month qualifying spend and registration; this is a product-flow test, not a claim that the simulated purchase qualifies in real life.

## 3. Wait for a future campaign: E.SUN Kumamon Card

In **Card Management**, add only **E.SUN Kumamon Card** (`玉山熊本熊卡`). Keep the registration-only preference enabled.

Request a recommendation:

```fish
curl -sN -X POST "$API/recommendations/stream" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "store_name": "日本人氣主題樂園",
    "product_name": "主題樂園門票",
    "price": 10000,
    "currency": "TWD"
  }'
```

Expected result as of 2026-09-20:

- `wait_suggestion.card.name` is `熊本熊卡`.
- The future offer is `日本人氣主題樂園 享最高20%現金回饋`.
- `wait_suggestion.starts_at` is `2026-10-01`.
- `wait_suggestion.calendar_draft` contains a title, `starts_at`, and notes.

The API and frontend currently expose a **calendar draft only**. They do not create a Google Calendar event and the frontend has no “Add to Calendar” button yet. The offer also requires advance registration and a qualifying JPY 10,000 spend; the TWD value above is only used to exercise the current recommendation threshold.

## 4. No registration: E.SUN U Bear Card on Shopee

In **Card Management**, add only **E.SUN U Bear Card** (`玉山 U Bear 信用卡`). Remove the cards used in the prior scenarios, then keep the no-registration preference enabled.

```fish
curl -s -X PATCH "$API/me" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"registration_campaigns_enabled":false}' | jq

curl -sN -X POST "$API/recommendations/stream" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "store_name": "蝦皮",
    "product_name": "藍牙耳機",
    "price": 2000,
    "currency": "TWD"
  }'
```

Expected result as of 2026-09-20:

- `mode` is `no_registration`.
- `best_now.card.name` is `U Bear 信用卡`.
- The selected offer is `掃貨熊給力 網路消費最高享3%現金回饋`.
- `rate_display` is `3%` and `estimated_reward_twd` is `60` (the monthly extra-reward cap is NT$150).
- `requires_registration` is `false`.

The actual U Bear online-shopping bonus requires paperless billing. This scenario tests the recommendation flow; the user must still meet the bank's real eligibility conditions.

## Expected safety properties in every scenario

- The recommendation must name a card held by the test account.
- No-registration mode must not return an offer that requires registration.
- Registration-only mode must not fall back to a base benefit.
- `verification_status: "verified"` must include an official bank source URL.
