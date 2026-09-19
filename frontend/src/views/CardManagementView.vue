<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  CircleCheck,
  CreditCard,
  LoaderCircle,
  Plus,
  Search,
  Trash2,
  TriangleAlert,
  WalletCards,
} from '@lucide/vue'

import SiteHeader from '../components/SiteHeader.vue'
import { api } from '../services/api'

type CardRequestMethod = 'POST' | 'DELETE'

interface CardAction {
  key: string
  method: CardRequestMethod
}

interface ApiCard {
  id: string
  bank_name: string | null
  name: string
  artwork_id: string | null
  display_name: string | null
  issuer_en: string | null
  variant: string | null
  network: string | null
  tier: string | null
  official_image_url: string | null
  image_is_composite: boolean | null
}

interface UserCard {
  id: string
  card: ApiCard
  created_at: string
}

const resultLimit = 24
const searchQuery = ref('')
const catalogCards = ref<ApiCard[]>([])
const ownedCards = ref<UserCard[]>([])
const isLoading = ref(true)
const loadError = ref('')
const pendingAction = ref<CardAction | null>(null)
const failedAction = ref<CardAction | null>(null)
const actionError = ref('')
const announcement = ref('')
const failedImageIds = ref(new Set<string>())

const ownedCardIds = computed(() => new Set(ownedCards.value.map(({ card }) => card.id)))

const filteredCards = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase('zh-TW')

  if (!query) return catalogCards.value

  return catalogCards.value.filter((card) =>
    [
      card.bank_name,
      card.name,
      card.issuer_en,
      card.variant,
      card.network,
      card.tier,
    ]
      .filter(Boolean)
      .join(' ')
      .toLocaleLowerCase('zh-TW')
      .includes(query),
  )
})

const displayedCards = computed(() => filteredCards.value.slice(0, resultLimit))

function isOwned(card: ApiCard) {
  return ownedCardIds.value.has(card.id)
}

function isPending(key: string, method: CardRequestMethod) {
  return pendingAction.value?.key === key && pendingAction.value.method === method
}

function isFailed(key: string, method: CardRequestMethod) {
  return failedAction.value?.key === key && failedAction.value.method === method
}

function addButtonState(card: ApiCard) {
  if (isPending(card.id, 'POST')) return 'loading'
  if (isOwned(card)) return 'success'
  if (isFailed(card.id, 'POST')) return 'error'
  return 'idle'
}

function removeButtonState(userCard: UserCard) {
  if (isPending(userCard.id, 'DELETE')) return 'loading'
  if (isFailed(userCard.id, 'DELETE')) return 'error'
  return 'idle'
}

function markImageFailed(cardId: string) {
  const nextIds = new Set(failedImageIds.value)
  nextIds.add(cardId)
  failedImageIds.value = nextIds
}

async function addCard(card: ApiCard) {
  if (pendingAction.value || isOwned(card)) return

  pendingAction.value = { key: card.id, method: 'POST' }
  failedAction.value = null
  actionError.value = ''
  announcement.value = ''

  try {
    const response = await api.post<UserCard>('/me/cards', { card_id: card.id })
    ownedCards.value = [...ownedCards.value, response.data]
    announcement.value = `已將「${card.name}」加入卡包。`
  } catch {
    failedAction.value = { key: card.id, method: 'POST' }
    actionError.value = `無法加入「${card.name}」，持卡資料未變更。請稍後再試。`
  } finally {
    pendingAction.value = null
  }
}

async function removeCard(userCard: UserCard) {
  if (pendingAction.value) return

  pendingAction.value = { key: userCard.id, method: 'DELETE' }
  failedAction.value = null
  actionError.value = ''
  announcement.value = ''

  try {
    await api.delete(`/me/cards/${userCard.id}`)
    ownedCards.value = ownedCards.value.filter(({ id }) => id !== userCard.id)
    announcement.value = `已將「${userCard.card.name}」移出卡包。`
  } catch {
    failedAction.value = { key: userCard.id, method: 'DELETE' }
    actionError.value = `無法移除「${userCard.card.name}」，持卡資料未變更。請稍後再試。`
  } finally {
    pendingAction.value = null
  }
}

onMounted(async () => {
  try {
    const [catalogResponse, ownedResponse] = await Promise.all([
      api.get<ApiCard[]>('/cards'),
      api.get<UserCard[]>('/me/cards'),
    ])

    catalogCards.value = catalogResponse.data
    ownedCards.value = ownedResponse.data
  } catch {
    loadError.value = '無法讀取卡片資料，請稍後再試。'
  } finally {
    isLoading.value = false
  }
})
</script>

<template>
  <div class="card-page">
    <SiteHeader current="card-management" />

    <main class="card-workspace">
      <header class="page-intro">
        <div>
          <h1>卡片管理</h1>
          <p>加入你持有的信用卡，查詢推薦時會納入這些卡片。</p>
        </div>
      </header>

      <p class="visually-hidden" role="status" aria-live="polite">{{ announcement }}</p>

      <p v-if="loadError || actionError" class="action-error" role="alert">
        <TriangleAlert :size="20" aria-hidden="true" />
        <span>{{ loadError || actionError }}</span>
      </p>

      <section class="wallet-section" aria-labelledby="wallet-title">
        <header class="section-heading">
          <div>
            <h2 id="wallet-title">我的卡包</h2>
            <p>已加入的信用卡會顯示在這裡。</p>
          </div>
          <span class="section-heading__count">{{
            isLoading ? '—' : `${ownedCards.length} 張`
          }}</span>
        </header>

        <div v-if="isLoading" class="wallet-loading" role="status">
          <LoaderCircle class="button-spinner" :size="24" aria-hidden="true" />
          <span>正在讀取持卡資料…</span>
        </div>

        <div v-else-if="!loadError && ownedCards.length === 0" class="wallet-empty">
          <WalletCards :size="32" :stroke-width="1.6" aria-hidden="true" />
          <div>
            <h3>尚未加入信用卡</h3>
            <p>從下方卡片清單選擇你持有的信用卡。</p>
          </div>
        </div>

        <div v-else-if="ownedCards.length > 0" class="wallet-shell">
          <div class="wallet-track">
            <article v-for="userCard in ownedCards" :key="userCard.id" class="wallet-card">
              <div class="wallet-card__media">
                <img
                  v-if="
                    userCard.card.artwork_id && !failedImageIds.has(userCard.card.artwork_id)
                  "
                  :src="`/card-art/${userCard.card.artwork_id}.webp`"
                  :alt="`${userCard.card.bank_name ?? ''}${userCard.card.name}卡面`"
                  width="640"
                  height="400"
                  @error="markImageFailed(userCard.card.artwork_id)"
                />
                <div v-else class="card-art-fallback">
                  <CreditCard :size="36" :stroke-width="1.5" aria-hidden="true" />
                  <span>卡面圖片無法顯示</span>
                </div>
              </div>

              <div class="wallet-card__meta">
                <div>
                  <h3>{{ userCard.card.name }}</h3>
                  <p>{{ userCard.card.bank_name ?? '發卡銀行未提供' }}</p>
                </div>
                <button
                  class="wallet-remove"
                  type="button"
                  :data-state="removeButtonState(userCard)"
                  :disabled="Boolean(pendingAction)"
                  :aria-busy="isPending(userCard.id, 'DELETE')"
                  :aria-label="`移除${userCard.card.bank_name ?? ''}${userCard.card.name}`"
                  @click="removeCard(userCard)"
                >
                  <LoaderCircle
                    v-if="isPending(userCard.id, 'DELETE')"
                    class="button-spinner"
                    :size="17"
                    aria-hidden="true"
                  />
                  <Trash2 v-else :size="17" aria-hidden="true" />
                  {{ isPending(userCard.id, 'DELETE') ? '移除中…' : '移除' }}
                </button>
              </div>
            </article>
          </div>
        </div>
      </section>

      <section class="catalogue-section" aria-labelledby="catalogue-title">
        <header class="section-heading section-heading--catalogue">
          <div>
            <h2 id="catalogue-title">信用卡清單</h2>
            <p>同一卡別的不同卡面只列出一次。</p>
          </div>
        </header>

        <div class="search-control">
          <label for="card-search">搜尋信用卡</label>
          <div class="search-control__field">
            <Search :size="19" aria-hidden="true" />
            <input
              id="card-search"
              v-model="searchQuery"
              type="search"
              placeholder="例如：玉山銀行、LINE Pay"
              aria-describedby="card-search-help"
              autocomplete="off"
            />
          </div>
          <p id="card-search-help">可輸入銀行、卡片名稱、卡別或發卡組織。</p>
        </div>

        <div
          v-if="!isLoading && !loadError"
          class="catalogue-summary"
          role="status"
          aria-live="polite"
        >
          <span>找到 {{ filteredCards.length }} 張信用卡</span>
          <span v-if="filteredCards.length > resultLimit">
            顯示前 {{ resultLimit }} 張，輸入關鍵字可縮小範圍。
          </span>
        </div>

        <div v-if="isLoading" class="catalogue-loading" role="status">
          <LoaderCircle class="button-spinner" :size="24" aria-hidden="true" />
          <span>正在讀取信用卡清單…</span>
        </div>

        <div v-else-if="!loadError && displayedCards.length === 0" class="catalogue-empty">
          <Search :size="28" :stroke-width="1.6" aria-hidden="true" />
          <div>
            <h3>找不到符合條件的信用卡</h3>
            <p>請改用銀行名稱或卡片名稱搜尋。</p>
          </div>
        </div>

        <div v-else-if="displayedCards.length > 0" class="catalogue-grid">
          <article v-for="card in displayedCards" :key="card.id" class="catalogue-card">
            <div class="catalogue-card__image">
              <img
                v-if="card.artwork_id && !failedImageIds.has(card.artwork_id)"
                :src="`/card-art/${card.artwork_id}.webp`"
                :alt="`${card.bank_name ?? ''}${card.name}卡面`"
                width="640"
                height="400"
                loading="lazy"
                @error="markImageFailed(card.artwork_id)"
              />
              <div v-else class="card-art-fallback">
                <CreditCard :size="36" :stroke-width="1.5" aria-hidden="true" />
                <span>卡面圖片無法顯示</span>
              </div>
            </div>

            <div class="catalogue-card__meta">
              <div>
                <h3>{{ card.name }}</h3>
                <p>{{ card.bank_name ?? '發卡銀行未提供' }}</p>
              </div>
            </div>

            <button
              class="catalogue-card__action"
              type="button"
              :data-state="addButtonState(card)"
              :disabled="Boolean(pendingAction) || isOwned(card)"
              :aria-busy="isPending(card.id, 'POST')"
              :aria-label="
                isOwned(card)
                  ? `已加入${card.bank_name ?? ''}${card.name}`
                  : `加入${card.bank_name ?? ''}${card.name}`
              "
              @click="addCard(card)"
            >
              <LoaderCircle
                v-if="isPending(card.id, 'POST')"
                class="button-spinner"
                :size="18"
                aria-hidden="true"
              />
              <CircleCheck v-else-if="isOwned(card)" :size="18" aria-hidden="true" />
              <Plus v-else :size="18" aria-hidden="true" />
              {{ isPending(card.id, 'POST') ? '加入中…' : isOwned(card) ? '已加入' : '加入卡片' }}
            </button>
          </article>
        </div>
      </section>
    </main>

    <footer class="card-footer">
      <div class="card-footer__meta">
        <span>信用卡推薦</span>
        <span>© 2026 Meichu Hackathon @ Google</span>
      </div>
    </footer>
  </div>
</template>

<style scoped>
/* Hallmark · pre-emit critique: P5 H5 E4 S5 R5 V5
 * Hallmark · macrostructure: Catalogue · genre: modern-minimal · theme: Cobalt
 * tone: 中性、清楚 · anchor hue: cobalt 256 · enrichment: none
 * nav: N1b · footer: Ft5 · F6: ratio=4/3 landscape, density=3-up, action=Add
 * C1: shape=pill, density=compact, adornment=plus
 * contrast: pass (40–41) · slop: pass (42–45) · honest: pass (46)
 * chrome: pass (47) · tokens: pass (48) · responsive: pass (49)
 * icons: pass (30) · mobile: pass (34, 49, 50–57)
 */
.card-page {
  min-height: 100dvh;
  background: var(--color-paper);
  color: var(--color-ink);
}

.card-workspace,
.card-footer {
  width: min(100% - (var(--space-lg) * 2), var(--layout-max));
  margin-inline: auto;
}

.card-workspace {
  display: grid;
  gap: var(--space-2xl);
  padding-block: var(--space-xl) var(--space-3xl);
}

.page-intro {
  display: grid;
  gap: var(--space-lg);
  border-bottom: var(--rule-hairline) solid var(--color-rule);
  padding-block-end: var(--space-xl);
}

.page-intro h1,
.page-intro p,
.section-heading h2,
.section-heading p,
.wallet-empty h3,
.wallet-empty p,
.catalogue-empty h3,
.catalogue-empty p,
.wallet-card h3,
.wallet-card p,
.catalogue-card h3,
.catalogue-card p,
.catalogue-summary,
.search-control p,
.action-error {
  margin: 0;
}

.page-intro h1 {
  min-width: 0;
  overflow-wrap: anywhere;
  font-family: var(--font-display);
  font-size: clamp(2rem, 4vw, 3.25rem);
  font-style: normal;
  font-weight: 700;
  letter-spacing: -0.035em;
  line-height: 1.05;
}

.page-intro > div > p {
  max-width: 54ch;
  margin-block-start: var(--space-sm);
  color: var(--color-ink-2);
  line-height: 1.6;
}

.action-error {
  display: flex;
  align-items: flex-start;
  gap: var(--space-sm);
  border: var(--rule-hairline) solid var(--color-error);
  border-radius: var(--radius-control);
  padding: var(--space-md);
  background: var(--color-paper-2);
  color: var(--color-error);
  line-height: 1.5;
}

.action-error > svg {
  flex: 0 0 auto;
  margin-block-start: var(--space-3xs);
}

.wallet-section,
.catalogue-section {
  display: grid;
  gap: var(--space-lg);
}

.section-heading {
  display: flex;
  min-width: 0;
  align-items: end;
  justify-content: space-between;
  gap: var(--space-md);
}

.section-heading h2 {
  min-width: 0;
  overflow-wrap: anywhere;
  font-family: var(--font-display);
  font-size: var(--text-lg);
  font-style: normal;
  font-weight: 700;
  letter-spacing: -0.025em;
  line-height: 1.2;
}

.section-heading p {
  margin-block-start: var(--space-2xs);
  color: var(--color-muted);
  font-size: var(--text-sm);
  line-height: 1.5;
}

.section-heading__count {
  flex: 0 0 auto;
  color: var(--color-accent);
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}

.wallet-empty,
.catalogue-empty,
.wallet-loading,
.catalogue-loading {
  display: flex;
  min-height: 9rem;
  align-items: center;
  gap: var(--space-lg);
  border: var(--rule-hairline) dashed var(--color-rule-strong);
  border-radius: var(--radius-panel);
  padding: var(--space-lg);
  background: var(--color-paper-2);
  color: var(--color-muted);
}

.wallet-empty h3,
.catalogue-empty h3 {
  color: var(--color-ink);
  font-family: var(--font-display);
  font-size: var(--text-md);
  font-style: normal;
  font-weight: 700;
}

.wallet-empty p,
.catalogue-empty p {
  margin-block-start: var(--space-2xs);
  font-size: var(--text-sm);
  line-height: 1.5;
}

.wallet-shell {
  border: var(--rule-hairline) solid var(--color-graphite-rule);
  border-radius: var(--radius-panel);
  padding: var(--space-lg);
  background: var(--color-graphite);
  color: var(--color-graphite-ink);
}

.wallet-track {
  display: grid;
  min-width: 0;
  grid-auto-columns: minmax(15rem, 19rem);
  grid-auto-flow: column;
  gap: var(--space-lg);
  overflow-x: auto;
  padding-block-end: var(--space-xs);
  scroll-snap-type: inline proximity;
  scrollbar-color: var(--color-graphite-rule) var(--color-graphite);
}

.wallet-card {
  display: grid;
  min-width: 0;
  align-content: start;
  gap: var(--space-sm);
  scroll-snap-align: start;
}

.wallet-card__media,
.catalogue-card__image {
  display: grid;
  min-width: 0;
  place-items: center;
  overflow: clip;
  border-radius: var(--radius-panel);
}

.wallet-card__media {
  background: transparent;
}

.wallet-card__media img,
.catalogue-card__image img {
  display: block;
  width: auto;
  height: auto;
  max-width: 100%;
  max-height: 16rem;
  object-fit: contain;
}

.wallet-card__meta {
  display: grid;
  min-width: 0;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: var(--space-sm);
}

.wallet-card h3,
.catalogue-card h3 {
  min-width: 0;
  overflow-wrap: anywhere;
  font-family: var(--font-display);
  font-size: var(--text-base);
  font-style: normal;
  font-weight: 700;
  line-height: 1.35;
}

.wallet-card h3 {
  color: var(--color-graphite-ink);
}

.wallet-card p {
  margin-block-start: var(--space-2xs);
  color: var(--color-graphite-muted);
  font-size: var(--text-sm);
  line-height: 1.4;
}

.wallet-remove,
.catalogue-card__action {
  display: inline-flex;
  min-height: var(--control-height);
  align-items: center;
  justify-content: center;
  gap: var(--space-xs);
  border-radius: var(--radius-pill);
  outline: var(--rule-focus) solid transparent;
  outline-offset: var(--rule-hairline);
  padding-inline: var(--space-md);
  font-size: var(--text-sm);
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  transition:
    background-color var(--dur-short) var(--ease-out),
    color var(--dur-short) var(--ease-out),
    transform var(--dur-micro) var(--ease-out);
}

.wallet-remove {
  border: var(--rule-hairline) solid var(--color-graphite-rule);
  background: var(--color-graphite-raised);
  color: var(--color-graphite-ink);
}

.catalogue-card__action:focus-visible,
.search-control input:focus-visible {
  outline-color: var(--color-focus);
}

.wallet-remove:focus-visible {
  outline-color: var(--color-focus-dark);
}

.wallet-remove:active,
.catalogue-card__action:active {
  transform: translateY(var(--rule-hairline));
}

.wallet-remove:disabled,
.catalogue-card__action:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.wallet-remove[data-state='error'] {
  border-color: var(--color-error-focus);
}

.catalogue-section {
  padding-block-start: var(--space-xl);
}

.search-control {
  display: grid;
  max-width: 42rem;
  gap: var(--space-xs);
}

.search-control > label {
  color: var(--color-ink-2);
  font-size: var(--text-sm);
  font-weight: 600;
}

.search-control__field {
  position: relative;
}

.search-control__field > svg {
  position: absolute;
  z-index: var(--z-base, 1);
  inset-block-start: 50%;
  inset-inline-start: var(--space-md);
  color: var(--color-muted);
  pointer-events: none;
  transform: translateY(-50%);
}

.search-control input {
  width: 100%;
  height: var(--input-height);
  border: var(--rule-hairline) solid var(--color-rule-strong);
  border-radius: var(--radius-control);
  outline: var(--rule-focus) solid transparent;
  outline-offset: var(--rule-hairline);
  padding-inline: calc(var(--space-md) + 1.75rem) var(--space-md);
  background: var(--color-paper);
  color: var(--color-ink);
  transition: background-color var(--dur-short) var(--ease-out);
}

.search-control input::placeholder {
  color: var(--color-muted);
}

.search-control p {
  min-height: 1lh;
  color: var(--color-muted);
  font-size: var(--text-sm);
  line-height: 1.5;
}

.catalogue-summary {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: var(--space-xs) var(--space-lg);
  border-block: var(--rule-hairline) solid var(--color-rule);
  padding-block: var(--space-sm);
  color: var(--color-muted);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}

.catalogue-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-xl) var(--space-lg);
}

.catalogue-card {
  display: grid;
  min-width: 0;
  align-content: start;
  gap: var(--space-sm);
}

.catalogue-card__image {
  height: 16rem;
  background: transparent;
}

.card-art-fallback {
  display: grid;
  min-width: 0;
  place-items: center;
  gap: var(--space-xs);
  color: var(--color-muted);
  font-size: var(--text-sm);
  text-align: center;
}

.wallet-card__media .card-art-fallback {
  color: var(--color-ink-2);
}

.catalogue-card__meta {
  display: grid;
  min-width: 0;
  gap: var(--space-xs);
}

.catalogue-card p {
  margin-block-start: var(--space-2xs);
  color: var(--color-muted);
  font-size: var(--text-sm);
  line-height: 1.4;
}

.catalogue-card__action {
  width: 100%;
  border: var(--rule-hairline) solid var(--color-ink);
  background: transparent;
  color: var(--color-ink);
}

.catalogue-card__action[data-state='success'] {
  border-color: var(--color-rule-strong);
  background: var(--color-paper-2);
  color: var(--color-muted);
}

.catalogue-card__action[data-state='error'] {
  border-color: var(--color-error);
  color: var(--color-error);
}

.button-spinner {
  opacity: 0;
  animation:
    spinner-reveal 0s 150ms forwards,
    cards-spin 1s 150ms linear infinite;
}

.card-footer {
  display: grid;
  gap: var(--space-xl);
  padding-block: var(--space-2xl) var(--space-lg);
}

.card-footer__meta {
  display: flex;
  align-items: baseline;
  flex-direction: column;
  gap: var(--space-xs);
  border-top: var(--rule-hairline) solid var(--color-rule);
  padding-block-start: var(--space-sm);
  color: var(--color-muted);
  font-size: var(--text-xs);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
}

@media (hover: hover) and (pointer: fine) {
  .wallet-remove:hover:not(:disabled) {
    background: var(--color-graphite-rule);
    color: var(--color-graphite-ink);
  }

  .catalogue-card__action:hover:not(:disabled) {
    background: var(--color-ink);
    color: var(--color-paper);
  }

  .search-control input:hover:not(:focus-visible) {
    background: var(--color-paper-2);
  }
}

@media (min-width: 40rem) {
  .card-workspace,
  .card-footer {
    width: min(100% - (var(--space-xl) * 2), var(--layout-max));
  }

  .catalogue-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .card-footer__meta {
    flex-direction: row;
    justify-content: space-between;
  }
}

@media (min-width: 60rem) {
  .page-intro {
    grid-template-columns: minmax(0, 1.4fr) minmax(0, 0.6fr);
  }

  .catalogue-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@keyframes cards-spin {
  to {
    transform: rotate(1turn);
  }
}

@keyframes spinner-reveal {
  to {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .wallet-remove,
  .catalogue-card__action,
  .search-control input {
    transition-duration: var(--dur-reduced);
  }

  .wallet-remove:active,
  .catalogue-card__action:active {
    transform: none;
  }

  .button-spinner {
    animation-duration: 0s, 1.8s;
  }
}
</style>
