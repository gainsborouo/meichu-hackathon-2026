FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates

WORKDIR /srv/llm-gateway

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

COPY . ./

EXPOSE 4141

CMD ["npm", "run", "start"]
