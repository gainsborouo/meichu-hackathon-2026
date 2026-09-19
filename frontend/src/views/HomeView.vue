<script setup lang="ts">
import { computed, ref } from 'vue'
import { Banknote, CircleX, CreditCard, LogIn, Search, Store, Tag } from '@lucide/vue'

const location = ref('')
const amount = ref('')
const category = ref('')
const locationError = ref('')
const amountError = ref('')
const categoryError = ref('')

const formattedAmount = computed(() => amount.value.replace(/\B(?=(\d{3})+(?!\d))/g, ','))

function handleLocationInput() {
  if (location.value.trim()) locationError.value = ''
}

function handleAmountInput(event: Event) {
  const input = event.target as HTMLInputElement
  amount.value = input.value.replace(/\D/g, '').replace(/^0+(?=\d)/, '')
  input.value = formattedAmount.value

  if (/^[1-9]\d*$/.test(amount.value)) amountError.value = ''
}

function handleCategoryInput() {
  if (category.value.trim()) categoryError.value = ''
}

function clearLocation() {
  location.value = ''
  locationError.value = ''
}

function clearAmount() {
  amount.value = ''
  amountError.value = ''
}

function clearCategory() {
  category.value = ''
  categoryError.value = ''
}

function submitSearch() {
  locationError.value = location.value.trim() ? '' : '請輸入消費地點'
  amountError.value = /^[1-9]\d*$/.test(amount.value) ? '' : '請輸入大於 0 的消費金額'
  categoryError.value = category.value.trim() ? '' : '請輸入品項或類別'
}
</script>

<template>
  <div class="home-page">
    <header class="topbar">
      <a class="brand" href="/" aria-label="信用卡推薦首頁">
        <span class="brand__mark" aria-hidden="true">
          <CreditCard :size="19" :stroke-width="1.8" />
        </span>
        <span>信用卡推薦</span>
      </a>

      <button class="topbar__login" type="button" disabled aria-label="登入功能尚未開放">
        <LogIn :size="18" aria-hidden="true" />
        登入
      </button>
    </header>

    <main class="workspace">
      <section class="workspace__intro" aria-labelledby="page-title">
        <h1 id="page-title">這筆消費，<br />該刷哪張卡？</h1>
        <p class="workspace__lede">輸入消費地點、金額與品項，查詢適合使用的信用卡。</p>
      </section>

      <form class="search-panel" novalidate @submit.prevent="submitSearch">
        <header class="search-panel__header">
          <div>
            <h2>查詢消費資訊</h2>
            <p>填寫這次消費的基本資訊。</p>
          </div>
          <Search :size="22" :stroke-width="1.8" aria-hidden="true" />
        </header>

        <div class="search-panel__body">
          <div class="field">
            <label for="location">
              <span>消費地點</span>
              <span class="field__required">必填</span>
            </label>
            <div class="input-shell" :class="{ 'input-shell--error': locationError }">
              <Store :size="21" :stroke-width="1.8" aria-hidden="true" />
              <input
                id="location"
                v-model="location"
                name="location"
                type="text"
                autocomplete="off"
                placeholder="例如：全聯、蝦皮、東京"
                :aria-invalid="locationError ? 'true' : 'false'"
                aria-describedby="location-message"
                @input="handleLocationInput"
              />
              <span class="input-shell__action">
                <button
                  v-if="location"
                  type="button"
                  aria-label="清除消費地點"
                  @click="clearLocation"
                >
                  <CircleX :size="20" aria-hidden="true" />
                </button>
              </span>
            </div>
            <p
              id="location-message"
              class="field__message"
              :class="{ 'field__message--error': locationError }"
              :role="locationError ? 'alert' : undefined"
            >
              {{ locationError }}
            </p>
          </div>

          <div class="field">
            <label for="amount">
              <span>金額（以新臺幣計算）</span>
              <span class="field__required">必填</span>
            </label>
            <div class="input-shell" :class="{ 'input-shell--error': amountError }">
              <Banknote :size="21" :stroke-width="1.8" aria-hidden="true" />
              <input
                id="amount"
                name="amount"
                type="text"
                inputmode="numeric"
                pattern="[0-9]*"
                autocomplete="off"
                placeholder="例如：2,500"
                :value="formattedAmount"
                :aria-invalid="amountError ? 'true' : 'false'"
                aria-describedby="amount-message"
                @input="handleAmountInput"
              />
              <span class="input-shell__action">
                <button v-if="amount" type="button" aria-label="清除消費金額" @click="clearAmount">
                  <CircleX :size="20" aria-hidden="true" />
                </button>
              </span>
            </div>
            <p
              id="amount-message"
              class="field__message"
              :class="{ 'field__message--error': amountError }"
              :role="amountError ? 'alert' : undefined"
            >
              {{ amountError }}
            </p>
          </div>

          <div class="field">
            <label for="category">
              <span>品項或類別</span>
              <span class="field__required">必填</span>
            </label>
            <div class="input-shell" :class="{ 'input-shell--error': categoryError }">
              <Tag :size="21" :stroke-width="1.8" aria-hidden="true" />
              <input
                id="category"
                v-model="category"
                name="category"
                type="text"
                autocomplete="off"
                placeholder="例如：餐飲、影音、機票"
                :aria-invalid="categoryError ? 'true' : 'false'"
                aria-describedby="category-message"
                @input="handleCategoryInput"
              />
              <span class="input-shell__action">
                <button
                  v-if="category"
                  type="button"
                  aria-label="清除品項或類別"
                  @click="clearCategory"
                >
                  <CircleX :size="20" aria-hidden="true" />
                </button>
              </span>
            </div>
            <p
              id="category-message"
              class="field__message"
              :class="{ 'field__message--error': categoryError }"
              :role="categoryError ? 'alert' : undefined"
            >
              {{ categoryError }}
            </p>
          </div>

          <button class="search-panel__submit" type="submit">
            <Search :size="20" aria-hidden="true" />
            搜尋信用卡推薦
          </button>
        </div>
      </form>
    </main>

    <footer class="page-footer">© 2026 Meichu Hackathon @ Google</footer>
  </div>
</template>

<style scoped>
.home-page {
  display: flex;
  min-height: 100svh;
  flex-direction: column;
  background: var(--color-paper);
  color: var(--color-ink);
}

.topbar {
  display: flex;
  width: min(100% - (var(--space-lg) * 2), var(--layout-max));
  margin-inline: auto;
  align-items: center;
  justify-content: space-between;
}

.topbar {
  min-height: var(--topbar-height);
  border-bottom: var(--rule-hairline) solid var(--color-rule);
}

.brand,
.topbar__login,
.search-panel__submit {
  white-space: nowrap;
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: var(--space-sm);
  color: var(--color-ink);
  font-family: var(--font-display);
  font-size: var(--text-md);
  font-weight: 600;
  line-height: 1;
  text-decoration: none;
}

.brand:active {
  color: var(--color-accent);
}

.brand__mark {
  display: grid;
  width: var(--control-height);
  height: var(--control-height);
  place-items: center;
  border: var(--rule-hairline) solid var(--color-rule-strong);
  border-radius: var(--radius-control);
  color: var(--color-accent);
}

.topbar__login {
  display: inline-flex;
  min-height: var(--control-height);
  align-items: center;
  gap: var(--space-xs);
  border: var(--rule-hairline) solid var(--color-rule);
  border-radius: var(--radius-control);
  padding-inline: var(--space-md);
  background: var(--color-paper-2);
  color: var(--color-muted);
  font-size: var(--text-sm);
  font-weight: 600;
}

.topbar__login:disabled,
.search-panel__submit:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.workspace {
  display: grid;
  width: min(100% - (var(--space-lg) * 2), var(--layout-max));
  flex: 1;
  margin-inline: auto;
  align-items: center;
  gap: var(--space-2xl);
  padding-block: var(--space-xl) var(--space-2xl);
}

.workspace__intro {
  min-width: 0;
  animation: enter-workspace var(--dur-long) var(--ease-out) both;
}

.workspace h1 {
  min-width: 0;
  max-width: 10ch;
  margin: 0;
  overflow-wrap: anywhere;
  font-family: var(--font-display);
  font-size: var(--text-display);
  font-style: normal;
  font-weight: 600;
  letter-spacing: -0.04em;
  line-height: 1.02;
}

.workspace__lede {
  max-width: 54ch;
  margin: var(--space-lg) 0 0;
  color: var(--color-ink-2);
  font-size: var(--text-md);
  line-height: 1.65;
}

.search-panel {
  min-width: 0;
  overflow: clip;
  border: var(--rule-hairline) solid var(--color-graphite-rule);
  border-radius: var(--radius-panel);
  background: var(--color-graphite);
  color: var(--color-graphite-ink);
  box-shadow: var(--shadow-panel);
  animation: enter-workspace var(--dur-long) var(--ease-out) var(--dur-micro) both;
}

.search-panel__header {
  display: flex;
  min-height: calc(var(--control-height) + var(--space-lg));
  align-items: center;
  justify-content: space-between;
  gap: var(--space-md);
  border-bottom: var(--rule-hairline) solid var(--color-graphite-rule);
  padding: var(--space-lg);
}

.search-panel__header h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: var(--text-lg);
  font-style: normal;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.search-panel__header p {
  margin: var(--space-2xs) 0 0;
  color: var(--color-graphite-muted);
  font-size: var(--text-sm);
}

.search-panel__header > svg {
  flex: 0 0 auto;
  color: var(--color-accent-light);
}

.search-panel__body {
  display: grid;
  min-width: 0;
  gap: var(--space-md);
  padding: var(--space-lg);
}

.field {
  display: grid;
  min-width: 0;
  gap: var(--space-xs);
}

.field label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-sm);
  color: var(--color-graphite-ink);
  font-size: var(--text-sm);
  font-weight: 600;
  line-height: 1;
}

.field__required {
  color: var(--color-graphite-muted);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  font-weight: 500;
  letter-spacing: 0.06em;
}

.input-shell {
  display: flex;
  width: 100%;
  min-height: var(--input-height);
  min-width: 0;
  align-items: center;
  gap: var(--space-sm);
  border: var(--rule-hairline) solid var(--color-graphite-rule-strong);
  border-radius: var(--radius-control);
  outline: var(--rule-focus) solid transparent;
  outline-offset: var(--rule-hairline);
  padding-inline: var(--space-md);
  background: var(--color-graphite-raised);
  color: var(--color-graphite-muted);
  transition:
    background-color var(--dur-short) var(--ease-out),
    border-color var(--dur-short) var(--ease-out);
}

.input-shell:focus-within {
  border-color: var(--color-graphite-ink);
  outline-color: var(--color-focus-dark);
  background: var(--color-graphite-raised-2);
}

.input-shell--error {
  border-color: var(--color-error);
}

.input-shell--error:focus-within {
  outline-color: var(--color-error-focus);
}

.input-shell > svg {
  flex: 0 0 auto;
  color: var(--color-accent-light);
}

.input-shell input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--color-graphite-ink);
  font-size: var(--text-base);
  font-variant-numeric: tabular-nums;
  line-height: 1.5;
}

.input-shell input::placeholder {
  color: var(--color-graphite-muted);
  opacity: 1;
}

.input-shell input:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.input-shell:has(input:disabled) {
  cursor: not-allowed;
  opacity: 0.55;
}

.input-shell__action {
  display: grid;
  width: var(--control-height);
  height: var(--control-height);
  flex: 0 0 auto;
  place-items: center;
}

.input-shell__action button {
  display: grid;
  width: var(--control-height);
  height: var(--control-height);
  place-items: center;
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--color-graphite-muted);
  transition: color var(--dur-short) var(--ease-out);
}

.brand:focus-visible,
.topbar__login:focus-visible {
  outline: var(--rule-focus) solid var(--color-focus);
  outline-offset: var(--rule-focus);
}

.input-shell__action button:focus-visible,
.search-panel__submit:focus-visible {
  outline: var(--rule-focus) solid var(--color-focus-dark);
  outline-offset: var(--rule-focus);
}

.input-shell__action button:active,
.search-panel__submit:active {
  transform: translateY(var(--rule-hairline));
}

.input-shell__action button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.field__message {
  min-height: 1lh;
  margin: 0;
  color: var(--color-graphite-muted);
  font-size: var(--text-sm);
  line-height: 1.5;
}

.field__message:not(.field__message--error) {
  display: none;
}

.field__message--error {
  color: var(--color-error-light);
}

.page-footer {
  width: min(100% - (var(--space-lg) * 2), var(--layout-max));
  margin-inline: auto;
  border-top: var(--rule-hairline) solid var(--color-rule);
  padding-block: var(--space-md);
  color: var(--color-muted);
  font-size: var(--text-xs);
  line-height: 1.5;
  text-align: center;
}

.search-panel__submit {
  display: inline-flex;
  min-height: var(--input-height);
  align-items: center;
  justify-content: center;
  gap: var(--space-xs);
  margin-top: var(--space-sm);
  border: var(--rule-hairline) solid var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--color-accent);
  color: var(--color-accent-ink);
  font-size: var(--text-base);
  font-weight: 700;
  transition:
    background-color var(--dur-short) var(--ease-out),
    transform var(--dur-micro) var(--ease-out);
}

.search-panel__submit[aria-busy='true'] {
  cursor: wait;
  opacity: 0.7;
}

.search-panel__submit[data-state='error'] {
  border-color: var(--color-error);
  background: var(--color-error);
}

.search-panel__submit[data-state='success'] {
  border-color: var(--color-success);
  background: var(--color-success);
}

@media (hover: hover) and (pointer: fine) {
  .brand:hover {
    color: var(--color-accent);
  }

  .input-shell:hover {
    background: var(--color-graphite-raised-2);
  }

  .input-shell__action button:hover {
    color: var(--color-graphite-ink);
  }

  .search-panel__submit:hover {
    background: var(--color-accent-hover);
  }
}

@media (min-width: 40rem) {
  .topbar,
  .page-footer,
  .workspace {
    width: min(100% - (var(--space-xl) * 2), var(--layout-max));
  }

  .search-panel__header,
  .search-panel__body {
    padding-inline: var(--space-xl);
  }
}

@media (min-width: 60rem) {
  .workspace {
    grid-template-columns: minmax(0, 0.88fr) minmax(0, 1fr);
    gap: var(--space-3xl);
    padding-block: var(--space-lg) var(--space-xl);
  }
}

@keyframes enter-workspace {
  from {
    opacity: 0;
    transform: translateY(var(--space-sm));
  }

  to {
    opacity: 1;
    transform: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .workspace__intro,
  .search-panel {
    animation: none;
  }

  .input-shell,
  .input-shell__action button,
  .search-panel__submit {
    transition-duration: var(--dur-reduced);
  }

  .input-shell__action button:active,
  .search-panel__submit:active {
    transform: none;
  }
}
</style>
