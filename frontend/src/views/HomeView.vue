<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import {
  Banknote,
  CircleUserRound,
  CircleX,
  CreditCard,
  LogOut,
  Search,
  Store,
  Tag,
} from '@lucide/vue'
import { onAuthStateChanged, signInWithPopup, signOut, type User } from 'firebase/auth'

import { auth, googleProvider } from '@/firebase'

const location = ref('')
const amount = ref('')
const category = ref('')
const locationError = ref('')
const amountError = ref('')
const categoryError = ref('')
const authUser = ref<User | null>(null)
const authReady = ref(false)
const authBusy = ref(false)
const authError = ref('')
const avatarFailed = ref(false)

const formattedAmount = computed(() => amount.value.replace(/\B(?=(\d{3})+(?!\d))/g, ','))
const authUserName = computed(
  () => authUser.value?.displayName || authUser.value?.email || 'Google 使用者',
)
const showAvatar = computed(() => Boolean(authUser.value?.photoURL) && !avatarFailed.value)

const unsubscribeFromAuth = onAuthStateChanged(
  auth,
  (user) => {
    authUser.value = user
    authReady.value = true
    avatarFailed.value = false
  },
  () => {
    authReady.value = true
    authError.value = '無法確認登入狀態，請重新整理頁面。'
  },
)

onUnmounted(unsubscribeFromAuth)

function authErrorCode(error: unknown) {
  return typeof error === 'object' && error !== null && 'code' in error ? String(error.code) : ''
}

async function handleLogin() {
  if (!authReady.value || authBusy.value) return

  authBusy.value = true
  authError.value = ''

  try {
    await signInWithPopup(auth, googleProvider)
  } catch (error) {
    const code = authErrorCode(error)

    if (code === 'auth/popup-closed-by-user' || code === 'auth/cancelled-popup-request') return

    authError.value =
      code === 'auth/popup-blocked'
        ? '瀏覽器阻擋登入視窗，請允許彈出式視窗後再試。'
        : '登入失敗，請稍後再試。'
  } finally {
    authBusy.value = false
  }
}

async function handleLogout() {
  if (authBusy.value) return

  authBusy.value = true
  authError.value = ''

  try {
    await signOut(auth)
  } catch {
    authError.value = '登出失敗，請稍後再試。'
  } finally {
    authBusy.value = false
  }
}

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

      <div class="topbar__auth">
        <div class="topbar__auth-actions">
          <div v-if="authUser" class="topbar__identity">
            <img
              v-if="showAvatar"
              class="topbar__avatar"
              :src="authUser.photoURL || ''"
              alt=""
              referrerpolicy="no-referrer"
              @error="avatarFailed = true"
            />
            <span v-else class="topbar__avatar topbar__avatar--fallback" aria-hidden="true">
              <CircleUserRound :size="24" />
            </span>
            <span class="topbar__identity-name">{{ authUserName }}</span>
          </div>

          <button
            v-if="authUser"
            class="topbar__login"
            type="button"
            :disabled="authBusy"
            :aria-busy="authBusy"
            @click="handleLogout"
          >
            <LogOut :size="18" aria-hidden="true" />
            {{ authBusy ? '登出中…' : '登出' }}
          </button>
          <button
            v-else
            class="topbar__login topbar__login--google"
            type="button"
            aria-label="使用 Google 帳號登入"
            :disabled="!authReady || authBusy"
            :aria-busy="authBusy"
            @click="handleLogin"
          >
            <svg
              class="topbar__google-icon"
              viewBox="0 0 18 18"
              aria-hidden="true"
              focusable="false"
            >
              <path
                fill="#4285f4"
                d="M17.64 9.205c0-.638-.057-1.252-.164-1.841H9v3.482h4.844a4.14 4.14 0 0 1-1.796 2.716v2.258h2.909c1.702-1.567 2.683-3.874 2.683-6.615Z"
              />
              <path
                fill="#34a853"
                d="M9 18c2.43 0 4.468-.806 5.957-2.18l-2.909-2.258c-.806.54-1.835.859-3.048.859-2.344 0-4.328-1.585-5.037-3.714H.956v2.332A9 9 0 0 0 9 18Z"
              />
              <path
                fill="#fbbc05"
                d="M3.963 10.707A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.281-1.707V4.961H.956A9 9 0 0 0 0 9c0 1.452.347 2.827.956 4.039l3.007-2.332Z"
              />
              <path
                fill="#ea4335"
                d="M9 3.579c1.321 0 2.507.454 3.441 1.346l2.581-2.581C13.464.892 11.426 0 9 0A9 9 0 0 0 .956 4.961l3.007 2.332C4.672 5.164 6.656 3.579 9 3.579Z"
              />
            </svg>
            {{ !authReady ? '確認登入狀態…' : authBusy ? '登入中…' : '登入' }}
          </button>
        </div>

        <p v-if="authError" class="topbar__auth-error" role="alert">{{ authError }}</p>
      </div>
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
  flex-wrap: wrap;
  gap: var(--space-sm);
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

.topbar__auth {
  display: grid;
  min-width: 0;
  justify-items: end;
  gap: var(--space-2xs);
  padding-block: var(--space-xs);
}

.topbar__auth-actions,
.topbar__identity {
  display: flex;
  min-width: 0;
  align-items: center;
}

.topbar__auth-actions {
  gap: var(--space-sm);
}

.topbar__identity {
  gap: var(--space-xs);
}

.topbar__avatar {
  display: grid;
  width: 2rem;
  height: 2rem;
  flex: 0 0 auto;
  place-items: center;
  border: var(--rule-hairline) solid var(--color-rule);
  border-radius: 50%;
  object-fit: cover;
}

.topbar__avatar--fallback {
  color: var(--color-muted);
}

.topbar__identity-name {
  max-width: 12ch;
  overflow: hidden;
  color: var(--color-ink-2);
  font-size: var(--text-sm);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar__auth-error {
  max-width: 38ch;
  margin: 0;
  color: var(--color-error);
  font-size: var(--text-xs);
  line-height: 1.4;
  text-align: right;
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

.topbar__login--google {
  border-color: #747775;
  background: #fff;
  color: #1f1f1f;
}

.topbar__google-icon {
  width: 1.125rem;
  height: 1.125rem;
  flex: 0 0 auto;
}

.topbar__login[aria-busy='true'] {
  cursor: wait;
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
