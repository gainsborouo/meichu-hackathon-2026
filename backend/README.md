# Backend

FastAPI service for meichu-hackathon-2026.

## Layout

```
app/
  main.py            # app factory + middleware
  api/v1/router.py   # aggregates v1 routers
  api/v1/routes/     # endpoint modules
  core/config.py     # settings (env / .env)
  models/            # persistence models
  schemas/           # pydantic request/response models
  services/          # business logic
tests/
```

## Local development

```bash
uv sync
uv run uvicorn app.main:app --reload
```

Docs at http://localhost:8000/docs, health at `/api/v1/health`.

## Tests and lint

```bash
uv run pytest
uv run ruff check .
```

## Docker

```bash
docker build -t meichu-backend .
docker run --rm -p 8000:8000 meichu-backend
```

## Compose

`compose.yaml` lives at the repo root, so run these from there:

```bash
docker compose up --build       # start the API on http://localhost:8000
docker compose watch            # same, but syncs app/ and restarts on edits
docker compose down
```

`BACKEND_PORT` overrides the published port; `backend/.env` is loaded if present.
