<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CalendarCheck, CalendarPlus, ExternalLink, Link2, Trash2, Unlink } from '@lucide/vue'
import { storeToRefs } from 'pinia'
import { AxiosError } from 'axios'

import SiteHeader from '../components/SiteHeader.vue'
import { api } from '@/services/api'
import { googleClientId, requestCalendarCode } from '@/services/googleCodeClient'
import { useAuthStore } from '@/stores/authStore'

interface CalendarEvent {
  id: string
  sale_id: string | null
  title: string
  starts_at: string
  ends_at: string | null
  all_day: boolean
  notes: string | null
  html_link: string | null
}

const { user: authUser, ready: authReady } = storeToRefs(useAuthStore())

const connected = ref(false)
const events = ref<CalendarEvent[]>([])
const busy = ref('')
const error = ref('')
const notice = ref('')

const signedIn = computed(() => Boolean(authUser.value))
const clientIdMissing = computed(() => !googleClientId())

/** Surface the backend's own detail string -- that is the point of this page. */
function describe(err: unknown): string {
  if (err instanceof AxiosError) {
    const detail = err.response?.data?.detail
    const status = err.response?.status
    if (detail) return `HTTP ${status}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`
    return err.message
  }
  return err instanceof Error ? err.message : String(err)
}

async function run(label: string, action: () => Promise<void>) {
  if (busy.value) return
  busy.value = label
  error.value = ''
  notice.value = ''
  try {
    await action()
  } catch (err) {
    error.value = describe(err)
  } finally {
    busy.value = ''
  }
}

async function refresh() {
  const status = await api.get<{ connected: boolean }>('/me/calendar')
  connected.value = status.data.connected
  if (connected.value) {
    events.value = (await api.get<CalendarEvent[]>('/me/calendar/events')).data
  } else {
    events.value = []
  }
}

function handleRefresh() {
  return run('refresh', refresh)
}

function handleConnect() {
  return run('connect', async () => {
    const code = await requestCalendarCode()
    // No redirect_uri: the popup flow exchanges against "postmessage", which is
    // what the backend already defaults to.
    await api.post('/me/calendar/connect', { code })
    notice.value = '已連結 Google 行事曆。'
    await refresh()
  })
}

function handleDisconnect() {
  return run('disconnect', async () => {
    await api.delete('/me/calendar/connect')
    notice.value = '已解除連結（已建立的事件仍留在 Google 行事曆）。'
    await refresh()
  })
}

function handleCreate() {
  return run('create', async () => {
    const start = new Date()
    start.setDate(start.getDate() + 1)
    start.setHours(15, 0, 0, 0)
    const end = new Date(start.getTime() + 60 * 60 * 1000)

    const { data } = await api.post('/me/calendar/events', {
      title: '測試：購買除濕機',
      starts_at: start.toISOString(),
      ends_at: end.toISOString(),
      notes: '由 /calendar 測試頁建立。',
      reminders_minutes: [1440],
    })
    notice.value = data.already_notified
      ? '已建立事件，且此檔期先前已通知過（already_notified: true）。'
      : '已建立事件，請到 Google 行事曆確認。'
    await refresh()
  })
}

function handleDelete(id: string) {
  return run(`delete:${id}`, async () => {
    await api.delete(`/me/calendar/events/${id}`)
    notice.value = '已刪除事件。'
    await refresh()
  })
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString('zh-TW', { dateStyle: 'medium', timeStyle: 'short' })
}

// Firebase resolves the session after mount, so wait for it rather than
// firing a request that would just 401.
watch(signedIn, (isIn) => {
  if (isIn) handleRefresh()
  else {
    connected.value = false
    events.value = []
  }
}, { immediate: true })
</script>

<template>
  <SiteHeader current="calendar" />

  <main class="page">
    <header class="page__head">
      <h1>行事曆連結測試</h1>
      <p class="page__lede">
        測試 <code>/me/calendar</code> 系列端點：授權、建立提醒、刪除。
      </p>
    </header>

    <p v-if="clientIdMissing" class="banner banner--warn">
      尚未設定 <code>VITE_GOOGLE_CLIENT_ID</code>，無法開啟授權視窗。請見
      <code>frontend/.env.example</code>。
    </p>

    <p v-if="authReady && !signedIn" class="banner banner--warn">
      請先用右上角的「登入」以 Google 帳號登入，所有端點都需要 Firebase ID token。
    </p>

    <template v-else-if="signedIn">
      <section class="panel">
        <div class="panel__status">
          <CalendarCheck v-if="connected" :size="20" aria-hidden="true" />
          <Link2 v-else :size="20" aria-hidden="true" />
          <span :class="['badge', connected ? 'badge--on' : 'badge--off']">
            {{ connected ? '已連結' : '未連結' }}
          </span>
          <span class="panel__hint">GET /me/calendar</span>
        </div>

        <div class="panel__actions">
          <button
            v-if="!connected"
            class="btn btn--primary"
            type="button"
            :disabled="Boolean(busy) || clientIdMissing"
            @click="handleConnect"
          >
            <Link2 :size="18" aria-hidden="true" />
            {{ busy === 'connect' ? '授權中…' : '連結 Google 行事曆' }}
          </button>

          <template v-else>
            <button
              class="btn btn--primary"
              type="button"
              :disabled="Boolean(busy)"
              @click="handleCreate"
            >
              <CalendarPlus :size="18" aria-hidden="true" />
              {{ busy === 'create' ? '建立中…' : '建立測試事件（明天 15:00）' }}
            </button>
            <button class="btn" type="button" :disabled="Boolean(busy)" @click="handleDisconnect">
              <Unlink :size="18" aria-hidden="true" />
              解除連結
            </button>
          </template>

          <button class="btn" type="button" :disabled="Boolean(busy)" @click="handleRefresh">
            重新整理
          </button>
        </div>
      </section>

      <p v-if="notice" class="banner banner--ok" role="status">{{ notice }}</p>
      <p v-if="error" class="banner banner--error" role="alert">{{ error }}</p>

      <section v-if="connected" class="events">
        <h2>已建立的事件 <span class="events__count">{{ events.length }}</span></h2>
        <p v-if="!events.length" class="events__empty">還沒有事件。</p>
        <ul v-else class="events__list">
          <li v-for="event in events" :key="event.id" class="event">
            <div class="event__main">
              <p class="event__title">{{ event.title }}</p>
              <p class="event__time">{{ formatDateTime(event.starts_at) }}</p>
              <p v-if="event.sale_id" class="event__sale">檔期：{{ event.sale_id }}</p>
            </div>
            <div class="event__actions">
              <a
                v-if="event.html_link"
                class="btn btn--sm"
                :href="event.html_link"
                target="_blank"
                rel="noopener"
              >
                <ExternalLink :size="16" aria-hidden="true" />
                在 Google 開啟
              </a>
              <button
                class="btn btn--sm"
                type="button"
                :disabled="Boolean(busy)"
                @click="handleDelete(event.id)"
              >
                <Trash2 :size="16" aria-hidden="true" />
                刪除
              </button>
            </div>
          </li>
        </ul>
      </section>
    </template>
  </main>
</template>

<style scoped>
.page {
  max-width: 52rem;
  margin: 0 auto;
  padding: var(--space-xl) var(--space-md) var(--space-2xl);
}

.page__head h1 {
  margin: 0;
  font-family: var(--font-display);
  font-size: var(--text-lg);
}

.page__lede {
  margin: var(--space-2xs) 0 var(--space-lg);
  color: var(--color-muted);
  font-size: var(--text-sm);
}

code {
  font-family: var(--font-mono);
  font-size: 0.9em;
}

.banner {
  margin: var(--space-md) 0;
  padding: var(--space-sm) var(--space-md);
  border-radius: 0.5rem;
  border: 1px solid var(--color-rule);
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}

.banner--warn {
  border-color: var(--color-accent-light);
  background: var(--color-paper-2);
}

.banner--ok {
  border-color: var(--color-success);
  color: var(--color-success);
}

.banner--error {
  border-color: var(--color-error);
  color: var(--color-error);
  font-family: var(--font-mono);
}

.panel {
  padding: var(--space-md);
  border: 1px solid var(--color-rule);
  border-radius: 0.75rem;
  background: var(--color-paper-2);
}

.panel__status {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
}

.panel__hint {
  margin-left: auto;
  color: var(--color-muted);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.badge {
  padding: var(--space-3xs) var(--space-xs);
  border-radius: 999px;
  font-size: var(--text-xs);
  font-weight: 600;
}

.badge--on {
  background: var(--color-success);
  color: var(--color-accent-ink);
}

.badge--off {
  background: var(--color-paper-3);
  color: var(--color-muted);
}

.panel__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-xs);
  margin-top: var(--space-md);
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2xs);
  padding: var(--space-xs) var(--space-sm);
  border: 1px solid var(--color-rule-strong);
  border-radius: 0.5rem;
  background: var(--color-paper);
  color: var(--color-ink);
  font-size: var(--text-sm);
  text-decoration: none;
  cursor: pointer;
}

.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.btn--primary {
  border-color: transparent;
  background: var(--color-accent);
  color: var(--color-accent-ink);
}

.btn--sm {
  padding: var(--space-3xs) var(--space-xs);
  font-size: var(--text-xs);
}

.events {
  margin-top: var(--space-xl);
}

.events h2 {
  font-family: var(--font-display);
  font-size: var(--text-base);
}

.events__count {
  color: var(--color-muted);
  font-family: var(--font-mono);
}

.events__empty {
  color: var(--color-muted);
  font-size: var(--text-sm);
}

.events__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--space-xs);
}

.event {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-xs);
  align-items: center;
  justify-content: space-between;
  padding: var(--space-sm);
  border: 1px solid var(--color-rule);
  border-radius: 0.5rem;
}

.event__main p {
  margin: 0;
}

.event__title {
  font-weight: 600;
}

.event__time,
.event__sale {
  color: var(--color-muted);
  font-size: var(--text-sm);
}

.event__actions {
  display: flex;
  gap: var(--space-2xs);
}
</style>
