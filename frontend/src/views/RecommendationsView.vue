<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { CreditCard, ExternalLink, LoaderCircle, Search, TriangleAlert } from '@lucide/vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'

import SiteHeader from '../components/SiteHeader.vue'
import { useAuthStore } from '../stores/authStore'

const STREAM_URL = '/api/v1/recommendations/stream'

// Mirrors backend/app/schemas/recommendations.py.
interface RecommendationRequest {
  product_name: string
  store_name: string
  price: number
  currency: 'TWD'
}

interface CardRef {
  id: string
  bank_name: string | null
  name: string
  // Not part of the backend CardRef today; rendered when present.
  artwork_id?: string | null
}

interface OfficialSource {
  title: string
  url: string
}

interface BestNow {
  card: CardRef
  sale_id: string
  campaign_title: string
  estimated_reward_twd: number
  rate_display: string
  cap_description: string | null
  requires_registration: boolean
  registration_url: string | null
  reason: string
  verification_status: 'verified' | 'unverified'
  official_sources: OfficialSource[]
}

interface CalendarDraft {
  title: string
  starts_at: string
  notes: string
}

interface WaitSuggestion {
  recommended: true
  card: CardRef
  sale_id: string
  starts_at: string
  estimated_reward_twd: number
  estimated_extra_reward_twd: number
  reason: string
  official_sources: OfficialSource[]
  calendar_draft: CalendarDraft
}

interface RecommendationResponse {
  mode: 'no_registration' | 'registration'
  best_now: BestNow | null
  wait_suggestion: WaitSuggestion | null
  explanation: string | null
}

interface SseEvent {
  event: string
  data: string
}

type SearchState = 'idle' | 'loading' | 'success' | 'error' | 'unauthenticated'

class StreamFailure extends Error {}

const GENERIC_ERROR = '無法取得信用卡推薦，請稍後再試。'
const LOGIN_REQUIRED = '請先登入 Google 帳號，才能取得信用卡推薦。'
const DEFAULT_PROGRESS = '系統正在比對持卡資料與優惠活動，請稍候。'
const STAGE_PROGRESS: Record<string, string> = {
  preprocessing: '正在整理您的持卡資料與優惠活動…',
  official_verification: '正在核對銀行官方活動…',
}

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
const searchResult = ref<RecommendationResponse | null>(null)
const requestError = ref('')
const progressMessage = ref(DEFAULT_PROGRESS)
const failedImageIds = ref(new Set<string>())
let activeController: AbortController | null = null

const formattedAmount = computed(() => amount.value.replace(/\B(?=(\d{3})+(?!\d))/g, ','))

const modeNotice = computed(() => {
  if (!searchResult.value) return ''

  return searchResult.value.mode === 'registration'
    ? '依您的設定，僅列出需要登錄的優惠。'
    : '依您的設定，僅列出不需登錄的優惠。'
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

function parseSseBlock(block: string): SseEvent | null {
  let event = 'message'
  const data: string[] = []

  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue

    const separator = line.indexOf(':')
    const field = separator === -1 ? line : line.slice(0, separator)
    let value = separator === -1 ? '' : line.slice(separator + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }

  return data.length ? { event, data: data.join('\n') } : null
}

// Events are separated by a blank line and may be split across network chunks,
// so bytes are buffered until a complete event is available.
async function readSseStream(
  body: ReadableStream<Uint8Array>,
  signal: AbortSignal,
  onEvent: (event: SseEvent) => void,
) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  const cancel = () => void reader.cancel().catch(() => undefined)
  signal.addEventListener('abort', cancel, { once: true })
  let buffer = ''

  const drain = (final: boolean) => {
    buffer = buffer.replace(/\r\n?/g, '\n')
    let boundary = buffer.indexOf('\n\n')

    while (boundary !== -1) {
      const parsed = parseSseBlock(buffer.slice(0, boundary))
      buffer = buffer.slice(boundary + 2)
      if (parsed) onEvent(parsed)
      boundary = buffer.indexOf('\n\n')
    }

    if (final && buffer.trim()) {
      const parsed = parseSseBlock(buffer)
      buffer = ''
      if (parsed) onEvent(parsed)
    }
  }

  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read()
      if (signal.aborted) return

      if (value) buffer += decoder.decode(value, { stream: !done })
      if (done) {
        buffer += decoder.decode()
        drain(true)
        return
      }
      drain(false)
    }
  } finally {
    signal.removeEventListener('abort', cancel)
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parsePayload(data: string): Record<string, unknown> {
  try {
    const payload: unknown = JSON.parse(data)
    if (isObject(payload)) return payload
  } catch {
    // fall through to the failure below
  }

  throw new StreamFailure('malformed SSE payload')
}

function parseRecommendation(data: string): RecommendationResponse {
  const payload = parsePayload(data)

  if (
    (payload.mode !== 'no_registration' && payload.mode !== 'registration') ||
    !('best_now' in payload) ||
    !('wait_suggestion' in payload)
  ) {
    throw new StreamFailure('unexpected recommendation payload')
  }

  return payload as unknown as RecommendationResponse
}

async function loadRecommendations() {
  if (!authReady.value || !validateSearch()) return

  activeController?.abort()
  const controller = new AbortController()
  activeController = controller
  const isCurrent = () => activeController === controller && !controller.signal.aborted

  searchResult.value = null
  requestError.value = ''
  progressMessage.value = DEFAULT_PROGRESS

  const user = authUser.value
  if (!user) {
    activeController = null
    searchState.value = 'unauthenticated'
    return
  }

  searchState.value = 'loading'

  const request: RecommendationRequest = {
    product_name: category.value.trim(),
    store_name: platform.value.trim(),
    price: Number(amount.value),
    currency: 'TWD',
  }

  try {
    const token = await user.getIdToken()
    if (!isCurrent()) return

    const response = await fetch(STREAM_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(request),
      signal: controller.signal,
    })
    if (!isCurrent()) return

    if (!response.ok) {
      throw new StreamFailure(response.status === 401 ? LOGIN_REQUIRED : GENERIC_ERROR)
    }
    if (!response.body) throw new StreamFailure(GENERIC_ERROR)

    let received = false

    await readSseStream(response.body, controller.signal, (event) => {
      if (!isCurrent()) return

      if (event.event === 'searching') {
        const stage = parsePayload(event.data).stage
        progressMessage.value =
          (typeof stage === 'string' && STAGE_PROGRESS[stage]) || DEFAULT_PROGRESS
      } else if (event.event === 'recommendation') {
        searchResult.value = parseRecommendation(event.data)
        searchState.value = 'success'
        received = true
      } else if (event.event === 'error') {
        if (!received) throw new StreamFailure(GENERIC_ERROR)
      }
    })

    if (!isCurrent()) return
    if (!received) throw new StreamFailure(GENERIC_ERROR)
  } catch (error) {
    if (!isCurrent()) return

    searchResult.value = null
    searchState.value = 'error'
    requestError.value =
      error instanceof StreamFailure && error.message === LOGIN_REQUIRED
        ? LOGIN_REQUIRED
        : GENERIC_ERROR
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
    progressMessage.value = DEFAULT_PROGRESS
    return
  }

  void loadRecommendations()
}

const twdFormatter = new Intl.NumberFormat('zh-TW', {
  style: 'currency',
  currency: 'TWD',
  maximumFractionDigits: 2,
})

function formatTwd(amountTwd: number) {
  return twdFormatter.format(amountTwd)
}

function formatDate(value: string) {
  return value.replace(/-/g, '/')
}

function formatDateTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value

  return new Intl.DateTimeFormat('zh-TW', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Asia/Taipei',
  }).format(date)
}

function safeExternalUrl(value: string | null | undefined) {
  if (!value) return ''

  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : ''
  } catch {
    return ''
  }
}

function safeSources(sources: OfficialSource[]) {
  return sources
    .map((source) => ({ title: source.title, href: safeExternalUrl(source.url) }))
    .filter((source) => source.href)
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
          <h1>這筆消費該刷哪張卡</h1>
          <p>調整消費條件後，可直接重新取得推薦結果。</p>
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
          <p>{{ progressMessage }}</p>
        </div>
      </section>

      <section v-else-if="searchState === 'unauthenticated'" class="state-panel state-panel--error">
        <TriangleAlert :size="30" aria-hidden="true" />
        <div>
          <h2 role="alert">{{ LOGIN_REQUIRED }}</h2>
          <p>登入後才能依據您持有的信用卡計算推薦，搜尋條件已保留。</p>
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
        v-else-if="searchState === 'success' && searchResult"
        class="ranking-section"
        aria-labelledby="ranking-title"
      >
        <header class="ranking-heading">
          <div>
            <p>推薦結果</p>
            <h2 id="ranking-title">目前最適合的信用卡</h2>
          </div>
          <span>{{ modeNotice }}</span>
        </header>

        <div class="ranking-list">
          <article
            v-if="searchResult.best_now"
            class="recommendation-card recommendation-card--best"
            data-testid="best-now"
          >
            <div class="recommendation-card__header">
              <div class="card-art">
                <img
                  v-if="
                    searchResult.best_now.card.artwork_id &&
                    !failedImageIds.has(searchResult.best_now.card.artwork_id)
                  "
                  :src="`/card-art/${searchResult.best_now.card.artwork_id}.webp`"
                  :alt="`${searchResult.best_now.card.bank_name ?? ''}${searchResult.best_now.card.name}卡面`"
                  width="640"
                  height="400"
                  @error="markImageFailed(searchResult.best_now.card.artwork_id)"
                />
                <div v-else class="card-art__fallback">
                  <CreditCard :size="38" :stroke-width="1.5" aria-hidden="true" />
                  <span>無卡面圖片</span>
                </div>
              </div>

              <div class="card-identity">
                <div class="card-badges">
                  <span class="best-badge">目前最佳</span>
                  <span
                    class="verification-badge"
                    :class="`verification-badge--${searchResult.best_now.verification_status}`"
                  >
                    {{
                      searchResult.best_now.verification_status === 'verified'
                        ? '已查證官方來源'
                        : '尚未查證官方來源'
                    }}
                  </span>
                </div>
                <h3>{{ searchResult.best_now.card.name }}</h3>
                <p>{{ searchResult.best_now.card.bank_name }}</p>
              </div>

              <div class="reward-summary">
                <span>預估回饋</span>
                <strong>{{ formatTwd(searchResult.best_now.estimated_reward_twd) }}</strong>
                <span>回饋率 {{ searchResult.best_now.rate_display }}</span>
              </div>
            </div>

            <div class="recommendation-card__body">
              <p class="recommendation-reason">{{ searchResult.best_now.reason }}</p>

              <div class="reward-meta">
                <p>活動：{{ searchResult.best_now.campaign_title }}</p>
                <div class="reward-flags">
                  <span v-if="searchResult.best_now.cap_description">
                    {{ searchResult.best_now.cap_description }}
                  </span>
                  <span>
                    {{ searchResult.best_now.requires_registration ? '需要登錄' : '不需登錄' }}
                  </span>
                </div>
              </div>

              <div
                v-if="
                  safeExternalUrl(searchResult.best_now.registration_url) ||
                  safeSources(searchResult.best_now.official_sources).length
                "
                class="source-links"
              >
                <a
                  v-if="safeExternalUrl(searchResult.best_now.registration_url)"
                  :href="safeExternalUrl(searchResult.best_now.registration_url)"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  前往登錄
                  <ExternalLink :size="15" aria-hidden="true" />
                </a>
                <a
                  v-for="source in safeSources(searchResult.best_now.official_sources)"
                  :key="source.href"
                  :href="source.href"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ source.title }}
                  <ExternalLink :size="15" aria-hidden="true" />
                </a>
              </div>
            </div>
          </article>

          <section v-else class="state-panel" data-testid="no-best-now">
            <CreditCard :size="30" aria-hidden="true" />
            <div>
              <h2>目前沒有適用的優惠</h2>
              <p>{{ searchResult.explanation ?? '持有的信用卡目前沒有符合此消費條件的優惠。' }}</p>
            </div>
          </section>

          <article
            v-if="searchResult.wait_suggestion"
            class="recommendation-card wait-card"
            data-testid="wait-suggestion"
            aria-labelledby="wait-title"
          >
            <div class="recommendation-card__header">
              <div class="card-art">
                <img
                  v-if="
                    searchResult.wait_suggestion.card.artwork_id &&
                    !failedImageIds.has(searchResult.wait_suggestion.card.artwork_id)
                  "
                  :src="`/card-art/${searchResult.wait_suggestion.card.artwork_id}.webp`"
                  :alt="`${searchResult.wait_suggestion.card.bank_name ?? ''}${searchResult.wait_suggestion.card.name}卡面`"
                  width="640"
                  height="400"
                  @error="markImageFailed(searchResult.wait_suggestion.card.artwork_id)"
                />
                <div v-else class="card-art__fallback">
                  <CreditCard :size="38" :stroke-width="1.5" aria-hidden="true" />
                  <span>無卡面圖片</span>
                </div>
              </div>

              <div class="card-identity">
                <div class="card-badges">
                  <span class="wait-badge">等待活動</span>
                </div>
                <h3 id="wait-title">{{ searchResult.wait_suggestion.card.name }}</h3>
                <p>{{ searchResult.wait_suggestion.card.bank_name }}</p>
              </div>

              <div class="reward-summary">
                <span>預估回饋</span>
                <strong>{{ formatTwd(searchResult.wait_suggestion.estimated_reward_twd) }}</strong>
                <span>
                  比現在多 {{ formatTwd(searchResult.wait_suggestion.estimated_extra_reward_twd) }}
                </span>
              </div>
            </div>

            <div class="recommendation-card__body">
              <dl class="wait-facts">
                <dt>活動開始日</dt>
                <dd>{{ formatDate(searchResult.wait_suggestion.starts_at) }}</dd>
              </dl>
              <p class="recommendation-reason">{{ searchResult.wait_suggestion.reason }}</p>

              <div
                v-if="safeSources(searchResult.wait_suggestion.official_sources).length"
                class="source-links"
              >
                <a
                  v-for="source in safeSources(searchResult.wait_suggestion.official_sources)"
                  :key="source.href"
                  :href="source.href"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ source.title }}
                  <ExternalLink :size="15" aria-hidden="true" />
                </a>
              </div>

              <div class="calendar-draft" data-testid="calendar-draft">
                <h4>行事曆草稿</h4>
                <p class="calendar-draft__title">
                  {{ searchResult.wait_suggestion.calendar_draft.title }}
                </p>
                <p class="calendar-draft__time">
                  {{ formatDateTime(searchResult.wait_suggestion.calendar_draft.starts_at) }}
                </p>
                <p class="calendar-draft__notes">
                  {{ searchResult.wait_suggestion.calendar_draft.notes }}
                </p>
                <p class="calendar-draft__hint">這只是草稿，尚未建立任何行事曆事件。</p>
              </div>
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
.source-links a:focus-visible {
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
.reward-flags {
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

.card-badges .best-badge,
.card-badges .verification-badge--verified {
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

.source-links {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-xs) var(--space-md);
}

.source-links a {
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

.verification-badge--unverified {
  color: var(--color-muted);
}

.wait-card {
  border-style: dashed;
}

.wait-card .card-badges .wait-badge {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.wait-facts {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: var(--space-xs) var(--space-md);
  margin: 0;
  color: var(--color-ink-2);
  font-size: var(--text-sm);
}

.wait-facts dt {
  color: var(--color-muted);
  font-weight: 700;
}

.wait-facts dd {
  margin: 0;
}

.calendar-draft {
  display: grid;
  gap: var(--space-xs);
  border-left: var(--rule-focus) solid var(--color-accent);
  padding-inline-start: var(--space-md);
  color: var(--color-ink-2);
  font-size: var(--text-sm);
}

.calendar-draft h4,
.calendar-draft p {
  margin: 0;
}

.calendar-draft__title {
  font-weight: 700;
}

.calendar-draft__notes {
  white-space: pre-line;
  overflow-wrap: anywhere;
}

.calendar-draft__hint {
  color: var(--color-muted);
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
