<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  ChevronDown,
  CircleCheck,
  CreditCard,
  LoaderCircle,
  Plus,
  Search,
  Trash2,
  TriangleAlert,
  WalletCards,
} from '@lucide/vue'
import { useI18n } from 'vue-i18n'

import { localizedBankName, localizedCardName } from '../cardNames'
import SiteHeader from '../components/SiteHeader.vue'
import type { Locale } from '../i18n'
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
  name_en: string | null
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

interface CardGroup {
  key: string
  bankName: string
  cards: ApiCard[]
  totalCount: number
}

interface CardMessage {
  key: string
  name: string
}

const { locale, t } = useI18n()
const searchQuery = ref('')
const catalogCards = ref<ApiCard[]>([])
const ownedCards = ref<UserCard[]>([])
const isLoading = ref(true)
const loadError = ref(false)
const pendingAction = ref<CardAction | null>(null)
const failedAction = ref<CardAction | null>(null)
const actionError = ref<CardMessage | null>(null)
const announcement = ref<CardMessage | null>(null)
const failedImageIds = ref(new Set<string>())
const expandedBankKeys = ref(new Set<string>())

const ownedCardIds = computed(() => new Set(ownedCards.value.map(({ card }) => card.id)))
const hasSearchQuery = computed(() => searchQuery.value.trim().length > 0)
const actionErrorMessage = computed(() =>
  actionError.value ? t(actionError.value.key, { name: actionError.value.name }) : '',
)
const announcementMessage = computed(() =>
  announcement.value ? t(announcement.value.key, { name: announcement.value.name }) : '',
)

function bankName(card: ApiCard) {
  return localizedBankName(card, locale.value as Locale) ?? t('common.bankUnknown')
}

function cardName(card: ApiCard) {
  return localizedCardName(card, locale.value as Locale)
}

const filteredCards = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase(locale.value)

  if (!query) return catalogCards.value

  return catalogCards.value.filter((card) =>
    [card.bank_name, card.name, card.issuer_en, card.name_en, card.variant, card.network, card.tier]
      .filter(Boolean)
      .join(' ')
      .toLocaleLowerCase(locale.value)
      .includes(query),
  )
})

const cardTotalsByBank = computed(() => {
  const totals = new Map<string, number>()

  for (const card of catalogCards.value) {
    const key = card.bank_name ?? ''
    totals.set(key, (totals.get(key) ?? 0) + 1)
  }

  return totals
})

const groupedCards = computed<CardGroup[]>(() => {
  const groups = new Map<string, CardGroup>()

  for (const card of filteredCards.value) {
    const key = card.bank_name ?? ''
    const group = groups.get(key)

    if (group) {
      group.cards.push(card)
      continue
    }

    groups.set(key, {
      key,
      bankName: bankName(card),
      cards: [card],
      totalCount: cardTotalsByBank.value.get(key) ?? 0,
    })
  }

  return [...groups.values()]
})

function expandAllVisibleBanks() {
  expandedBankKeys.value = new Set(groupedCards.value.map(({ key }) => key))
}

function handleBankToggle(key: string, event: Event) {
  const details = event.currentTarget
  if (!(details instanceof HTMLDetailsElement)) return

  const nextKeys = new Set(expandedBankKeys.value)
  if (details.open) nextKeys.add(key)
  else nextKeys.delete(key)
  expandedBankKeys.value = nextKeys
}

watch(searchQuery, expandAllVisibleBanks)

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
  actionError.value = null
  announcement.value = null

  try {
    const response = await api.post<UserCard>('/me/cards', { card_id: card.id })
    ownedCards.value = [...ownedCards.value, response.data]
    announcement.value = { key: 'cards.addedAnnouncement', name: cardName(card) }
  } catch {
    failedAction.value = { key: card.id, method: 'POST' }
    actionError.value = { key: 'cards.addFailed', name: cardName(card) }
  } finally {
    pendingAction.value = null
  }
}

async function removeCard(userCard: UserCard) {
  if (pendingAction.value) return

  pendingAction.value = { key: userCard.id, method: 'DELETE' }
  failedAction.value = null
  actionError.value = null
  announcement.value = null

  try {
    await api.delete(`/me/cards/${userCard.id}`)
    ownedCards.value = ownedCards.value.filter(({ id }) => id !== userCard.id)
    announcement.value = { key: 'cards.removedAnnouncement', name: cardName(userCard.card) }
  } catch {
    failedAction.value = { key: userCard.id, method: 'DELETE' }
    actionError.value = { key: 'cards.removeFailed', name: cardName(userCard.card) }
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
    expandAllVisibleBanks()
  } catch {
    loadError.value = true
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
          <h1>{{ t('cards.title') }}</h1>
          <p>{{ t('cards.description') }}</p>
        </div>
      </header>

      <p class="visually-hidden" role="status" aria-live="polite">
        {{ announcementMessage }}
      </p>

      <p v-if="loadError || actionError" class="action-error" role="alert">
        <TriangleAlert :size="20" aria-hidden="true" />
        <span>{{ loadError ? t('cards.loadFailed') : actionErrorMessage }}</span>
      </p>

      <section class="wallet-section" aria-labelledby="wallet-title">
        <header class="section-heading">
          <div>
            <h2 id="wallet-title">{{ t('cards.walletTitle') }}</h2>
            <p>{{ t('cards.walletDescription') }}</p>
          </div>
          <span class="section-heading__count">{{
            isLoading ? '—' : t('cards.count', ownedCards.length)
          }}</span>
        </header>

        <div v-if="isLoading" class="wallet-loading" role="status">
          <LoaderCircle class="button-spinner" :size="24" aria-hidden="true" />
          <span>{{ t('cards.walletLoading') }}</span>
        </div>

        <div v-else-if="!loadError && ownedCards.length === 0" class="wallet-empty">
          <WalletCards :size="32" :stroke-width="1.6" aria-hidden="true" />
          <div>
            <h3>{{ t('cards.walletEmptyTitle') }}</h3>
            <p>{{ t('cards.walletEmptyDescription') }}</p>
          </div>
        </div>

        <div v-else-if="ownedCards.length > 0" class="wallet-shell">
          <div class="wallet-track">
            <article v-for="userCard in ownedCards" :key="userCard.id" class="wallet-card">
              <div class="wallet-card__media">
                <img
                  v-if="userCard.card.artwork_id && !failedImageIds.has(userCard.card.artwork_id)"
                  :src="`/card-art/${userCard.card.artwork_id}.webp`"
                  :alt="
                    t('common.cardArtworkAlt', {
                      bank: bankName(userCard.card),
                      name: cardName(userCard.card),
                    })
                  "
                  width="640"
                  height="400"
                  @error="markImageFailed(userCard.card.artwork_id)"
                />
                <div v-else class="card-art-fallback">
                  <CreditCard :size="36" :stroke-width="1.5" aria-hidden="true" />
                  <span>{{ t('common.cardArtworkUnavailable') }}</span>
                </div>
              </div>

              <div class="wallet-card__meta">
                <div>
                  <h3>{{ cardName(userCard.card) }}</h3>
                  <p>{{ bankName(userCard.card) }}</p>
                </div>
                <button
                  class="wallet-remove"
                  type="button"
                  :data-state="removeButtonState(userCard)"
                  :disabled="Boolean(pendingAction)"
                  :aria-busy="isPending(userCard.id, 'DELETE')"
                  :aria-label="
                    t('cards.removeLabel', {
                      bank: bankName(userCard.card),
                      name: cardName(userCard.card),
                    })
                  "
                  @click="removeCard(userCard)"
                >
                  <LoaderCircle
                    v-if="isPending(userCard.id, 'DELETE')"
                    class="button-spinner"
                    :size="17"
                    aria-hidden="true"
                  />
                  <Trash2 v-else :size="17" aria-hidden="true" />
                  {{ isPending(userCard.id, 'DELETE') ? t('cards.removing') : t('cards.remove') }}
                </button>
              </div>
            </article>
          </div>
        </div>
      </section>

      <section class="catalogue-section" aria-labelledby="catalogue-title">
        <header class="section-heading section-heading--catalogue">
          <div>
            <h2 id="catalogue-title">{{ t('cards.catalogueTitle') }}</h2>
            <p>{{ t('cards.catalogueDescription') }}</p>
          </div>
        </header>

        <div class="search-control">
          <label for="card-search">{{ t('cards.searchLabel') }}</label>
          <div class="search-control__field">
            <Search :size="19" aria-hidden="true" />
            <input
              id="card-search"
              v-model="searchQuery"
              type="search"
              :placeholder="t('cards.searchPlaceholder')"
              aria-describedby="card-search-help"
              autocomplete="off"
            />
          </div>
          <p id="card-search-help">{{ t('cards.searchHelp') }}</p>
        </div>

        <div
          v-if="!isLoading && !loadError"
          class="catalogue-summary"
          role="status"
          aria-live="polite"
        >
          <span>{{ t('cards.found', filteredCards.length) }}</span>
        </div>

        <div v-if="isLoading" class="catalogue-loading" role="status">
          <LoaderCircle class="button-spinner" :size="24" aria-hidden="true" />
          <span>{{ t('cards.catalogueLoading') }}</span>
        </div>

        <div v-else-if="!loadError && groupedCards.length === 0" class="catalogue-empty">
          <Search :size="28" :stroke-width="1.6" aria-hidden="true" />
          <div>
            <h3>{{ t('cards.noResultsTitle') }}</h3>
            <p>{{ t('cards.noResultsDescription') }}</p>
          </div>
        </div>

        <div v-else-if="groupedCards.length > 0" class="catalogue-groups">
          <details
            v-for="group in groupedCards"
            :key="group.key"
            class="catalogue-bank"
            :open="expandedBankKeys.has(group.key)"
            @toggle="handleBankToggle(group.key, $event)"
          >
            <summary>
              <span class="catalogue-bank__name">
                <ChevronDown :size="20" aria-hidden="true" />
                {{ group.bankName }}
              </span>
              <span class="catalogue-bank__count">
                {{
                  hasSearchQuery
                    ? t('cards.groupMatches', {
                        matched: group.cards.length,
                        total: group.totalCount,
                      })
                    : t('cards.groupCount', group.totalCount)
                }}
              </span>
            </summary>

            <div class="catalogue-grid">
              <article v-for="card in group.cards" :key="card.id" class="catalogue-card">
                <div class="catalogue-card__image">
                  <img
                    v-if="card.artwork_id && !failedImageIds.has(card.artwork_id)"
                    :src="`/card-art/${card.artwork_id}.webp`"
                    :alt="
                      t('common.cardArtworkAlt', {
                        bank: bankName(card),
                        name: cardName(card),
                      })
                    "
                    width="640"
                    height="400"
                    loading="lazy"
                    @error="markImageFailed(card.artwork_id)"
                  />
                  <div v-else class="card-art-fallback">
                    <CreditCard :size="36" :stroke-width="1.5" aria-hidden="true" />
                    <span>{{ t('common.cardArtworkUnavailable') }}</span>
                  </div>
                </div>

                <div class="catalogue-card__meta">
                  <div>
                    <h3>{{ cardName(card) }}</h3>
                    <p>{{ bankName(card) }}</p>
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
                      ? t('cards.addedLabel', { bank: bankName(card), name: cardName(card) })
                      : t('cards.addLabel', { bank: bankName(card), name: cardName(card) })
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
                  {{
                    isPending(card.id, 'POST')
                      ? t('cards.adding')
                      : isOwned(card)
                        ? t('cards.added')
                        : t('cards.add')
                  }}
                </button>
              </article>
            </div>
          </details>
        </div>
      </section>
    </main>

    <footer class="card-footer">
      <div class="card-footer__meta">
        <span>{{ t('common.brand') }}</span>
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
.catalogue-bank > summary:focus-visible,
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

.catalogue-groups {
  display: grid;
  gap: var(--space-lg);
}

.catalogue-bank {
  overflow: clip;
  border: var(--rule-hairline) solid var(--color-rule);
  border-radius: var(--radius-panel);
  background: var(--color-paper);
}

.catalogue-bank > summary {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-md);
  border-radius: var(--radius-panel);
  outline: var(--rule-focus) solid transparent;
  outline-offset: calc(-1 * var(--rule-focus));
  padding: var(--space-md) var(--space-lg);
  cursor: pointer;
  list-style: none;
}

.catalogue-bank > summary::-webkit-details-marker {
  display: none;
}

.catalogue-bank__name {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: var(--space-sm);
  overflow-wrap: anywhere;
  font-family: var(--font-display);
  font-size: var(--text-md);
  font-weight: 700;
}

.catalogue-bank__name > svg {
  flex: 0 0 auto;
  color: var(--color-accent);
  transition: transform var(--dur-short) var(--ease-out);
}

.catalogue-bank[open] .catalogue-bank__name > svg {
  transform: rotate(180deg);
}

.catalogue-bank__count {
  flex: 0 0 auto;
  color: var(--color-muted);
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}

.catalogue-bank > .catalogue-grid {
  border-top: var(--rule-hairline) solid var(--color-rule);
  padding: var(--space-lg);
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

  .catalogue-bank > summary:hover {
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
  .catalogue-bank__name > svg,
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
