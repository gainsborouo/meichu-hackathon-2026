import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { User } from 'firebase/auth'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, type LocationQueryRaw } from 'vue-router'

import { i18n, setLocale } from '../../i18n'
import { useAuthStore } from '../../stores/authStore'
import RecommendationsView from '../RecommendationsView.vue'

const apiMocks = vi.hoisted(() => ({
  get: vi.fn<(url: string) => Promise<{ data: { registration_campaigns_enabled: boolean } }>>(),
  patch:
    vi.fn<
      (
        url: string,
        data: { registration_campaigns_enabled: boolean },
      ) => Promise<{ data: { registration_campaigns_enabled: boolean } }>
    >(),
  post: vi.fn<(url: string, data?: unknown) => Promise<unknown>>(),
}))

const calendarConnectMock = vi.hoisted(() => ({
  connectCalendarIfNeeded: vi.fn<() => Promise<{ status: string; message?: string }>>(),
}))

vi.mock('@/firebase', () => ({ auth: {} }))
vi.mock('@/services/api', () => ({ api: apiMocks }))
vi.mock('@/services/calendarConnect', () => ({
  connectCalendarIfNeeded: calendarConnectMock.connectCalendarIfNeeded,
}))

const STREAM_URL = '/api/v1/recommendations/stream'
const CHAT_STREAM_URL = '/api/v1/recommendations/chat/stream'

const bestNowCard = {
  id: 'card-1',
  bank_name: '玉山銀行',
  name: 'Unicard',
  issuer_en: 'E.SUN Bank',
  name_en: 'E.SUN Unicard',
}

const waitCard = {
  id: 'card-2',
  bank_name: '台新銀行',
  name: '@GoGo 卡',
  issuer_en: 'Taishin International Bank',
  name_en: '@GoGo card',
}

const recommendation = {
  mode: 'no_registration',
  best_now: {
    candidate_type: 'campaign',
    card: bestNowCard,
    sale_id: 'esun-unicard-2026',
    benefit_id: null,
    campaign_title: '指定網購加碼',
    estimated_reward_twd: 224.7,
    rate_display: '3%',
    cap_description: '每月上限 500 點',
    requires_registration: false,
    registration_url: null,
    reason: '此卡在網購通路提供較高回饋。',
    verification_status: 'verified',
    official_sources: [{ title: '玉山官方活動頁', url: 'https://www.esunbank.com/promo' }],
  },
  wait_suggestion: {
    recommended: true,
    card: waitCard,
    sale_id: 'taishin-gogo-2026',
    starts_at: '2026-09-23',
    estimated_reward_twd: 280,
    estimated_extra_reward_twd: 55.3,
    reason: '9/23 起有更高的網購回饋。',
    official_sources: [
      { title: '台新官方活動頁', url: 'https://www.taishinbank.com.tw/gogo' },
      { title: '不安全連結', url: 'javascript:alert(1)' },
    ],
    calendar_draft: {
      title: 'momo 購買 AirPods Pro',
      starts_at: '2026-09-23T09:00:00+08:00',
      notes: '活動：GoGo 網購加碼\n條件：需以 @GoGo 卡刷卡',
    },
  },
  explanation: null,
}

const validQuery = {
  platform: 'momo',
  price: '7490',
  category: 'AirPods Pro',
}

const encoder = new TextEncoder()

function sse(event: string, data: unknown) {
  return `event: ${event}\ndata: ${typeof data === 'string' ? data : JSON.stringify(data)}\n\n`
}

const fullStream = [
  sse('searching', { stage: 'preprocessing', mode: 'no_registration' }),
  sse('searching', { stage: 'official_verification' }),
  sse('recommendation', recommendation),
  sse('done', {}),
].join('')

// Splits into small byte slices so events (and multi-byte characters) straddle chunks.
function chunked(text: string, size = 7) {
  const bytes = encoder.encode(text)
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (let index = 0; index < bytes.length; index += size) {
        controller.enqueue(bytes.slice(index, index + size))
      }
      controller.close()
    },
  })
}

function controlledStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  let cancelled = false
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c
    },
    cancel() {
      cancelled = true
    },
  })

  return {
    body,
    isCancelled: () => cancelled,
    // A real server cannot write into a connection the client already dropped.
    push: (text: string) => {
      if (!cancelled) controller.enqueue(encoder.encode(text))
    },
    close: () => {
      if (!cancelled) controller.close()
    },
  }
}

function okResponse(body: ReadableStream<Uint8Array> | null): Response {
  return { ok: true, status: 200, body } as Response
}

function failedResponse(status: number): Response {
  return { ok: false, status, body: null } as Response
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })

  return { promise, reject, resolve }
}

const fetchMock = vi.fn<typeof fetch>()
const mountedWrappers: VueWrapper[] = []

function makeUser(token = 'firebase-token') {
  return {
    uid: 'user-1',
    getIdToken: vi.fn<() => Promise<string>>().mockResolvedValue(token),
  } as unknown as User
}

async function settle() {
  await flushPromises()
  await flushPromises()
}

async function mountRecommendations({
  query = validQuery,
  ready = true,
  user = makeUser() as User | null,
}: {
  query?: LocationQueryRaw
  ready?: boolean
  user?: User | null
} = {}) {
  const pinia = createPinia()
  const authStore = useAuthStore(pinia)
  authStore.ready = ready
  authStore.user = user

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/recommendations',
        name: 'recommendations',
        component: RecommendationsView,
      },
    ],
  })

  await router.push({ name: 'recommendations', query })
  await router.isReady()

  const wrapper = mount(RecommendationsView, {
    global: {
      plugins: [pinia, router, i18n],
      stubs: { SiteHeader: true },
    },
  })
  mountedWrappers.push(wrapper)
  await settle()

  return { authStore, router, user, wrapper }
}

function requestInit(call = 0) {
  return fetchMock.mock.calls[call]![1] as RequestInit
}

beforeEach(() => {
  setLocale('zh-TW', false)
  apiMocks.get.mockReset()
  apiMocks.get.mockResolvedValue({ data: { registration_campaigns_enabled: false } })
  apiMocks.patch.mockReset()
  apiMocks.patch.mockImplementation(async (_url, data) => ({ data }))
  apiMocks.post.mockReset()
  apiMocks.post.mockResolvedValue({})
  calendarConnectMock.connectCalendarIfNeeded.mockReset()
  calendarConnectMock.connectCalendarIfNeeded.mockResolvedValue({ status: 'already-connected' })
  fetchMock.mockReset()
  fetchMock.mockImplementation(() => Promise.resolve(okResponse(chunked(fullStream))))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount()
  vi.unstubAllGlobals()
})

describe('RecommendationsView', () => {
  it('loads the registration setting alongside the initial recommendation stream', async () => {
    const preference = deferred<{ data: { registration_campaigns_enabled: boolean } }>()
    apiMocks.get.mockReturnValueOnce(preference.promise)
    const { wrapper } = await mountRecommendations()

    expect(apiMocks.get).toHaveBeenCalledWith('/me')
    expect(fetchMock).toHaveBeenCalledOnce()

    const toggle = wrapper.get<HTMLButtonElement>('#registration-campaigns-toggle')
    expect(toggle.attributes('role')).toBe('switch')
    expect(toggle.attributes('disabled')).toBeDefined()
    expect(wrapper.get('label[for="registration-campaigns-toggle"]').text()).toBe(
      '是否已登錄信用卡活動',
    )
    expect(wrapper.find('#registration-preference-message').exists()).toBe(false)
    expect(wrapper.get('label[for="recommendations-web-search"]').text()).toBe('啟用網路搜尋')
    const webSearchToggle = wrapper.get('#recommendations-web-search')
    expect(webSearchToggle.attributes()).toMatchObject({
      role: 'switch',
      'aria-checked': 'true',
    })
    expect(webSearchToggle.attributes('aria-describedby')).toBeUndefined()
    expect(wrapper.find('#recommendations-web-search-hint').exists()).toBe(false)

    preference.resolve({ data: { registration_campaigns_enabled: true } })
    await settle()

    expect(toggle.attributes('aria-checked')).toBe('true')
    expect(toggle.attributes('disabled')).toBeUndefined()
  })

  it('updates the registration setting without starting another search', async () => {
    const update = deferred<{ data: { registration_campaigns_enabled: boolean } }>()
    apiMocks.patch.mockReturnValueOnce(update.promise)
    const { wrapper } = await mountRecommendations()

    await wrapper.get('.registration-switch__track').trigger('click')
    await settle()

    expect(apiMocks.patch).toHaveBeenCalledWith('/me', {
      registration_campaigns_enabled: true,
    })
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(wrapper.get('#registration-campaigns-toggle').attributes('disabled')).toBeDefined()
    expect(wrapper.get('.query-submit').attributes('disabled')).toBeDefined()

    update.resolve({ data: { registration_campaigns_enabled: true } })
    await settle()

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(wrapper.find('[data-testid="best-now"]').exists()).toBe(true)
  })

  it('updates the setting without validating form values or clearing the old result', async () => {
    const { wrapper } = await mountRecommendations()
    expect(wrapper.find('[data-testid="best-now"]').exists()).toBe(true)

    await wrapper.get<HTMLInputElement>('#recommendation-platform').setValue('')
    await wrapper.get('.registration-switch__track').trigger('click')
    await settle()

    expect(apiMocks.patch).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(wrapper.find('[data-testid="best-now"]').exists()).toBe(true)
    expect(wrapper.findAll('.query-field__error')).toHaveLength(0)
  })

  it('restores the toggle and keeps the recommendation when the patch fails', async () => {
    apiMocks.patch.mockRejectedValueOnce(new Error('network'))
    const { wrapper } = await mountRecommendations()

    await wrapper.get('.registration-switch__track').trigger('click')
    await settle()

    expect(wrapper.get('#registration-campaigns-toggle').attributes('aria-checked')).toBe('false')
    expect(wrapper.find('[data-testid="best-now"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="registration-preference-error"]').text()).toBe(
      '無法更新登錄活動設定，請稍後再試。',
    )
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('disables the toggle when the registration setting cannot be loaded', async () => {
    apiMocks.get.mockRejectedValueOnce(new Error('network'))
    const { wrapper } = await mountRecommendations()

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(wrapper.get('#registration-campaigns-toggle').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="registration-preference-error"]').text()).toBe(
      '無法讀取登錄活動設定，請重新整理後再試。',
    )
  })

  it('aborts the active stream without starting another search after saving the setting', async () => {
    const first = controlledStream()
    fetchMock.mockResolvedValueOnce(okResponse(first.body))
    const { wrapper } = await mountRecommendations()

    await wrapper.get('.registration-switch__track').trigger('click')
    await settle()

    expect(first.isCancelled()).toBe(true)
    expect(apiMocks.patch).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(wrapper.get('#registration-campaigns-toggle').attributes('aria-checked')).toBe('true')
    expect(wrapper.find('.state-panel--error').exists()).toBe(false)
  })

  it('posts to the SSE endpoint with the Firebase token and mapped body', async () => {
    const { user } = await mountRecommendations()

    expect(user!.getIdToken).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0]![0]).toBe(STREAM_URL)

    const init = requestInit()
    expect(init.method).toBe('POST')
    expect(init.headers).toEqual({
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      Authorization: 'Bearer firebase-token',
    })
    expect(init.signal).toBeInstanceOf(AbortSignal)
    expect(JSON.parse(init.body as string)).toEqual({
      product_name: 'AirPods Pro',
      store_name: 'momo',
      price: 7490,
      currency: 'TWD',
      locale: 'zh-TW',
      web_search_enabled: true,
    })
    expect(JSON.parse(init.body as string)).not.toHaveProperty('include_unowned')
  })

  it('restores web search from the route and applies changes only after submit', async () => {
    const { router, wrapper } = await mountRecommendations({
      query: { ...validQuery, webSearch: '1' },
    })
    const toggle = wrapper.get('#recommendations-web-search')

    expect(toggle.attributes('aria-checked')).toBe('true')
    expect(JSON.parse(requestInit().body as string).web_search_enabled).toBe(true)

    await toggle.trigger('click')

    expect(toggle.attributes('aria-checked')).toBe('false')
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.query.webSearch).toBe('1')

    await wrapper.get('.query-form').trigger('submit')
    await settle()

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(router.currentRoute.value.query.webSearch).toBe('0')
    expect(JSON.parse(requestInit(1).body as string).web_search_enabled).toBe(false)
  })

  it('treats unsupported webSearch route values as disabled', async () => {
    const { wrapper } = await mountRecommendations({
      query: { ...validQuery, webSearch: 'true' },
    })

    expect(wrapper.get('#recommendations-web-search').attributes('aria-checked')).toBe('false')
    expect(JSON.parse(requestInit().body as string).web_search_enabled).toBe(false)
  })

  it('opens the chat and streams an answer with the bound recommendation context', async () => {
    const answer = [
      sse('delta', { text: '這張卡的回饋上限是每月 500 點。\n\n' }),
      sse('delta', {
        text: '[官方活動頁](https://www.esunbank.com/promo) <script>alert(1)</script> ![圖](https://example.com/a.png)',
      }),
      sse('done', {}),
    ].join('')
    fetchMock
      .mockResolvedValueOnce(okResponse(chunked(fullStream)))
      .mockResolvedValueOnce(okResponse(chunked(answer)))

    const { user, wrapper } = await mountRecommendations()

    expect(wrapper.get('[data-testid="chat-launcher"]').text()).toContain('詢問這次推薦')
    await wrapper.get('[data-testid="chat-launcher"]').trigger('click')
    expect(wrapper.get('.chat-messages__hint').text()).toContain('這次推薦')
    await wrapper.get<HTMLInputElement>('#recommendation-platform').setValue('尚未送出的新地點')

    const input = wrapper.get<HTMLTextAreaElement>('#recommendation-chat-question')
    expect(input.attributes('maxlength')).toBe('2000')
    await input.setValue('x'.repeat(2001))
    await wrapper.get('.chat-composer').trigger('submit')
    expect(fetchMock).toHaveBeenCalledOnce()
    await input.setValue('這個 3% 有上限嗎？')
    await input.trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(fetchMock).toHaveBeenCalledOnce()
    await input.trigger('keydown', { key: 'Enter' })
    await settle()

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(user!.getIdToken).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[1]![0]).toBe(CHAT_STREAM_URL)
    const init = requestInit(1)
    expect(init.headers).toEqual({
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      Authorization: 'Bearer firebase-token',
    })
    expect(init.signal).toBeInstanceOf(AbortSignal)
    expect(JSON.parse(init.body as string)).toEqual({
      purchase: {
        store_name: 'momo',
        product_name: 'AirPods Pro',
        price: 7490,
        currency: 'TWD',
      },
      recommendation,
      messages: [],
      question: '這個 3% 有上限嗎？',
    })

    const response = wrapper.get('[data-testid="chat-message-assistant"]')
    expect(response.text()).toContain('每月 500 點')
    expect(response.find('script').exists()).toBe(false)
    expect(response.find('img').exists()).toBe(false)
    const source = response.get('a[href="https://www.esunbank.com/promo"]')
    expect(source.attributes('target')).toBe('_blank')
    expect(source.attributes('rel')).toBe('noopener noreferrer')
  })

  it('sends only the latest 12 completed chat messages', async () => {
    const answer = sse('delta', { text: '回答' }) + sse('done', {})
    fetchMock.mockImplementation((url) =>
      Promise.resolve(okResponse(chunked(String(url) === CHAT_STREAM_URL ? answer : fullStream))),
    )
    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="chat-launcher"]').trigger('click')

    const input = wrapper.get<HTMLTextAreaElement>('#recommendation-chat-question')
    for (let index = 1; index <= 8; index += 1) {
      await input.setValue(`問題 ${index}`)
      await wrapper.get('.chat-composer').trigger('submit')
      await settle()
    }

    const body = JSON.parse(requestInit(8).body as string)
    expect(body.question).toBe('問題 8')
    expect(body.messages).toHaveLength(12)
    expect(body.messages[0]).toEqual({ role: 'user', content: '問題 2' })
    expect(body.messages.at(-1)).toEqual({ role: 'assistant', content: '回答' })
    expect(wrapper.findAll('[data-testid="chat-message-user"]')).toHaveLength(8)
  })

  it('keeps a failed question for retry without duplicating it', async () => {
    const answer = sse('delta', { text: '重試成功' }) + sse('done', {})
    fetchMock
      .mockResolvedValueOnce(okResponse(chunked(fullStream)))
      .mockResolvedValueOnce(failedResponse(503))
      .mockResolvedValueOnce(okResponse(chunked(answer)))
    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="chat-launcher"]').trigger('click')
    await wrapper.get<HTMLTextAreaElement>('#recommendation-chat-question').setValue('原問題')
    await wrapper.get('.chat-composer').trigger('submit')
    await settle()

    expect(wrapper.get('.chat-message__error').text()).toContain('無法取得回答')
    expect(wrapper.findAll('[data-testid="chat-message-user-pending"]')).toHaveLength(1)

    await wrapper.get('.chat-message__error button').trigger('click')
    await settle()

    expect(JSON.parse(requestInit(1).body as string).question).toBe('原問題')
    expect(JSON.parse(requestInit(2).body as string).question).toBe('原問題')
    expect(wrapper.find('[data-testid="chat-message-user-pending"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid="chat-message-user"]')).toHaveLength(1)
    expect(wrapper.get('[data-testid="chat-message-assistant"]').text()).toContain('重試成功')
  })

  it('discards a failed exchange when a new question is sent', async () => {
    const answer = sse('delta', { text: '新回答' }) + sse('done', {})
    fetchMock
      .mockResolvedValueOnce(okResponse(chunked(fullStream)))
      .mockResolvedValueOnce(failedResponse(503))
      .mockResolvedValueOnce(okResponse(chunked(answer)))
    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="chat-launcher"]').trigger('click')
    const input = wrapper.get<HTMLTextAreaElement>('#recommendation-chat-question')

    await input.setValue('失敗的問題')
    await wrapper.get('.chat-composer').trigger('submit')
    await settle()
    await input.setValue('新的問題')
    await wrapper.get('.chat-composer').trigger('submit')
    await settle()

    const body = JSON.parse(requestInit(2).body as string)
    expect(body.messages).toEqual([])
    expect(body.question).toBe('新的問題')
    expect(wrapper.text()).not.toContain('失敗的問題')
    expect(wrapper.get('[data-testid="chat-message-user"]').text()).toBe('新的問題')
  })

  it('continues a collapsed stream and marks the completed answer as unread', async () => {
    const stream = controlledStream()
    fetchMock
      .mockResolvedValueOnce(okResponse(chunked(fullStream)))
      .mockResolvedValueOnce(okResponse(stream.body))
    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="chat-launcher"]').trigger('click')
    await wrapper.get<HTMLTextAreaElement>('#recommendation-chat-question').setValue('有上限嗎？')
    await wrapper.get('.chat-composer').trigger('submit')
    await settle()

    await wrapper.get('.chat-panel__close').trigger('click')
    expect(stream.isCancelled()).toBe(false)
    expect(wrapper.find('[data-testid="chat-launcher"] .spinner').exists()).toBe(true)

    stream.push(sse('delta', { text: '有，每月 500 點。' }) + sse('done', {}))
    stream.close()
    await settle()

    expect(wrapper.find('.chat-launcher__unread').exists()).toBe(true)
    await wrapper.get('[data-testid="chat-launcher"]').trigger('click')
    expect(wrapper.find('.chat-launcher__unread').exists()).toBe(false)
    expect(wrapper.get('[data-testid="chat-message-assistant"]').text()).toContain('500 點')
  })

  it('follows chat deltas only when the message list is near the bottom', async () => {
    const stream = controlledStream()
    fetchMock
      .mockResolvedValueOnce(okResponse(chunked(fullStream)))
      .mockResolvedValueOnce(okResponse(stream.body))
    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="chat-launcher"]').trigger('click')
    await wrapper.get<HTMLTextAreaElement>('#recommendation-chat-question').setValue('問題')
    await wrapper.get('.chat-composer').trigger('submit')
    await settle()

    const list = wrapper.get<HTMLElement>('.chat-messages').element
    Object.defineProperties(list, {
      clientHeight: { configurable: true, value: 100 },
      scrollHeight: { configurable: true, value: 500 },
      scrollTop: { configurable: true, value: 0, writable: true },
    })

    stream.push(sse('delta', { text: '第一段' }))
    await settle()
    expect(list.scrollTop).toBe(0)

    list.scrollTop = 400
    stream.push(sse('delta', { text: '第二段' }))
    await settle()
    expect(list.scrollTop).toBe(500)

    stream.push(sse('done', {}))
    stream.close()
    await settle()
  })

  it('cancels and clears chat when a new recommendation starts', async () => {
    const chatStream = controlledStream()
    fetchMock
      .mockResolvedValueOnce(okResponse(chunked(fullStream)))
      .mockResolvedValueOnce(okResponse(chatStream.body))
      .mockResolvedValueOnce(okResponse(chunked(fullStream)))
    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="chat-launcher"]').trigger('click')
    await wrapper.get<HTMLTextAreaElement>('#recommendation-chat-question').setValue('舊問題')
    await wrapper.get('.chat-composer').trigger('submit')
    await settle()

    await wrapper.get<HTMLInputElement>('#recommendation-platform').setValue('蝦皮')
    await wrapper.get('form.query-form').trigger('submit')
    await settle()

    expect(chatStream.isCancelled()).toBe(true)
    expect(wrapper.find('[data-testid="chat-launcher"]').exists()).toBe(true)
    await wrapper.get('[data-testid="chat-launcher"]').trigger('click')
    expect(wrapper.find('.chat-messages__hint').exists()).toBe(true)
    expect(wrapper.find('[data-testid^="chat-message-"]').exists()).toBe(false)
  })

  it('shows chat for an empty recommendation and reports invalid chat streams', async () => {
    const emptyRecommendation = {
      ...recommendation,
      best_now: null,
      wait_suggestion: null,
      explanation: '目前沒有適用優惠。',
    }
    fetchMock
      .mockResolvedValueOnce(
        okResponse(chunked(sse('recommendation', emptyRecommendation) + sse('done', {}))),
      )
      .mockResolvedValueOnce(okResponse(chunked(sse('delta', { value: '錯誤格式' }))))
    const { wrapper } = await mountRecommendations()

    expect(wrapper.find('[data-testid="chat-launcher"]').exists()).toBe(true)
    await wrapper.get('[data-testid="chat-launcher"]').trigger('click')
    await wrapper.get<HTMLTextAreaElement>('#recommendation-chat-question').setValue('為什麼？')
    await wrapper.get('.chat-composer').trigger('submit')
    await settle()

    expect(wrapper.get('.chat-message__error').text()).toContain('無法取得回答')
  })

  it('requires a done event and shows the sign-in error for a chat 401', async () => {
    fetchMock
      .mockResolvedValueOnce(okResponse(chunked(fullStream)))
      .mockResolvedValueOnce(okResponse(chunked(sse('delta', { text: '未完成回答' }))))
      .mockResolvedValueOnce(failedResponse(401))
    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="chat-launcher"]').trigger('click')
    const input = wrapper.get<HTMLTextAreaElement>('#recommendation-chat-question')

    await input.setValue('第一題')
    await wrapper.get('.chat-composer').trigger('submit')
    await settle()
    expect(wrapper.get('[data-testid="chat-message-assistant-pending"]').text()).toContain(
      '未完成回答',
    )
    expect(wrapper.get('.chat-message__error').text()).toContain('無法取得回答')

    await input.setValue('第二題')
    await wrapper.get('.chat-composer').trigger('submit')
    await settle()
    expect(wrapper.get('.chat-message__error').text()).toContain('登入狀態已失效')
  })

  it('cancels the chat stream when the page unmounts', async () => {
    const stream = controlledStream()
    fetchMock
      .mockResolvedValueOnce(okResponse(chunked(fullStream)))
      .mockResolvedValueOnce(okResponse(stream.body))
    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="chat-launcher"]').trigger('click')
    await wrapper.get<HTMLTextAreaElement>('#recommendation-chat-question').setValue('問題')
    await wrapper.get('.chat-composer').trigger('submit')
    await settle()

    wrapper.unmount()
    mountedWrappers.splice(mountedWrappers.indexOf(wrapper), 1)
    await settle()

    expect(stream.isCancelled()).toBe(true)
  })

  it('cancels and repeats a valid search when the locale changes', async () => {
    const first = controlledStream()
    fetchMock
      .mockImplementationOnce(() => Promise.resolve(okResponse(first.body)))
      .mockImplementationOnce(() => Promise.resolve(okResponse(chunked(fullStream))))

    const { wrapper } = await mountRecommendations({
      query: { ...validQuery, webSearch: '1' },
    })
    expect(fetchMock).toHaveBeenCalledOnce()

    setLocale('en-US')
    await settle()

    expect(first.isCancelled()).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(JSON.parse(requestInit(1).body as string)).toMatchObject({
      locale: 'en-US',
      web_search_enabled: true,
    })
    expect(wrapper.get('#ranking-title').text()).toBe('Best Card Right Now')
    expect(wrapper.get('[data-testid="best-now"]').text()).toContain('NT$224.70')
    expect(wrapper.get('[data-testid="best-now"]').text()).toContain('E.SUN Bank')
    expect(wrapper.get('[data-testid="record-purchase-best-now"]').text()).toContain(
      'Record purchase with E.SUN Unicard',
    )
  })

  it('shows searching progress in the loading state until the recommendation arrives', async () => {
    const stream = controlledStream()
    fetchMock.mockResolvedValue(okResponse(stream.body))

    const { wrapper } = await mountRecommendations()

    expect(wrapper.get('[role="status"]').text()).toContain('正在計算信用卡推薦')

    stream.push(sse('searching', { stage: 'preprocessing' }))
    await settle()
    expect(wrapper.get('[role="status"]').text()).toContain('正在整理你的持卡資料與優惠活動')

    stream.push(sse('searching', { stage: 'official_verification' }))
    await settle()
    expect(wrapper.get('[role="status"]').text()).toContain('正在核對銀行官方活動')
    expect(wrapper.find('.recommendation-card').exists()).toBe(false)

    stream.push(sse('recommendation', recommendation))
    await settle()
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="best-now"]').exists()).toBe(true)

    stream.push(sse('done', {}))
    stream.close()
    await settle()
    expect(wrapper.find('[data-testid="best-now"]').exists()).toBe(true)
  })

  it('renders best_now, official sources and the wait suggestion from chunked events', async () => {
    const { wrapper } = await mountRecommendations()

    const best = wrapper.get('[data-testid="best-now"]')
    expect(best.text()).toContain('Unicard')
    expect(best.text()).toContain('玉山銀行')
    expect(best.text()).toContain('224.7')
    expect(best.text()).toContain('3%')
    expect(best.text()).toContain('每月上限 500 點')
    expect(best.text()).toContain('不需登錄')
    expect(best.text()).toContain('此卡在網購通路提供較高回饋。')
    expect(best.text()).toContain('已查證官方來源')
    expect(best.text()).not.toContain('前往登錄')

    const official = best.get('a[href="https://www.esunbank.com/promo"]')
    expect(official.text()).toContain('玉山官方活動頁')
    expect(official.attributes('rel')).toBe('noopener noreferrer')
    expect(official.attributes('target')).toBe('_blank')

    const wait = wrapper.get('[data-testid="wait-suggestion"]')
    expect(wait.text()).toContain('等待活動')
    expect(wait.text()).toContain('@GoGo 卡')
    expect(wait.text()).toContain('台新銀行')
    expect(wait.text()).not.toContain('Unicard')
    expect(wait.text()).toContain('2026年9月23日')
    expect(wait.text()).toContain('280')
    expect(wait.text()).toContain('55.3')
    expect(wait.text()).toContain('9/23 起有更高的網購回饋。')
    expect(wait.find('a[href="https://www.taishinbank.com.tw/gogo"]').exists()).toBe(true)
    expect(wait.find('a[href^="javascript:"]').exists()).toBe(false)

    const draft = wait.get('[data-testid="calendar-draft"]')
    expect(draft.text()).toContain('momo 購買 AirPods Pro')
    expect(draft.text()).toContain('活動：GoGo 網購加碼')
    expect(draft.text()).toContain('條件：需以 @GoGo 卡刷卡')
    expect(draft.text()).toContain('尚未建立')
    expect(draft.get('[data-testid="add-to-calendar"]').text()).toContain('加入行事曆')
    expect(best.get('[data-testid="record-purchase-best-now"]').text()).toContain('Unicard')
    expect(wait.get('[data-testid="record-purchase-wait-suggestion"]').text()).toContain('@GoGo 卡')
  })

  it('turns the calendar draft into an event with the campaign sale id', async () => {
    apiMocks.post.mockResolvedValue({ data: { already_notified: false } })

    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="add-to-calendar"]').trigger('click')
    await settle()

    expect(apiMocks.post).toHaveBeenCalledExactlyOnceWith('/me/calendar/events', {
      title: recommendation.wait_suggestion.calendar_draft.title,
      starts_at: recommendation.wait_suggestion.calendar_draft.starts_at,
      notes: recommendation.wait_suggestion.calendar_draft.notes,
      sale_id: recommendation.wait_suggestion.sale_id,
    })

    const status = wrapper.get('[data-testid="calendar-status"]')
    expect(status.text()).toContain('已加入 Google 行事曆')
    // The draft hint and the button both retire once the event is real.
    expect(wrapper.find('[data-testid="add-to-calendar"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="calendar-draft"]').text()).not.toContain('尚未建立')
  })

  it('reports a campaign that was already flagged without duplicating it', async () => {
    apiMocks.post.mockResolvedValue({ data: { already_notified: true } })

    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="add-to-calendar"]').trigger('click')
    await settle()

    expect(wrapper.get('[data-testid="calendar-status"]').text()).toContain('先前已提醒過')
  })

  it('asks for calendar consent before creating the event', async () => {
    calendarConnectMock.connectCalendarIfNeeded.mockResolvedValue({ status: 'connected' })
    apiMocks.post.mockResolvedValue({ data: { already_notified: false } })

    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="add-to-calendar"]').trigger('click')
    await settle()

    expect(calendarConnectMock.connectCalendarIfNeeded).toHaveBeenCalledOnce()
    expect(apiMocks.post).toHaveBeenCalledWith('/me/calendar/events', expect.anything())
  })

  it('does not create an event when consent is declined', async () => {
    calendarConnectMock.connectCalendarIfNeeded.mockResolvedValue({ status: 'declined' })

    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="add-to-calendar"]').trigger('click')
    await settle()

    expect(apiMocks.post).not.toHaveBeenCalled()
    const status = wrapper.get('[data-testid="calendar-status"]')
    expect(status.text()).toContain('需要連結 Google 行事曆')
    expect(status.attributes('role')).toBe('alert')
    // Still retryable: the button stays put.
    expect(wrapper.find('[data-testid="add-to-calendar"]').exists()).toBe(true)
  })

  it('keeps the button available after a failed attempt', async () => {
    apiMocks.post.mockRejectedValueOnce(new Error('network'))

    const { wrapper } = await mountRecommendations()
    await wrapper.get('[data-testid="add-to-calendar"]').trigger('click')
    await settle()

    expect(wrapper.get('[data-testid="calendar-status"]').text()).toContain('無法加入行事曆')

    apiMocks.post.mockResolvedValue({ data: { already_notified: false } })
    await wrapper.get('[data-testid="add-to-calendar"]').trigger('click')
    await settle()

    expect(wrapper.get('[data-testid="calendar-status"]').text()).toContain('已加入 Google 行事曆')
  })

  it('records one selected card with the criteria that produced the result', async () => {
    let finishPurchase!: () => void
    apiMocks.post.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishPurchase = () => resolve({})
        }),
    )

    const { wrapper } = await mountRecommendations()
    await wrapper.get<HTMLInputElement>('#recommendation-platform').setValue('蝦皮')

    const bestButton = wrapper.get<HTMLButtonElement>('[data-testid="record-purchase-best-now"]')
    const waitButton = wrapper.get<HTMLButtonElement>(
      '[data-testid="record-purchase-wait-suggestion"]',
    )
    await waitButton.trigger('click')
    await bestButton.trigger('click')

    expect(apiMocks.post).toHaveBeenCalledExactlyOnceWith('/me/purchases', {
      card_id: waitCard.id,
      sale_id: recommendation.wait_suggestion.sale_id,
      product_name: 'AirPods Pro',
      store_name: 'momo',
      price: 7490,
      currency: 'TWD',
    })
    expect(bestButton.element.disabled).toBe(true)
    expect(waitButton.element.disabled).toBe(true)
    expect(waitButton.attributes('aria-busy')).toBe('true')

    finishPurchase()
    await settle()

    expect(waitButton.attributes('data-state')).toBe('success')
    expect(wrapper.get('[role="status"]').text()).toBe('已記錄這筆消費')
  })

  it('shows a recording error and allows retrying', async () => {
    apiMocks.post.mockRejectedValueOnce(new Error('Request failed')).mockResolvedValueOnce({})
    const { wrapper } = await mountRecommendations()
    const bestButton = wrapper.get<HTMLButtonElement>('[data-testid="record-purchase-best-now"]')
    const waitButton = wrapper.get<HTMLButtonElement>(
      '[data-testid="record-purchase-wait-suggestion"]',
    )

    await bestButton.trigger('click')
    await settle()

    expect(wrapper.get('[role="alert"]').text()).toBe('無法記錄消費，請稍後再試。')
    expect(bestButton.attributes('data-state')).toBe('error')
    expect(bestButton.element.disabled).toBe(false)
    expect(waitButton.element.disabled).toBe(false)

    await bestButton.trigger('click')
    await settle()

    expect(apiMocks.post).toHaveBeenCalledTimes(2)
    expect(wrapper.get('[role="status"]').text()).toBe('已記錄這筆消費')
  })

  it('does not apply a stale purchase response to new search results', async () => {
    let finishPurchase!: () => void
    apiMocks.post.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishPurchase = () => resolve({})
        }),
    )
    const { wrapper } = await mountRecommendations()

    await wrapper.get('[data-testid="record-purchase-best-now"]').trigger('click')
    await wrapper.get<HTMLInputElement>('#recommendation-platform').setValue('蝦皮')
    await wrapper.get('form').trigger('submit')
    await settle()

    const newButton = wrapper.get<HTMLButtonElement>('[data-testid="record-purchase-best-now"]')
    expect(newButton.attributes('data-state')).toBe('idle')
    expect(newButton.element.disabled).toBe(false)

    finishPurchase()
    await settle()

    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    expect(newButton.attributes('data-state')).toBe('idle')
  })

  it('labels a limited-time campaign and a base benefit differently', async () => {
    const first = await mountRecommendations()
    expect(first.wrapper.get('[data-testid="candidate-type"]').text()).toBe('限時活動')

    const baseBenefit = {
      ...recommendation,
      best_now: {
        ...recommendation.best_now,
        candidate_type: 'base_benefit',
        sale_id: null,
        benefit_id: 'benefit-1',
        campaign_title: '國內一般消費',
        rate_display: '1%',
      },
      wait_suggestion: null,
    }
    fetchMock.mockResolvedValue(
      okResponse(chunked(sse('recommendation', baseBenefit) + sse('done', {}))),
    )
    const second = await mountRecommendations()
    const best = second.wrapper.get('[data-testid="best-now"]')
    expect(best.get('[data-testid="candidate-type"]').text()).toBe('基本回饋')
    expect(best.text()).toContain('國內一般消費')
    expect(best.text()).toContain('1%')
  })

  it('offers a registration link and unverified status when the backend says so', async () => {
    const unverified = {
      ...recommendation,
      mode: 'registration',
      best_now: {
        ...recommendation.best_now,
        requires_registration: true,
        registration_url: 'https://www.esunbank.com/register',
        verification_status: 'unverified',
        official_sources: [],
      },
      wait_suggestion: null,
    }
    fetchMock.mockResolvedValue(
      okResponse(chunked(sse('recommendation', unverified) + sse('done', {}))),
    )

    const { wrapper } = await mountRecommendations()

    const best = wrapper.get('[data-testid="best-now"]')
    expect(best.text()).toContain('需要登錄')
    expect(best.text()).toContain('尚未查證官方來源')
    expect(best.get('a[href="https://www.esunbank.com/register"]').text()).toContain('前往登錄')
    expect(wrapper.text()).toContain('僅列出需要登錄的優惠')
    expect(wrapper.find('[data-testid="wait-suggestion"]').exists()).toBe(false)
  })

  it('shows the backend explanation instead of a card when best_now is null', async () => {
    const empty = {
      mode: 'no_registration',
      best_now: null,
      wait_suggestion: null,
      explanation: '目前持有的卡片中，沒有符合此購物條件且屬「免登錄」的有效優惠。',
    }
    fetchMock.mockResolvedValue(okResponse(chunked(sse('recommendation', empty) + sse('done', {}))))

    const { wrapper } = await mountRecommendations()

    expect(wrapper.find('[data-testid="no-best-now"]').text()).toContain(empty.explanation)
    expect(wrapper.find('.recommendation-card').exists()).toBe(false)
    expect(wrapper.find('[data-testid="best-now"]').exists()).toBe(false)
    expect(wrapper.find('.purchase-button').exists()).toBe(false)
  })

  it('shows the existing error UI for an SSE error event and retries', async () => {
    fetchMock.mockResolvedValueOnce(
      okResponse(
        chunked(sse('searching', { stage: 'preprocessing' }) + sse('error', { message: 'x' })),
      ),
    )

    const { wrapper } = await mountRecommendations()

    expect(wrapper.get('[role="alert"]').text()).toBe('無法取得信用卡推薦，請稍後再試。')
    expect(wrapper.find('.recommendation-card').exists()).toBe(false)

    await wrapper.get('.state-panel--error button').trigger('click')
    await settle()

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="best-now"]').exists()).toBe(true)
  })

  it('shows an error for non-2xx responses', async () => {
    fetchMock.mockResolvedValue(failedResponse(503))

    const { wrapper } = await mountRecommendations()

    expect(wrapper.get('[role="alert"]').text()).toBe('無法取得信用卡推薦，請稍後再試。')
  })

  it('asks the user to sign in again on a 401', async () => {
    fetchMock.mockResolvedValue(failedResponse(401))

    const { wrapper } = await mountRecommendations()

    expect(wrapper.get('[role="alert"]').text()).toContain('請先登入')
  })

  it('shows an error for malformed payloads and streams that end without a result', async () => {
    fetchMock.mockResolvedValueOnce(okResponse(chunked(sse('recommendation', 'not json'))))
    const first = await mountRecommendations()
    expect(first.wrapper.get('[role="alert"]').text()).toBe('無法取得信用卡推薦，請稍後再試。')
    expect(first.wrapper.find('[data-testid="best-now"]').exists()).toBe(false)

    fetchMock.mockResolvedValueOnce(okResponse(chunked(sse('recommendation', { mode: 'oops' }))))
    const second = await mountRecommendations()
    expect(second.wrapper.get('[role="alert"]').text()).toBe('無法取得信用卡推薦，請稍後再試。')

    fetchMock.mockResolvedValueOnce(okResponse(chunked(sse('done', {}))))
    const third = await mountRecommendations()
    expect(third.wrapper.get('[role="alert"]').text()).toBe('無法取得信用卡推薦，請稍後再試。')
  })

  it('does not send a request and asks for sign-in when there is no user', async () => {
    const { wrapper } = await mountRecommendations({ user: null })

    expect(fetchMock).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('請先登入')
    expect(wrapper.find('.recommendation-card').exists()).toBe(false)
  })

  it('waits for auth initialization before requesting', async () => {
    const { authStore, wrapper } = await mountRecommendations({ ready: false })

    expect(wrapper.get('[role="status"]').text()).toContain('正在計算信用卡推薦')
    expect(fetchMock).not.toHaveBeenCalled()

    authStore.ready = true
    await settle()

    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('keeps invalid URL values editable without sending a request', async () => {
    const { wrapper } = await mountRecommendations({
      query: { platform: '', price: '0', category: '' },
    })

    expect(fetchMock).not.toHaveBeenCalled()
    expect(wrapper.findAll('[role="alert"]').map((message) => message.text())).toEqual([
      '請輸入消費地點',
      '請輸入有效且大於 0 的消費金額',
      '請輸入品項或類別',
    ])
    expect(wrapper.get<HTMLInputElement>('#recommendation-price').element.value).toBe('0')
  })

  it('aborts the previous stream on a new search and ignores its late events', async () => {
    const first = controlledStream()
    const second = controlledStream()
    fetchMock
      .mockResolvedValueOnce(okResponse(first.body))
      .mockResolvedValueOnce(okResponse(second.body))

    const { router, wrapper } = await mountRecommendations()
    const firstSignal = requestInit(0).signal as AbortSignal
    expect(firstSignal.aborted).toBe(false)

    await wrapper.get<HTMLInputElement>('#recommendation-platform').setValue('蝦皮')
    await wrapper.get('form').trigger('submit')
    await settle()

    expect(router.currentRoute.value.query.platform).toBe('蝦皮')
    expect(firstSignal.aborted).toBe(true)
    expect(first.isCancelled()).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(JSON.parse(requestInit(1).body as string).store_name).toBe('蝦皮')

    second.push(
      sse('recommendation', {
        ...recommendation,
        best_now: { ...recommendation.best_now, card: { ...bestNowCard, name: '新搜尋的卡' } },
        wait_suggestion: null,
      }),
    )
    await settle()
    expect(wrapper.get('[data-testid="best-now"]').text()).toContain('新搜尋的卡')

    first.push(sse('recommendation', recommendation))
    await settle()

    expect(wrapper.get('[data-testid="best-now"]').text()).toContain('新搜尋的卡')
    expect(wrapper.text()).not.toContain('Unicard')
  })

  it('aborts the stream when the page unmounts', async () => {
    const stream = controlledStream()
    fetchMock.mockResolvedValue(okResponse(stream.body))

    const { wrapper } = await mountRecommendations()
    const signal = requestInit().signal as AbortSignal

    wrapper.unmount()
    mountedWrappers.splice(mountedWrappers.indexOf(wrapper), 1)
    await settle()

    expect(signal.aborted).toBe(true)
    expect(stream.isCancelled()).toBe(true)
  })

  it('uses card-art when an artwork id is provided and the fallback otherwise', async () => {
    const withArtwork = {
      ...recommendation,
      best_now: {
        ...recommendation.best_now,
        card: { ...bestNowCard, artwork_id: 'esun-unicard' },
      },
    }
    fetchMock.mockResolvedValue(
      okResponse(chunked(sse('recommendation', withArtwork) + sse('done', {}))),
    )

    const { wrapper } = await mountRecommendations()

    const best = wrapper.get('[data-testid="best-now"]')
    expect(best.get('img').attributes('src')).toBe('/card-art/esun-unicard.webp')
    expect(best.find('.card-art__fallback').exists()).toBe(false)

    const wait = wrapper.get('[data-testid="wait-suggestion"]')
    expect(wait.find('img').exists()).toBe(false)
    expect(wait.find('.card-art__fallback').exists()).toBe(true)
  })

  it('never calls the old search API or the Calendar API', async () => {
    await mountRecommendations()

    const urls = fetchMock.mock.calls.map(([url]) => String(url))
    expect(urls).toEqual([STREAM_URL])
    expect(urls.some((url) => url.includes('/search') || url.includes('calendar'))).toBe(false)
  })

  it('does not automatically search again when authentication changes later', async () => {
    const { authStore } = await mountRecommendations()

    authStore.user = null
    await settle()

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
