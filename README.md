# Meichu Hackathon 2026

## Services

- [`llm-gateway`](./llm-gateway/): private OpenAI-compatible team LLM gateway.

## Local setup and recommendation testing

Requirements: Docker Desktop is running, and `backend/.env` defines `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`. Never commit `.env` files or tokens.

Start the services, apply database migrations, and import the committed campaign data:

```bash
docker compose -f compose.dev.yaml up -d --build
docker compose -f compose.dev.yaml exec backend python -m alembic upgrade head
docker compose -f compose.dev.yaml exec backend python -m app.cli.import_sales
```

Verify that all ten crawler-enabled cards have a usable base benefit:

```bash
docker compose -f compose.dev.yaml exec backend python -m app.cli.verify_benefit_coverage --require ctbc-linepay,esun-kumamon,esun-pi-card,esun-ubear,esun-unicard,fubon-costco,fubon-j,fubon-momo,taishin-pxmart,taishin-richart
```

The final line should be `coverage: 10/10 cards have a base benefit`. Open `http://localhost`, sign in, then add cards you hold from **Card Management**.

### Recommendation test

The frontend supports card management. The registration preference is currently set through the API. These commands use **fish** syntax:

```fish
set API http://localhost:8000/api/v1
set AUTH "Authorization: Bearer $FIREBASE_ID_TOKEN"

# Do not register for campaigns: allow base benefits and no-registration offers only.
curl -s -X PATCH "$API/me" -H "$AUTH" -H "Content-Type: application/json" -d '{"registration_campaigns_enabled":false}' | jq
```

On the home page, enter `PX Mart` / `laundry detergent` / `500`. If the account holds only LINE Pay, the result must recommend LINE Pay. A recommendation must never name a card that is not in the user's wallet.

Test registration-required campaigns:

```fish
curl -s -X PATCH "$API/me" -H "$AUTH" -H "Content-Type: application/json" -d '{"registration_campaigns_enabled":true}' | jq
```

Submit the same purchase again. If a result is returned, it must require registration. Returning no recommendation is correct when no eligible campaign exists; the service must not fall back to a base benefit.

Stop the containers when finished. This preserves the local PostgreSQL volume:

```bash
docker compose -f compose.dev.yaml down
```
