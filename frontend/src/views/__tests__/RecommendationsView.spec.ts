import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { User } from 'firebase/auth'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, type LocationQueryRaw } from 'vue-router'

import { i18n, setLocale } from '../../i18n'
import { useAuthStore } from '../../stores/authStore'
import RecommendationsView from '../RecommendationsView.vue'

vi.mock('@/firebase', () => ({ auth: {} }))

const STREAM_URL = '/api/v1/recommendations/stream'

const bestNowCard = {
  id: 'card-1',
  bank_name: '玉山銀行',
  name: 'Unicard',
}

const waitCard = {
  id: 'card-2',
  bank_name: '台新銀行',
  name: '@GoGo 卡',
}

const recommendation = {
  mode: 'no_registration',
  best_now: {
    card: bestNowCard,
    sale_id: 'esun-unicard-2026',
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
  fetchMock.mockReset()
  fetchMock.mockImplementation(() => Promise.resolve(okResponse(chunked(fullStream))))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount()
  vi.unstubAllGlobals()
})

describe('RecommendationsView', () => {
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
    })
    expect(JSON.parse(init.body as string)).not.toHaveProperty('include_unowned')
  })

  it('cancels and repeats a valid search when the locale changes', async () => {
    const first = controlledStream()
    fetchMock
      .mockImplementationOnce(() => Promise.resolve(okResponse(first.body)))
      .mockImplementationOnce(() => Promise.resolve(okResponse(chunked(fullStream))))

    const { wrapper } = await mountRecommendations()
    expect(fetchMock).toHaveBeenCalledOnce()

    setLocale('en-US')
    await settle()

    expect(first.isCancelled()).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(JSON.parse(requestInit(1).body as string)).toMatchObject({ locale: 'en-US' })
    expect(wrapper.get('#ranking-title').text()).toBe('Best Card Right Now')
    expect(wrapper.get('[data-testid="best-now"]').text()).toContain('NT$224.70')
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
    expect(draft.find('button').exists()).toBe(false)
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
