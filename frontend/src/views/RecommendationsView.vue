<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { CreditCard, ExternalLink, LoaderCircle, Search, TriangleAlert } from '@lucide/vue'
import { storeToRefs } from 'pinia'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter, type LocationQueryValue } from 'vue-router'

import SiteHeader from '../components/SiteHeader.vue'
import type { Locale } from '../i18n'
import { useAuthStore } from '../stores/authStore'

const STREAM_URL = '/api/v1/recommendations/stream'

// Mirrors backend/app/schemas/recommendations.py.
interface RecommendationRequest {
  product_name: string
  store_name: string
  price: number
  currency: 'TWD'
  locale: Locale
}

interface CardRef {
  id: string
  bank_name: string | null
  name: string
  // Optional in the schema; the fallback art is shown when it is missing.
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

const GENERIC_ERROR_KEY = 'recommendations.genericError'
const LOGIN_REQUIRED_KEY = 'recommendations.loginRequired'
const DEFAULT_PROGRESS_KEY = 'recommendations.defaultProgress'
const STAGE_PROGRESS: Record<string, string> = {
  preprocessing: 'recommendations.preprocessingProgress',
  official_verification: 'recommendations.verificationProgress',
}

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const { ready: authReady, user: authUser } = storeToRefs(authStore)
const { locale, t } = useI18n()

const platform = ref('')
const amount = ref('')
const category = ref('')
const platformError = ref(false)
const amountError = ref(false)
const categoryError = ref(false)
const searchState = ref<SearchState>('idle')
const searchResult = ref<RecommendationResponse | null>(null)
const requestErrorKey = ref('')
const progressKey = ref(DEFAULT_PROGRESS_KEY)
const failedImageIds = ref(new Set<string>())
let activeController: AbortController | null = null

const formattedAmount = computed(() => amount.value.replace(/\B(?=(\d{3})+(?!\d))/g, ','))
const requestError = computed(() => (requestErrorKey.value ? t(requestErrorKey.value) : ''))
const progressMessage = computed(() => t(progressKey.value))

const modeNotice = computed(() => {
  if (!searchResult.value) return ''

  return searchResult.value.mode === 'registration'
    ? t('recommendations.registrationMode')
    : t('recommendations.noRegistrationMode')
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
  platformError.value = !platform.value.trim()
  amountError.value = !isValidAmount(amount.value)
  categoryError.value = !category.value.trim()

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
  if (platform.value.trim()) platformError.value = false
}

function handleAmountInput(event: Event) {
  const input = event.target as HTMLInputElement
  amount.value = input.value.replace(/\D/g, '').replace(/^0+(?=\d)/, '')
  input.value = formattedAmount.value

  if (isValidAmount(amount.value)) amountError.value = false
}

function handleCategoryInput() {
  if (category.value.trim()) categoryError.value = false
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
  requestErrorKey.value = ''
  progressKey.value = DEFAULT_PROGRESS_KEY

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
    locale: locale.value as Locale,
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
      throw new StreamFailure(response.status === 401 ? LOGIN_REQUIRED_KEY : GENERIC_ERROR_KEY)
    }
    if (!response.body) throw new StreamFailure(GENERIC_ERROR_KEY)

    let received = false

    await readSseStream(response.body, controller.signal, (event) => {
      if (!isCurrent()) return

      if (event.event === 'searching') {
        const stage = parsePayload(event.data).stage
        progressKey.value =
          (typeof stage === 'string' && STAGE_PROGRESS[stage]) || DEFAULT_PROGRESS_KEY
      } else if (event.event === 'recommendation') {
        searchResult.value = parseRecommendation(event.data)
        searchState.value = 'success'
        received = true
      } else if (event.event === 'error') {
        if (!received) throw new StreamFailure(GENERIC_ERROR_KEY)
      }
    })

    if (!isCurrent()) return
    if (!received) throw new StreamFailure(GENERIC_ERROR_KEY)
  } catch (error) {
    if (!isCurrent()) return

    searchResult.value = null
    searchState.value = 'error'
    requestErrorKey.value =
      error instanceof StreamFailure && error.message === LOGIN_REQUIRED_KEY
        ? LOGIN_REQUIRED_KEY
        : GENERIC_ERROR_KEY
  } finally {
    if (activeController === controller) activeController = null
  }
}

async function submitSearch() {
  if (!validateSearch()) {
    activeController?.abort()
    searchResult.value = null
    searchState.value = 'idle'
    requestErrorKey.value = ''
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
  requestErrorKey.value = ''

  if (!validateSearch()) {
    searchState.value = 'idle'
    return
  }

  if (!authReady.value) {
    searchState.value = 'loading'
    progressKey.value = DEFAULT_PROGRESS_KEY
    return
  }

  void loadRecommendations()
}

function formatTwd(amountTwd: number) {
  return new Intl.NumberFormat(locale.value, {
    style: 'currency',
    currency: 'TWD',
    maximumFractionDigits: 2,
  }).format(amountTwd)
}

function formatDate(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  if (!year || !month || !day) return value

  return new Intl.DateTimeFormat(locale.value, {
    dateStyle: 'medium',
    timeZone: 'Asia/Taipei',
  }).format(new Date(Date.UTC(year, month - 1, day)))
}

function formatDateTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value

  return new Intl.DateTimeFormat(locale.value, {
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

watch([() => route.fullPath, authReady, locale], prepareRouteSearch, { immediate: true })

onBeforeUnmount(() => activeController?.abort())
</script>

<template>
  <div class="recommendations-page">
    <SiteHeader current="home" />

    <main class="recommendations-workspace" :aria-busy="searchState === 'loading'">
      <header class="page-intro">
        <div>
          <p class="page-intro__eyebrow">{{ t('recommendations.eyebrow') }}</p>
          <h1>{{ t('recommendations.title') }}</h1>
          <p>{{ t('recommendations.description') }}</p>
        </div>
      </header>

      <form
        class="query-form"
        novalidate
        :aria-label="t('recommendations.formLabel')"
        @submit.prevent="submitSearch"
      >
        <div class="query-field">
          <label for="recommendation-platform">{{ t('fields.location') }}</label>
          <input
            id="recommendation-platform"
            v-model="platform"
            name="platform"
            type="text"
            autocomplete="off"
            :placeholder="t('fields.locationPlaceholder')"
            :aria-invalid="platformError ? 'true' : 'false'"
            aria-describedby="recommendation-platform-message"
            @input="handlePlatformInput"
          />
          <p
            id="recommendation-platform-message"
            :class="{ 'query-field__error': platformError }"
            :role="platformError ? 'alert' : undefined"
          >
            {{ platformError ? t('validation.locationRequired') : '' }}
          </p>
        </div>

        <div class="query-field">
          <label for="recommendation-price">{{ t('fields.amountShort') }}</label>
          <input
            id="recommendation-price"
            name="price"
            type="text"
            inputmode="numeric"
            pattern="[0-9]*"
            autocomplete="off"
            :placeholder="t('fields.amountPlaceholder')"
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
            {{ amountError ? t('validation.amountValidPositive') : '' }}
          </p>
        </div>

        <div class="query-field">
          <label for="recommendation-category">{{ t('fields.category') }}</label>
          <input
            id="recommendation-category"
            v-model="category"
            name="category"
            type="text"
            autocomplete="off"
            :placeholder="t('fields.categoryPlaceholder')"
            :aria-invalid="categoryError ? 'true' : 'false'"
            aria-describedby="recommendation-category-message"
            @input="handleCategoryInput"
          />
          <p
            id="recommendation-category-message"
            :class="{ 'query-field__error': categoryError }"
            :role="categoryError ? 'alert' : undefined"
          >
            {{ categoryError ? t('validation.categoryRequired') : '' }}
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
          {{
            searchState === 'loading'
              ? t('recommendations.searching')
              : t('recommendations.searchAgain')
          }}
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
          <h2>{{ t('recommendations.loadingTitle') }}</h2>
          <p>{{ progressMessage }}</p>
        </div>
      </section>

      <section v-else-if="searchState === 'unauthenticated'" class="state-panel state-panel--error">
        <TriangleAlert :size="30" aria-hidden="true" />
        <div>
          <h2 role="alert">{{ t('recommendations.loginRequired') }}</h2>
          <p>{{ t('recommendations.unauthenticatedDescription') }}</p>
        </div>
      </section>

      <section v-else-if="searchState === 'error'" class="state-panel state-panel--error">
        <TriangleAlert :size="30" aria-hidden="true" />
        <div>
          <h2 role="alert">{{ requestError }}</h2>
          <p>{{ t('recommendations.retryDescription') }}</p>
          <button type="button" @click="submitSearch">
            {{ t('recommendations.searchAgain') }}
          </button>
        </div>
      </section>

      <section
        v-else-if="searchState === 'success' && searchResult"
        class="ranking-section"
        aria-labelledby="ranking-title"
      >
        <header class="ranking-heading">
          <div>
            <p>{{ t('recommendations.results') }}</p>
            <h2 id="ranking-title">{{ t('recommendations.rankingTitle') }}</h2>
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
                  :alt="
                    t('common.cardArtworkAlt', {
                      bank: searchResult.best_now.card.bank_name ?? '',
                      name: searchResult.best_now.card.name,
                    })
                  "
                  width="640"
                  height="400"
                  @error="markImageFailed(searchResult.best_now.card.artwork_id)"
                />
                <div v-else class="card-art__fallback">
                  <CreditCard :size="38" :stroke-width="1.5" aria-hidden="true" />
                  <span>{{ t('common.noCardArtwork') }}</span>
                </div>
              </div>

              <div class="card-identity">
                <div class="card-badges">
                  <span class="best-badge">{{ t('recommendations.bestNow') }}</span>
                  <span
                    class="verification-badge"
                    :class="`verification-badge--${searchResult.best_now.verification_status}`"
                  >
                    {{
                      searchResult.best_now.verification_status === 'verified'
                        ? t('recommendations.verified')
                        : t('recommendations.unverified')
                    }}
                  </span>
                </div>
                <h3>{{ searchResult.best_now.card.name }}</h3>
                <p>{{ searchResult.best_now.card.bank_name }}</p>
              </div>

              <div class="reward-summary">
                <span>{{ t('recommendations.estimatedReward') }}</span>
                <strong>{{ formatTwd(searchResult.best_now.estimated_reward_twd) }}</strong>
                <span>{{
                  t('recommendations.rewardRate', { rate: searchResult.best_now.rate_display })
                }}</span>
              </div>
            </div>

            <div class="recommendation-card__body">
              <p class="recommendation-reason">{{ searchResult.best_now.reason }}</p>

              <div class="reward-meta">
                <p>
                  {{
                    t('recommendations.campaign', { title: searchResult.best_now.campaign_title })
                  }}
                </p>
                <div class="reward-flags">
                  <span v-if="searchResult.best_now.cap_description">
                    {{ searchResult.best_now.cap_description }}
                  </span>
                  <span>
                    {{
                      searchResult.best_now.requires_registration
                        ? t('recommendations.registrationRequired')
                        : t('recommendations.registrationNotRequired')
                    }}
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
                  {{ t('recommendations.register') }}
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
              <h2>{{ t('recommendations.noOfferTitle') }}</h2>
              <p>{{ searchResult.explanation ?? t('recommendations.noOfferDescription') }}</p>
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
                  :alt="
                    t('common.cardArtworkAlt', {
                      bank: searchResult.wait_suggestion.card.bank_name ?? '',
                      name: searchResult.wait_suggestion.card.name,
                    })
                  "
                  width="640"
                  height="400"
                  @error="markImageFailed(searchResult.wait_suggestion.card.artwork_id)"
                />
                <div v-else class="card-art__fallback">
                  <CreditCard :size="38" :stroke-width="1.5" aria-hidden="true" />
                  <span>{{ t('common.noCardArtwork') }}</span>
                </div>
              </div>

              <div class="card-identity">
                <div class="card-badges">
                  <span class="wait-badge">{{ t('recommendations.waitForCampaign') }}</span>
                </div>
                <h3 id="wait-title">{{ searchResult.wait_suggestion.card.name }}</h3>
                <p>{{ searchResult.wait_suggestion.card.bank_name }}</p>
              </div>

              <div class="reward-summary">
                <span>{{ t('recommendations.estimatedReward') }}</span>
                <strong>{{ formatTwd(searchResult.wait_suggestion.estimated_reward_twd) }}</strong>
                <span>
                  {{
                    t('recommendations.extraReward', {
                      amount: formatTwd(searchResult.wait_suggestion.estimated_extra_reward_twd),
                    })
                  }}
                </span>
              </div>
            </div>

            <div class="recommendation-card__body">
              <dl class="wait-facts">
                <dt>{{ t('recommendations.campaignStarts') }}</dt>
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
                <h4>{{ t('recommendations.calendarDraft') }}</h4>
                <p class="calendar-draft__title">
                  {{ searchResult.wait_suggestion.calendar_draft.title }}
                </p>
                <p class="calendar-draft__time">
                  {{ formatDateTime(searchResult.wait_suggestion.calendar_draft.starts_at) }}
                </p>
                <p class="calendar-draft__notes">
                  {{ searchResult.wait_suggestion.calendar_draft.notes }}
                </p>
                <p class="calendar-draft__hint">{{ t('recommendations.calendarDraftHint') }}</p>
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
