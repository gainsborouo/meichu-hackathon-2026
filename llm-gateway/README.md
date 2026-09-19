# LLM gateway

This is the team's private, OpenAI-compatible LLM gateway. It pins
`@jeffreycao/copilot-api` to `2.5.3`, publishes its port only on localhost,
and is ready for Nginx to expose only `/v1/` once DNS is available.

## First-time setup on this machine

Docker and Docker Compose are already available. Build the image (this installs
Node 22 and the gateway dependency inside the image):

```bash
cd llm-gateway
docker compose build
```

Log the server into the GitHub Copilot account. Do this only on this machine;
never share the GitHub credential with teammates:

```bash
docker compose run --rm copilot-api npm run auth:login
```

Create a separate gateway key for each teammate. Save the printed key in your
team's password manager before running the next command:

```bash
openssl rand -hex 32
docker compose run --rm copilot-api npm run keys:add -- 'PASTE_THE_KEY_HERE'
```

Then start the service:

```bash
docker compose up -d
docker compose logs -f copilot-api
```

Verify that it listens only locally:

```bash
curl http://127.0.0.1:4141/v1/models \
  -H 'Authorization: Bearer YOUR_GATEWAY_KEY'
```

Use the model IDs returned by `/v1/models`; do not hard-code a Gemini model
name, because availability depends on the Copilot plan.

## After DNS is ready

1. Point the `A` record for `mcllmh.userwei.com` to this machine's public IPv4
   address (and an `AAAA` record if you use IPv6).
2. Install Nginx and Certbot on the host:

   ```bash
   sudo apt update
   sudo apt install -y nginx certbot python3-certbot-nginx
   ```

3. Install `nginx.conf.example`, test it, and enable the site:

   ```bash
   sudo cp nginx.conf.example /etc/nginx/sites-available/llm-gateway
   sudo ln -s /etc/nginx/sites-available/llm-gateway /etc/nginx/sites-enabled/llm-gateway
   sudo nginx -t
   sudo systemctl reload nginx
   ```

4. Issue the HTTPS certificate after DNS resolves publicly:

   ```bash
   sudo certbot --nginx -d mcllmh.userwei.com
   ```

Only ports 80 and 443 should be opened in the firewall. Do not open 4141.

## Teammate client

Give each teammate only these values:

```text
base URL: https://mcllmh.userwei.com/v1
API key:  <their individual gateway key>
```

The endpoint is compatible with the OpenAI SDK:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://mcllmh.userwei.com/v1",
    api_key="TEAMMATE_GATEWAY_KEY",
)

response = client.chat.completions.create(
    model="MODEL_ID_FROM_V1_MODELS",
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.choices[0].message.content)
```
