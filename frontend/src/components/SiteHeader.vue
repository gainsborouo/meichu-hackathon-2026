<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  ChevronDown,
  CircleUserRound,
  Languages,
  CalendarCheck,
  FileUp,
  LogOut,
  WalletCards,
} from '@lucide/vue'
import { signInWithPopup, signOut } from 'firebase/auth'
import { storeToRefs } from 'pinia'
import { useI18n } from 'vue-i18n'

import { auth, googleProvider } from '@/firebase'
import { isLocale, setLocale } from '@/i18n'
import { connectCalendarIfNeeded } from '@/services/calendarConnect'
import { preloadGis } from '@/services/googleCodeClient'
import { useAuthStore } from '@/stores/authStore'

defineProps<{
  current: 'home' | 'upload-statement' | 'card-management' | 'calendar'
}>()

const authStore = useAuthStore()
const { user: authUser, ready: authReady } = storeToRefs(authStore)
const { locale, t } = useI18n()
const authBusy = ref(false)
const authErrorKey = ref('')
const avatarFailed = ref(false)
const calendarNotice = ref('')
const calendarError = ref('')
/** Shown when the popup never opened: a click gives consent a fresh gesture. */
const calendarRetry = ref(false)
const calendarBusy = ref(false)

// The consent popup opens two awaits after the login click, by which point the
// transient user activation may be gone. Having the script already cached
// shortens that gap.
onMounted(preloadGis)

const authUserName = computed(
  () => authUser.value?.displayName || authUser.value?.email || t('auth.googleUser'),
)
const authError = computed(() => (authErrorKey.value ? t(authErrorKey.value) : ''))
const showAvatar = computed(() => Boolean(authUser.value?.photoURL) && !avatarFailed.value)

watch(authUser, (current) => {
  avatarFailed.value = false
  if (!current) clearCalendarState()
})

function clearCalendarState() {
  calendarNotice.value = ''
  calendarError.value = ''
  calendarRetry.value = false
}

/** Run calendar consent and translate the outcome into what the header shows.
 *
 * Deliberately never rethrows: this runs inside the login handler, and a
 * calendar problem must not read as a failed sign-in.
 */
async function runCalendarConnect() {
  if (calendarBusy.value) return

  calendarBusy.value = true
  clearCalendarState()

  try {
    const outcome = await connectCalendarIfNeeded()

    switch (outcome.status) {
      case 'connected':
        calendarNotice.value = '已連結 Google 行事曆。'
        break
      case 'blocked':
        calendarError.value = '瀏覽器阻擋了行事曆授權視窗。'
        calendarRetry.value = true
        break
      case 'failed':
        calendarError.value = `行事曆連結失敗：${outcome.message}`
        calendarRetry.value = true
        break
      // 'already-connected', 'declined' and 'unavailable' each mean there is
      // nothing for the user to act on, so the header stays quiet.
    }
  } finally {
    calendarBusy.value = false
  }
}

function authErrorCode(error: unknown) {
  return typeof error === 'object' && error !== null && 'code' in error ? String(error.code) : ''
}

async function handleLogin() {
  if (!authReady.value || authBusy.value) return

  authBusy.value = true
  authErrorKey.value = ''

  try {
    await signInWithPopup(auth, googleProvider)
    // Consent runs after sign-in because it needs the session's ID token to
    // check the existing grant and to post the code.
    await runCalendarConnect()
  } catch (error) {
    const code = authErrorCode(error)

    if (code === 'auth/popup-closed-by-user' || code === 'auth/cancelled-popup-request') return

    authErrorKey.value = code === 'auth/popup-blocked' ? 'auth.popupBlocked' : 'auth.loginFailed'
  } finally {
    authBusy.value = false
  }
}

async function handleLogout() {
  if (authBusy.value) return

  authBusy.value = true
  authErrorKey.value = ''

  clearCalendarState()

  try {
    await signOut(auth)
  } catch {
    authErrorKey.value = 'auth.logoutFailed'
  } finally {
    authBusy.value = false
  }
}

function handleLocaleChange(event: Event) {
  const nextLocale = (event.target as HTMLSelectElement).value
  if (isLocale(nextLocale)) setLocale(nextLocale)
}
</script>

<template>
  <header class="topbar">
    <div class="topbar__left">
      <a
        class="brand"
        href="/"
        :aria-label="t('navigation.homeLabel')"
        :aria-current="current === 'home' ? 'page' : undefined"
      >
        <img class="brand__mark" src="/favicon.svg" alt="" aria-hidden="true" />
        <span class="brand__label">{{ t('common.brand') }}</span>
      </a>
    </div>

    <nav class="topbar__nav" :aria-label="t('navigation.mainLabel')">
      <a
        class="topbar__link"
        href="/upload-statement"
        :aria-current="current === 'upload-statement' ? 'page' : undefined"
      >
        <FileUp :size="18" aria-hidden="true" />
        {{ t('navigation.uploadStatement') }}
      </a>
      <a
        class="topbar__link"
        href="/cards"
        :aria-current="current === 'card-management' ? 'page' : undefined"
      >
        <WalletCards :size="18" aria-hidden="true" />
        {{ t('navigation.cardManagement') }}
      </a>
      <a
        class="topbar__link"
        href="/calendar"
        :aria-current="current === 'calendar' ? 'page' : undefined"
      >
        <CalendarCheck :size="18" aria-hidden="true" />
        行事曆
      </a>
    </nav>

    <div class="topbar__right">
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
            {{ authBusy ? t('auth.loggingOut') : t('auth.logout') }}
          </button>
          <button
            v-else
            class="topbar__login topbar__login--google"
            type="button"
            :aria-label="t('auth.googleLoginLabel')"
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
            {{ !authReady ? t('auth.checking') : authBusy ? t('auth.loggingIn') : t('auth.login') }}
          </button>
        </div>

        <p v-if="authError" class="topbar__auth-error" role="alert">{{ authError }}</p>

        <p v-if="calendarNotice" class="topbar__auth-notice" role="status">{{ calendarNotice }}</p>

        <p v-if="calendarError" class="topbar__auth-error" role="alert">
          {{ calendarError }}
          <button
            v-if="calendarRetry"
            class="topbar__auth-retry"
            type="button"
            :disabled="calendarBusy"
            :aria-busy="calendarBusy"
            @click="runCalendarConnect"
          >
            <CalendarCheck :size="14" aria-hidden="true" />
            {{ calendarBusy ? '連結中…' : '重新連結行事曆' }}
          </button>
        </p>
      </div>

      <label class="topbar__language">
        <span class="topbar__language-label">{{ t('language.label') }}</span>
        <Languages class="topbar__language-icon" :size="17" aria-hidden="true" />
        <select
          class="topbar__language-select"
          :value="locale"
          :aria-label="t('language.label')"
          @change="handleLocaleChange"
        >
          <option value="zh-TW">{{ t('language.zhTW') }}</option>
          <option value="en-US">{{ t('language.enUS') }}</option>
        </select>
        <ChevronDown class="topbar__language-chevron" :size="16" aria-hidden="true" />
      </label>
    </div>
  </header>
</template>

<style scoped>
/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V4
 * component: language select · genre: modern-minimal · theme: Cobalt
 * states: default · hover · focus · active · disabled
 */
.topbar {
  display: grid;
  width: min(100% - (var(--space-lg) * 2), var(--layout-max));
  min-height: var(--topbar-height);
  margin-inline: auto;
  align-items: center;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: var(--space-sm);
  border-bottom: var(--rule-hairline) solid var(--color-rule);
}

.brand,
.topbar__link,
.topbar__login {
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

.brand__mark {
  display: block;
  width: var(--control-height);
  height: var(--control-height);
  flex: 0 0 auto;
}

.topbar__left,
.topbar__right,
.topbar__nav,
.topbar__link,
.topbar__language,
.topbar__auth-actions,
.topbar__identity,
.topbar__login {
  display: flex;
  align-items: center;
}

.topbar__right {
  min-width: 0;
  align-items: flex-start;
  gap: var(--space-sm);
  justify-self: end;
}

.topbar__language {
  position: relative;
  padding-block: var(--space-xs);
  color: var(--color-muted);
}

.topbar__language-label {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

.topbar__language-select {
  width: 8.5rem;
  min-height: var(--control-height);
  appearance: none;
  border: var(--rule-hairline) solid var(--color-rule-strong);
  border-radius: var(--radius-control);
  outline: var(--rule-focus) solid transparent;
  outline-offset: var(--rule-hairline);
  padding-inline: calc(var(--space-lg) + var(--space-xs)) calc(var(--space-lg) + var(--space-sm));
  background: var(--color-paper);
  color: var(--color-ink-2);
  cursor: pointer;
  font: inherit;
  font-size: var(--text-sm);
  font-weight: 600;
  line-height: 1;
  box-shadow: var(--shadow-panel);
  transition: background-color var(--dur-micro) var(--ease-out);
}

.topbar__language-icon,
.topbar__language-chevron {
  position: absolute;
  z-index: var(--z-base);
  inset-block-start: 50%;
  pointer-events: none;
  transform: translateY(-50%);
}

.topbar__language-icon {
  inset-inline-start: var(--space-sm);
}

.topbar__language-chevron {
  inset-inline-end: var(--space-sm);
}

.topbar__language-select:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.topbar__left {
  min-width: 0;
  gap: var(--space-sm);
}

.topbar__nav {
  justify-self: start;
  gap: var(--space-2xs);
}

.topbar__link {
  min-height: var(--control-height);
  gap: var(--space-xs);
  border-radius: var(--radius-control);
  padding-inline: var(--space-sm);
  color: var(--color-ink-2);
  font-size: var(--text-sm);
  font-weight: 600;
  line-height: 1;
  text-decoration: none;
}

.topbar__link[aria-current='page'] {
  color: var(--color-accent);
}

.topbar__auth {
  display: grid;
  min-width: 0;
  justify-items: end;
  gap: var(--space-2xs);
  padding-block: var(--space-xs);
}

.topbar__auth-actions {
  min-width: 0;
  gap: var(--space-sm);
}

.topbar__identity {
  min-width: 0;
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

.topbar__auth-error,
.topbar__auth-notice {
  max-width: 38ch;
  margin: 0;
  color: var(--color-error);
  font-size: var(--text-xs);
  line-height: 1.4;
  text-align: right;
}

.topbar__auth-notice {
  color: var(--color-muted);
}

.topbar__auth-retry {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2xs);
  border: var(--rule-hairline) solid var(--color-rule);
  border-radius: var(--radius-control);
  margin-inline-start: var(--space-2xs);
  padding: 2px var(--space-xs);
  background: var(--color-paper-2);
  color: var(--color-ink-2);
  font-size: var(--text-xs);
  font-weight: 600;
  vertical-align: middle;
}

.topbar__auth-retry:focus-visible {
  outline: var(--rule-focus) solid var(--color-focus);
  outline-offset: var(--rule-focus);
}

.topbar__auth-retry[aria-busy='true'] {
  cursor: wait;
}

.topbar__auth-retry:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.topbar__login {
  min-height: var(--control-height);
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

.topbar__login:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.brand:focus-visible,
.topbar__link:focus-visible,
.topbar__language-select:focus-visible,
.topbar__login:focus-visible {
  outline: var(--rule-focus) solid var(--color-focus);
  outline-offset: var(--rule-focus);
}

.brand:active,
.topbar__link:active {
  color: var(--color-accent);
}

@media (hover: hover) and (pointer: fine) {
  .brand:hover,
  .topbar__link:hover {
    color: var(--color-accent);
  }

  .topbar__language-select:hover {
    background: var(--color-paper-2);
  }
}

.topbar__language-select:active {
  background: var(--color-paper-3);
}

@media (min-width: 40rem) {
  .topbar {
    width: min(100% - (var(--space-xl) * 2), var(--layout-max));
  }
}

@media (max-width: 52rem) {
  .topbar {
    grid-template-columns: minmax(0, 1fr) auto;
    padding-block: var(--space-xs);
  }

  .topbar__nav {
    display: flex;
    width: 100%;
    grid-column: 1 / -1;
    grid-row: 2;
    justify-content: flex-start;
    justify-self: start;
    border-top: var(--rule-hairline) solid var(--color-rule);
    padding-block-start: var(--space-xs);
  }

  .topbar__link {
    justify-content: flex-start;
  }
}

@media (max-width: 40rem) {
  .topbar__right {
    gap: var(--space-xs);
    justify-content: flex-end;
  }

  .topbar__identity {
    display: none;
  }

  .brand__label {
    display: none;
  }

  .topbar__language-icon {
    display: none;
  }

  .topbar__language-select {
    width: 6.75rem;
    padding-inline: var(--space-sm) var(--space-lg);
  }
}
</style>
