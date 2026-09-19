import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { User } from 'firebase/auth'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, type LocationQueryRaw } from 'vue-router'

import { useAuthStore } from '../../stores/authStore'
import RecommendationsView from '../RecommendationsView.vue'

const apiMocks = vi.hoisted(() => ({
  post: vi.fn<(url: string, data: unknown, config: { signal: AbortSignal }) => Promise<unknown>>(),
}))

vi.mock('@/firebase', () => ({ auth: {} }))
vi.mock('@/services/api', () => ({ api: { post: apiMocks.post } }))

const searchResponse = {
  query: {
    price: 1000,
    platform: '全聯',
    category: '日常消費',
    currency: 'TWD',
    include_unowned: false,
  },
  resolved_category: '超市',
  best: {
    user_card_id: 'user-card-1',
    owned: true,
    card: {
      id: 'card-1',
      bank_name: '中國信託銀行',
      name: '中國信託 LINE Pay 信用卡',
      artwork_id: 'ctbc-linepay-ve8710',
      display_name: '中國信託銀行｜中國信託 LINE Pay 信用卡（VE8710）',
      issuer_en: 'CTBC Bank',
      variant: 'VE8710',
      network: 'VISA',
      tier: 'Signature',
      official_image_url: 'https://example.com/ctbc.png',
      image_is_composite: false,
    },
    estimated_reward: {
      amount: 30,
      rate: 0.03,
      rate_max: 0.05,
      currency: 'TWD',
      unit: '現金回饋',
      capped: true,
      requires_registration: true,
      source_text: '符合指定通路加碼資格。',
    },
    matched_sales: [
      {
        id: 'sale-1',
        card_id: 'card-1',
        bank_name: '中國信託銀行',
        card_name: '中國信託 LINE Pay 信用卡',
        title: '指定通路加碼',
        reward: '最高 5%',
        conditions: '每月回饋上限 300 元。',
        campaign_period: '2026/01/01–2026/12/31',
        register_url: 'https://example.com/register',
        source_url: 'https://example.com/source',
        evidence: '活動辦法第 3 條。',
      },
    ],
    reason: '此卡在指定通路提供較高回饋。',
  },
  alternatives: [
    {
      user_card_id: 'user-card-2',
      owned: false,
      card: {
        id: 'card-2',
        bank_name: '測試銀行',
        name: '測試信用卡',
        artwork_id: null,
        display_name: null,
        issuer_en: null,
        variant: null,
        network: null,
        tier: null,
        official_image_url: null,
        image_is_composite: null,
      },
      estimated_reward: {
        amount: 20,
        rate: 0.02,
        rate_max: 0.02,
        currency: 'TWD',
        unit: '現金回饋',
        capped: false,
        requires_registration: false,
        source_text: '',
      },
      matched_sales: [],
      reason: '一般消費提供固定回饋。',
    },
  ],
  considered_card_count: 2,
}

const validQuery = {
  platform: '全聯',
  price: '1000',
  category: '日常消費',
}

const mountedWrappers: VueWrapper[] = []

async function mountRecommendations({
  query = validQuery,
  ready = true,
  user = { uid: 'user-1' } as User,
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
      plugins: [pinia, router],
      stubs: { SiteHeader: true },
    },
  })
  mountedWrappers.push(wrapper)

  return { authStore, router, wrapper }
}

beforeEach(() => {
  apiMocks.post.mockReset()
  apiMocks.post.mockResolvedValue({ data: searchResponse })
})

afterEach(() => {
  for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount()
})

describe('RecommendationsView', () => {
  it('shows loading and renders best plus alternatives in API order', async () => {
    let resolveRequest!: (value: { data: typeof searchResponse }) => void
    apiMocks.post.mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve
      }),
    )

    const { wrapper } = await mountRecommendations()

    expect(wrapper.get('[role="status"]').text()).toContain('正在計算信用卡推薦')
    expect(wrapper.find('.recommendation-card').exists()).toBe(false)
    expect(apiMocks.post).toHaveBeenCalledExactlyOnceWith(
      '/search',
      {
        price: 1000,
        platform: '全聯',
        category: '日常消費',
        currency: 'TWD',
        include_unowned: false,
      },
      { signal: expect.any(AbortSignal) },
    )

    resolveRequest({ data: searchResponse })
    await flushPromises()

    const cards = wrapper.findAll('.recommendation-card')
    expect(cards).toHaveLength(2)
    expect(cards[0]!.text()).toContain('第 1 名')
    expect(cards[0]!.text()).toContain(searchResponse.best.card.name)
    expect(cards[0]!.text()).toContain('3%–5%')
    expect(cards[0]!.get('img').attributes('src')).toBe(
      `/card-art/${searchResponse.best.card.artwork_id}.webp`,
    )
    expect(cards[1]!.text()).toContain('第 2 名')
    expect(cards[1]!.find('.card-art__fallback').exists()).toBe(true)
    expect(wrapper.get('details').text()).toContain('指定通路加碼')
    expect(wrapper.get('a[href="https://example.com/register"]').attributes('rel')).toBe(
      'noopener noreferrer',
    )
  })

  it('waits for auth initialization and includes unowned cards for anonymous searches', async () => {
    const { authStore, wrapper } = await mountRecommendations({ ready: false, user: null })

    expect(wrapper.get('[role="status"]').text()).toContain('正在計算信用卡推薦')
    expect(apiMocks.post).not.toHaveBeenCalled()

    authStore.ready = true
    await flushPromises()

    expect(apiMocks.post).toHaveBeenCalledExactlyOnceWith(
      '/search',
      expect.objectContaining({ include_unowned: true }),
      { signal: expect.any(AbortSignal) },
    )
  })

  it('keeps invalid URL values editable without sending a request', async () => {
    const { wrapper } = await mountRecommendations({
      query: { platform: '', price: '0', category: '' },
    })

    await flushPromises()

    expect(apiMocks.post).not.toHaveBeenCalled()
    expect(wrapper.findAll('[role="alert"]').map((message) => message.text())).toEqual([
      '請輸入消費地點',
      '請輸入有效且大於 0 的消費金額',
      '請輸入品項或類別',
    ])
    expect(wrapper.get<HTMLInputElement>('#recommendation-price').element.value).toBe('0')
  })

  it('keeps the query and retries after a request failure', async () => {
    apiMocks.post.mockRejectedValueOnce(new Error('Request failed'))
    const { wrapper } = await mountRecommendations()
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toBe('無法取得信用卡推薦，請稍後再試。')

    await wrapper.get('.state-panel--error button').trigger('click')
    await flushPromises()

    expect(apiMocks.post).toHaveBeenCalledTimes(2)
    expect(wrapper.findAll('.recommendation-card')).toHaveLength(2)
  })

  it('pushes changed queries, hides stale results, and cancels superseded requests', async () => {
    const { router, wrapper } = await mountRecommendations()
    await flushPromises()

    let resolveSecond!: (value: { data: typeof searchResponse }) => void
    apiMocks.post.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSecond = resolve
      }),
    )

    await wrapper.get<HTMLInputElement>('#recommendation-platform').setValue('蝦皮')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(router.currentRoute.value.query.platform).toBe('蝦皮')
    expect(wrapper.find('.recommendation-card').exists()).toBe(false)
    expect(wrapper.get('[role="status"]').text()).toContain('正在計算信用卡推薦')

    const secondSignal = apiMocks.post.mock.calls[1]![2].signal as AbortSignal
    await wrapper.get<HTMLInputElement>('#recommendation-platform').setValue('東京')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(secondSignal.aborted).toBe(true)
    expect(router.currentRoute.value.query.platform).toBe('東京')
    expect(apiMocks.post).toHaveBeenCalledTimes(3)

    resolveSecond({ data: searchResponse })
  })

  it('does not automatically search again when authentication changes later', async () => {
    const { authStore } = await mountRecommendations()
    await flushPromises()

    authStore.user = null
    await flushPromises()

    expect(apiMocks.post).toHaveBeenCalledTimes(1)
  })
})
