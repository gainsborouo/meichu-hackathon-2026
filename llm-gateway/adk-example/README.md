# ADK example

Minimal Google ADK agent that calls any OpenAI-compatible endpoint. It includes
one local Python tool that looks up the current time, so you can verify an ADK
tool call end to end.

## Run

```bash
cp .env.example .env
# Edit .env and replace LLM_API_KEY with your own API key.
uv run adk-example
```

Pass a custom prompt:

```bash
uv run adk-example "What time is it in America/New_York?"
```

The custom prompt above is useful for confirming that the model can call the
example `get_current_time` tool.

`uv run` automatically creates the virtual environment and installs the locked
dependencies. The real `.env` is ignored by Git; commit only `.env.example`.

Each run logs when the request was sent, when its first model event arrived,
and its total end-to-end duration. Logs use the standard timestamped format and
go to stderr, so normal model output remains on stdout.

## Why LiteLLM?

Google ADK's native Gemini client expects a Google AI Studio or Vertex AI
credential. This project instead uses ADK's LiteLLM adapter because it can call
an OpenAI-compatible `/v1/chat/completions` endpoint.
