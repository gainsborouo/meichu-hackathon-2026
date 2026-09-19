<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { CreditCard, ExternalLink, LoaderCircle, Search, TriangleAlert } from '@lucide/vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'

import SiteHeader from '../components/SiteHeader.vue'
import { creditCardArtworkCatalog, type CreditCardArtwork } from '../data/creditCardArtwork'
import { api } from '../services/api'
import { useAuthStore } from '../stores/authStore'

interface SearchRequest {
  price: number
  platform: string
  category: string
  currency: 'TWD'
  include_unowned: boolean
}

interface CardSummary {
  id: string
  bank_name: string
  name: string
}

interface EstimatedReward {
  amount: number
  rate: number
  rate_max: number
  currency: string
  unit: string
  capped: boolean
  requires_registration: boolean
  source_text: string
}

interface MatchedSale {
  id: string
  card_id: string
  bank_name: string
  card_name: string
  title: string
  reward: string
  conditions: string
  campaign_period: string
  register_url: string
  source_url: string
  evidence: string
}

interface Recommendation {
  user_card_id: string
  owned: boolean
  card: CardSummary
  estimated_reward: EstimatedReward
  matched_sales: MatchedSale[]
  reason: string
}

interface SearchResponse {
  query: SearchRequest
  resolved_category: string
  best: Recommendation
  alternatives: Recommendation[]
  considered_card_count: number
}

type SearchState = 'idle' | 'loading' | 'success' | 'error'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const { ready: authReady, user: authUser } = storeToRefs(authStore)

const platform = ref('')
const amount = ref('')
const category = ref('')
const platformError = ref('')
const amountError = ref('')
const categoryError = ref('')
const searchState = ref<SearchState>('idle')
const searchResult = ref<SearchResponse | null>(null)
const requestError = ref('')
const failedImageIds = ref(new Set<string>())
let activeController: AbortController | null = null

const formattedAmount = computed(() => amount.value.replace(/\B(?=(\d{3})+(?!\d))/g, ','))

function normalizeCardText(value: string) {
  return value.normalize('NFKC').trim().replace(/\s+/g, ' ')
}

function artworkKey(bankName: string, cardName: string) {
  return `${normalizeCardText(bankName)}\u0000${normalizeCardText(cardName)}`
}

const artworkByCard = new Map<string, CreditCardArtwork>()

for (const artwork of creditCardArtworkCatalog) {
  const key = artworkKey(artwork.issuer, artwork.cardName)
  if (!artworkByCard.has(key)) artworkByCard.set(key, artwork)
}

const rankedRecommendations = computed(() => {
  if (!searchResult.value) return []

  return [searchResult.value.best, ...searchResult.value.alternatives].map(
    (recommendation, index) => ({
      artwork: artworkByCard.get(
        artworkKey(recommendation.card.bank_name, recommendation.card.name),
      ),
      rank: index + 1,
      recommendation,
    }),
  )
})

function queryString(value: LocationQueryValue | LocationQueryValue[] | undefined) {
  return typeof value === 'string' ? value : ''
}

function syncFormFromRoute() {
  platform.value = queryString(route.query.platform)
  amount.value = queryString(route.query.price)
  category.value = queryString(route.query.category)
}

function isValidAmount(value: string) {
  return /^[1-9]\d*$/.test(value) && Number.isSafeInteger(Number(value))
}

function validateSearch() {
  platformError.value = platform.value.trim() ? '' : '請輸入消費地點'
  amountError.value = isValidAmount(amount.value) ? '' : '請輸入有效且大於 0 的消費金額'
  categoryError.value = category.value.trim() ? '' : '請輸入品項或類別'

  return !platformError.value && !amountError.value && !categoryError.value
}

function currentQuery() {
  return {
    platform: platform.value.trim(),
    price: amount.value,
    category: category.value.trim(),
  }
}

function routeHasCurrentQuery() {
  const query = currentQuery()
  return (
    queryString(route.query.platform) === query.platform &&
    queryString(route.query.price) === query.price &&
    queryString(route.query.category) === query.category
  )
}

function handlePlatformInput() {
  if (platform.value.trim()) platformError.value = ''
}

function handleAmountInput(event: Event) {
  const input = event.target as HTMLInputElement
  amount.value = input.value.replace(/\D/g, '').replace(/^0+(?=\d)/, '')
  input.value = formattedAmount.value

  if (isValidAmount(amount.value)) amountError.value = ''
}

function handleCategoryInput() {
  if (category.value.trim()) categoryError.value = ''
}

async function loadRecommendations() {
  if (!authReady.value || !validateSearch()) return

  activeController?.abort()
  const controller = new AbortController()
  activeController = controller
  searchState.value = 'loading'
  searchResult.value = null
  requestError.value = ''

  const request: SearchRequest = {
    price: Number(amount.value),
    platform: platform.value.trim(),
    category: category.value.trim(),
    currency: 'TWD',
    include_unowned: !authUser.value,
  }

  try {
    const response = await api.post<SearchResponse>('/search', request, {
      signal: controller.signal,
    })

    if (controller.signal.aborted) return

    searchResult.value = response.data
    searchState.value = 'success'
  } catch {
    if (controller.signal.aborted) return

    searchState.value = 'error'
    requestError.value = '無法取得信用卡推薦，請稍後再試。'
  } finally {
    if (activeController === controller) activeController = null
  }
}

async function submitSearch() {
  if (!validateSearch()) {
    activeController?.abort()
    searchResult.value = null
    searchState.value = 'idle'
    requestError.value = ''
    return
  }

  if (routeHasCurrentQuery()) {
    if (searchState.value !== 'loading') await loadRecommendations()
    return
  }

  await router.push({ name: 'recommendations', query: currentQuery() })
}

function prepareRouteSearch() {
  activeController?.abort()
  syncFormFromRoute()
  searchResult.value = null
  requestError.value = ''

  if (!validateSearch()) {
    searchState.value = 'idle'
    return
  }

  if (!authReady.value) {
    searchState.value = 'loading'
    return
  }

  void loadRecommendations()
}

function formatCurrency(reward: EstimatedReward) {
  try {
    return new Intl.NumberFormat('zh-TW', {
      style: 'currency',
      currency: reward.currency,
      maximumFractionDigits: 2,
    }).format(reward.amount)
  } catch {
    return `${reward.amount.toLocaleString('zh-TW')} ${reward.currency}`.trim()
  }
}

const percentFormatter = new Intl.NumberFormat('zh-TW', {
  style: 'percent',
  maximumFractionDigits: 2,
})

function formatRate(reward: EstimatedReward) {
  const rate = percentFormatter.format(reward.rate)

  return reward.rate_max > reward.rate
    ? `${rate}–${percentFormatter.format(reward.rate_max)}`
    : rate
}

function safeExternalUrl(value: string) {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : ''
  } catch {
    return ''
  }
}

function markImageFailed(artworkId: string) {
  const nextIds = new Set(failedImageIds.value)
  nextIds.add(artworkId)
  failedImageIds.value = nextIds
}

watch([() => route.fullPath, authReady], prepareRouteSearch, { immediate: true })

onBeforeUnmount(() => activeController?.abort())
</script>

<template>
  <div class="recommendations-page">
    <SiteHeader current="home" />

    <main class="recommendations-workspace" :aria-busy="searchState === 'loading'">
      <header class="page-intro">
        <div>
          <p class="page-intro__eyebrow">信用卡推薦</p>
          <h1>這筆消費的刷卡排名</h1>
          <p>調整消費條件後，可直接重新計算推薦結果。</p>
        </div>
      </header>

      <form class="query-form" novalidate aria-label="修改搜尋條件" @submit.prevent="submitSearch">
        <div class="query-field">
          <label for="recommendation-platform">消費地點</label>
          <input
            id="recommendation-platform"
            v-model="platform"
            name="platform"
            type="text"
            autocomplete="off"
            placeholder="例如：全聯、蝦皮、東京"
            :aria-invalid="platformError ? 'true' : 'false'"
            aria-describedby="recommendation-platform-message"
            @input="handlePlatformInput"
          />
          <p
            id="recommendation-platform-message"
            :class="{ 'query-field__error': platformError }"
            :role="platformError ? 'alert' : undefined"
          >
            {{ platformError }}
          </p>
        </div>

        <div class="query-field">
          <label for="recommendation-price">金額（新臺幣）</label>
          <input
            id="recommendation-price"
            name="price"
            type="text"
            inputmode="numeric"
            pattern="[0-9]*"
            autocomplete="off"
            placeholder="例如：2,500"
            :value="formattedAmount"
            :aria-invalid="amountError ? 'true' : 'false'"
            aria-describedby="recommendation-price-message"
            @input="handleAmountInput"
          />
          <p
            id="recommendation-price-message"
            :class="{ 'query-field__error': amountError }"
            :role="amountError ? 'alert' : undefined"
          >
            {{ amountError }}
          </p>
        </div>

        <div class="query-field">
          <label for="recommendation-category">品項或類別</label>
          <input
            id="recommendation-category"
            v-model="category"
            name="category"
            type="text"
            autocomplete="off"
            placeholder="例如：餐飲、影音、機票"
            :aria-invalid="categoryError ? 'true' : 'false'"
            aria-describedby="recommendation-category-message"
            @input="handleCategoryInput"
          />
          <p
            id="recommendation-category-message"
            :class="{ 'query-field__error': categoryError }"
            :role="categoryError ? 'alert' : undefined"
          >
            {{ categoryError }}
          </p>
        </div>

        <button class="query-submit" type="submit" :aria-busy="searchState === 'loading'">
          <LoaderCircle
            v-if="searchState === 'loading'"
            class="spinner"
            :size="18"
            aria-hidden="true"
          />
          <Search v-else :size="18" aria-hidden="true" />
          {{ searchState === 'loading' ? '搜尋中…' : '重新搜尋' }}
        </button>
      </form>

      <section
        v-if="searchState === 'loading'"
        class="state-panel"
        role="status"
        aria-live="polite"
      >
        <LoaderCircle class="state-panel__spinner" :size="34" aria-hidden="true" />
        <div>
          <h2>正在計算信用卡推薦</h2>
          <p>系統正在比對持卡資料與優惠活動，請稍候。</p>
        </div>
      </section>

      <section v-else-if="searchState === 'error'" class="state-panel state-panel--error">
        <TriangleAlert :size="30" aria-hidden="true" />
        <div>
          <h2 role="alert">{{ requestError }}</h2>
          <p>搜尋條件已保留，可直接重新送出。</p>
          <button type="button" @click="submitSearch">重新搜尋</button>
        </div>
      </section>

      <section
        v-else-if="searchState === 'success'"
        class="ranking-section"
        aria-labelledby="ranking-title"
      >
        <header class="ranking-heading">
          <div>
            <p>推薦結果</p>
            <h2 id="ranking-title">信用卡排名</h2>
          </div>
          <span>{{ rankedRecommendations.length }} 張</span>
        </header>

        <div class="ranking-list">
          <article
            v-for="item in rankedRecommendations"
            :key="`${item.recommendation.card.id}-${item.rank}`"
            class="recommendation-card"
            :class="{ 'recommendation-card--best': item.rank === 1 }"
          >
            <div class="recommendation-card__header">
              <div class="card-art">
                <img
                  v-if="item.artwork && !failedImageIds.has(item.artwork.id)"
                  :src="`/card-art/${item.artwork.id}.webp`"
                  :alt="`${item.recommendation.card.bank_name}${item.recommendation.card.name}卡面`"
                  width="640"
                  height="400"
                  @error="markImageFailed(item.artwork.id)"
                />
                <div v-else class="card-art__fallback">
                  <CreditCard :size="38" :stroke-width="1.5" aria-hidden="true" />
                  <span>無卡面圖片</span>
                </div>
              </div>

              <div class="card-identity">
                <div class="card-badges">
                  <span class="rank-badge">第 {{ item.rank }} 名</span>
                  <span v-if="item.rank === 1" class="best-badge">首選</span>
                  <span class="owned-badge">
                    {{ item.recommendation.owned ? '已持有' : '尚未持有' }}
                  </span>
                </div>
                <h3>{{ item.recommendation.card.name }}</h3>
                <p>{{ item.recommendation.card.bank_name }}</p>
              </div>

              <div class="reward-summary">
                <span>預估回饋</span>
                <strong>{{ formatCurrency(item.recommendation.estimated_reward) }}</strong>
                <span>{{ formatRate(item.recommendation.estimated_reward) }}</span>
              </div>
            </div>

            <div class="recommendation-card__body">
              <p class="recommendation-reason">{{ item.recommendation.reason }}</p>

              <div
                v-if="
                  item.recommendation.estimated_reward.source_text ||
                  item.recommendation.estimated_reward.unit ||
                  item.recommendation.estimated_reward.capped ||
                  item.recommendation.estimated_reward.requires_registration
                "
                class="reward-meta"
              >
                <p v-if="item.recommendation.estimated_reward.source_text">
                  {{ item.recommendation.estimated_reward.source_text }}
                </p>
                <div class="reward-flags">
                  <span v-if="item.recommendation.estimated_reward.unit">
                    {{ item.recommendation.estimated_reward.unit }}
                  </span>
                  <span v-if="item.recommendation.estimated_reward.capped">回饋有上限</span>
                  <span v-if="item.recommendation.estimated_reward.requires_registration">
                    需要登錄
                  </span>
                </div>
              </div>

              <details v-if="item.recommendation.matched_sales.length" class="matched-sales">
                <summary>
                  查看符合的優惠活動（{{ item.recommendation.matched_sales.length }}）
                </summary>
                <div class="matched-sales__list">
                  <article
                    v-for="(sale, saleIndex) in item.recommendation.matched_sales"
                    :key="`${sale.id}-${saleIndex}`"
                    class="sale"
                  >
                    <header>
                      <h4>{{ sale.title }}</h4>
                      <strong>{{ sale.reward }}</strong>
                    </header>
                    <dl>
                      <template v-if="sale.conditions">
                        <dt>活動條件</dt>
                        <dd>{{ sale.conditions }}</dd>
                      </template>
                      <template v-if="sale.campaign_period">
                        <dt>活動期間</dt>
                        <dd>{{ sale.campaign_period }}</dd>
                      </template>
                      <template v-if="sale.evidence">
                        <dt>活動依據</dt>
                        <dd>{{ sale.evidence }}</dd>
                      </template>
                    </dl>
                    <div class="sale__links">
                      <a
                        v-if="safeExternalUrl(sale.register_url)"
                        :href="safeExternalUrl(sale.register_url)"
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        前往登錄
                        <ExternalLink :size="15" aria-hidden="true" />
                      </a>
                      <a
                        v-if="safeExternalUrl(sale.source_url)"
                        :href="safeExternalUrl(sale.source_url)"
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        查看來源
                        <ExternalLink :size="15" aria-hidden="true" />
                      </a>
                    </div>
                  </article>
                </div>
              </details>
            </div>
          </article>
        </div>
      </section>
    </main>

    <footer class="page-footer">© 2026 Meichu Hackathon @ Google</footer>
  </div>
</template>

<style scoped>
.recommendations-page {
  display: flex;
  min-height: 100svh;
  flex-direction: column;
  background: var(--color-paper);
  color: var(--color-ink);
}

.recommendations-workspace,
.page-footer {
  width: min(100% - (var(--space-lg) * 2), var(--layout-max));
  margin-inline: auto;
}

.recommendations-workspace {
  display: grid;
  flex: 1;
  align-content: start;
  gap: var(--space-xl);
  padding-block: var(--space-xl) var(--space-3xl);
}

.page-intro {
  border-bottom: var(--rule-hairline) solid var(--color-rule);
  padding-block-end: var(--space-lg);
}

.page-intro__eyebrow,
.page-intro h1,
.page-intro p,
.query-field p,
.state-panel h2,
.state-panel p,
.ranking-heading h2,
.ranking-heading p,
.card-identity h3,
.card-identity p,
.recommendation-reason,
.reward-meta p,
.sale h4,
.sale dl,
.page-footer {
  margin: 0;
}

.page-intro__eyebrow,
.ranking-heading p {
  color: var(--color-accent);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 700;
  letter-spacing: 0.08em;
}

.page-intro h1 {
  margin-block-start: var(--space-xs);
  font-family: var(--font-display);
  font-size: clamp(2.25rem, 5vw, 4rem);
  font-weight: 700;
  letter-spacing: -0.04em;
  line-height: 1.05;
}

.page-intro > div > p:last-child {
  margin-block-start: var(--space-sm);
  color: var(--color-ink-2);
  line-height: 1.6;
}

.query-form {
  display: grid;
  min-width: 0;
  gap: var(--space-md);
  border: var(--rule-hairline) solid var(--color-rule);
  border-radius: var(--radius-panel);
  padding: var(--space-lg);
  background: var(--color-paper-2);
  box-shadow: var(--shadow-panel);
}

.query-field {
  display: grid;
  min-width: 0;
  gap: var(--space-xs);
}

.query-field label {
  font-size: var(--text-sm);
  font-weight: 700;
}

.query-field input {
  width: 100%;
  min-height: var(--control-height);
  min-width: 0;
  box-sizing: border-box;
  border: var(--rule-hairline) solid var(--color-rule-strong);
  border-radius: var(--radius-control);
  outline: var(--rule-focus) solid transparent;
  outline-offset: var(--rule-hairline);
  padding-inline: var(--space-md);
  background: var(--color-paper);
  color: var(--color-ink);
  font-variant-numeric: tabular-nums;
  transition:
    border-color var(--dur-short) var(--ease-out),
    outline-color var(--dur-short) var(--ease-out);
}

.query-field input:focus {
  border-color: var(--color-accent);
  outline-color: var(--color-focus);
}

.query-field input[aria-invalid='true'] {
  border-color: var(--color-error);
}

.query-field p {
  min-height: 1.5em;
  color: var(--color-muted);
  font-size: var(--text-xs);
}

.query-field p:not(.query-field__error) {
  visibility: hidden;
}

.query-field__error {
  color: var(--color-error) !important;
}

.query-submit,
.state-panel button {
  display: inline-flex;
  min-height: var(--control-height);
  align-items: center;
  justify-content: center;
  gap: var(--space-xs);
  border: var(--rule-hairline) solid var(--color-accent);
  border-radius: var(--radius-control);
  outline: var(--rule-focus) solid transparent;
  outline-offset: var(--rule-focus);
  padding-inline: var(--space-lg);
  background: var(--color-accent);
  color: var(--color-accent-ink);
  font-weight: 700;
  white-space: nowrap;
  transition:
    background-color var(--dur-short) var(--ease-out),
    transform var(--dur-micro) var(--ease-out);
}

.query-submit:focus-visible,
.state-panel button:focus-visible,
.matched-sales summary:focus-visible,
.sale__links a:focus-visible {
  outline-color: var(--color-focus);
}

.state-panel {
  display: flex;
  min-height: 17rem;
  align-items: center;
  justify-content: center;
  gap: var(--space-lg);
  border: var(--rule-hairline) solid var(--color-rule);
  border-radius: var(--radius-panel);
  padding: var(--space-xl);
  background: var(--color-paper-2);
  text-align: left;
}

.state-panel > svg {
  flex: 0 0 auto;
  color: var(--color-accent);
}

.state-panel h2 {
  font-family: var(--font-display);
  font-size: var(--text-lg);
}

.state-panel p {
  margin-block-start: var(--space-xs);
  color: var(--color-muted);
}

.state-panel button {
  margin-block-start: var(--space-md);
}

.state-panel--error > svg,
.state-panel--error h2 {
  color: var(--color-error);
}

.spinner,
.state-panel__spinner {
  animation: recommendation-spin 1s linear infinite;
}

.ranking-section,
.ranking-list {
  display: grid;
  gap: var(--space-lg);
}

.ranking-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: var(--space-md);
}

.ranking-heading h2 {
  margin-block-start: var(--space-2xs);
  font-family: var(--font-display);
  font-size: var(--text-lg);
}

.ranking-heading > span {
  color: var(--color-muted);
  font-family: var(--font-mono);
  font-size: var(--text-sm);
}

.recommendation-card {
  overflow: clip;
  border: var(--rule-hairline) solid var(--color-rule);
  border-radius: var(--radius-panel);
  background: var(--color-paper);
  box-shadow: var(--shadow-panel);
}

.recommendation-card--best {
  border-color: var(--color-accent);
}

.recommendation-card__header {
  display: grid;
  min-width: 0;
  gap: var(--space-lg);
  padding: var(--space-lg);
}

.card-art {
  display: grid;
  width: min(100%, 15rem);
  aspect-ratio: 1.6;
  overflow: hidden;
  place-items: center;
  border: var(--rule-hairline) solid var(--color-rule);
  border-radius: var(--radius-control);
  background: var(--color-paper-3);
}

.card-art img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.card-art__fallback {
  display: grid;
  place-items: center;
  gap: var(--space-xs);
  color: var(--color-muted);
  font-size: var(--text-xs);
}

.card-badges,
.reward-flags,
.sale__links {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-xs);
}

.card-badges span,
.reward-flags span {
  border: var(--rule-hairline) solid var(--color-rule);
  border-radius: var(--radius-pill);
  padding: var(--space-2xs) var(--space-sm);
  color: var(--color-muted);
  font-size: var(--text-xs);
  font-weight: 700;
}

.card-badges .rank-badge,
.card-badges .best-badge {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.card-identity {
  min-width: 0;
}

.card-identity h3 {
  margin-block-start: var(--space-sm);
  overflow-wrap: anywhere;
  font-family: var(--font-display);
  font-size: var(--text-lg);
  line-height: 1.2;
}

.card-identity > p {
  margin-block-start: var(--space-xs);
  color: var(--color-muted);
}

.reward-summary {
  display: grid;
  align-content: start;
  justify-items: start;
  gap: var(--space-2xs);
}

.reward-summary > span {
  color: var(--color-muted);
  font-size: var(--text-sm);
}

.reward-summary strong {
  color: var(--color-accent);
  font-family: var(--font-display);
  font-size: clamp(1.65rem, 4vw, 2.25rem);
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}

.recommendation-card__body {
  display: grid;
  gap: var(--space-md);
  border-top: var(--rule-hairline) solid var(--color-rule);
  padding: var(--space-lg);
  background: var(--color-paper-2);
}

.recommendation-reason {
  color: var(--color-ink-2);
  line-height: 1.65;
}

.reward-meta {
  display: grid;
  gap: var(--space-sm);
  color: var(--color-muted);
  font-size: var(--text-sm);
  line-height: 1.6;
}

.matched-sales {
  border-top: var(--rule-hairline) solid var(--color-rule);
  padding-block-start: var(--space-md);
}

.matched-sales summary {
  width: fit-content;
  border-radius: var(--radius-control);
  outline: var(--rule-focus) solid transparent;
  outline-offset: var(--rule-focus);
  color: var(--color-accent);
  font-weight: 700;
}

.matched-sales__list {
  display: grid;
  gap: var(--space-md);
  margin-block-start: var(--space-md);
}

.sale {
  display: grid;
  gap: var(--space-sm);
  border-left: var(--rule-focus) solid var(--color-accent);
  padding-inline-start: var(--space-md);
}

.sale header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-md);
}

.sale h4 {
  font-size: var(--text-base);
}

.sale header strong {
  flex: 0 0 auto;
  color: var(--color-accent);
}

.sale dl {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: var(--space-xs) var(--space-md);
  color: var(--color-ink-2);
  font-size: var(--text-sm);
}

.sale dt {
  color: var(--color-muted);
  font-weight: 700;
}

.sale dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.sale__links a {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2xs);
  border-radius: var(--radius-control);
  outline: var(--rule-focus) solid transparent;
  outline-offset: var(--rule-focus);
  color: var(--color-accent);
  font-size: var(--text-sm);
  font-weight: 700;
  text-underline-offset: 0.2em;
}

.page-footer {
  border-top: var(--rule-hairline) solid var(--color-rule);
  padding-block: var(--space-md);
  color: var(--color-muted);
  font-size: var(--text-xs);
  line-height: 1.5;
  text-align: center;
}

@media (hover: hover) and (pointer: fine) {
  .query-submit:hover,
  .state-panel button:hover {
    background: var(--color-accent-hover);
  }
}

@media (min-width: 40rem) {
  .recommendations-workspace,
  .page-footer {
    width: min(100% - (var(--space-xl) * 2), var(--layout-max));
  }

  .recommendation-card__header {
    grid-template-columns: minmax(10rem, 15rem) minmax(0, 1fr) max-content;
    align-items: center;
  }

  .reward-summary {
    justify-items: end;
    text-align: right;
  }
}

@media (min-width: 60rem) {
  .query-form {
    grid-template-columns: minmax(0, 1fr) minmax(9rem, 0.7fr) minmax(0, 1fr) auto;
    align-items: start;
  }

  .query-submit {
    margin-block-start: calc(1.5em + var(--space-xs));
  }
}

@keyframes recommendation-spin {
  to {
    transform: rotate(1turn);
  }
}

@media (prefers-reduced-motion: reduce) {
  .query-field input,
  .query-submit,
  .state-panel button {
    transition-duration: var(--dur-reduced);
  }

  .spinner,
  .state-panel__spinner {
    animation-duration: 1.8s;
  }
}
</style>
