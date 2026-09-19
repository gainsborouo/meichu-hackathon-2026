import { beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount } from '@vue/test-utils'
import CardManagementView from '../CardManagementView.vue'

const apiMocks = vi.hoisted(() => ({
  delete: vi.fn<(url: string) => Promise<unknown>>(),
  get: vi.fn<(url: string) => Promise<unknown>>(),
  post: vi.fn<(url: string, data?: unknown) => Promise<unknown>>(),
}))

vi.mock('@/services/api', () => ({ api: apiMocks }))

const catalogCards = [
  {
    id: '11111111-1111-4111-8111-111111111111',
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
  {
    id: '22222222-2222-4222-8222-222222222222',
    bank_name: '台北富邦銀行',
    name: 'momo 卡',
    artwork_id: 'fubon-momo',
    display_name: '台北富邦銀行｜momo 卡',
    issuer_en: 'Taipei Fubon Bank',
    variant: null,
    network: null,
    tier: null,
    official_image_url: 'https://example.com/momo.png',
    image_is_composite: false,
  },
]
const ownedCard = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  card: catalogCards[0]!,
  created_at: '2026-09-19T00:00:00Z',
}

function mockInitialRequests(userCards: unknown[] = []) {
  apiMocks.get.mockImplementation((url) => {
    if (url === '/cards') return Promise.resolve({ data: catalogCards })
    if (url === '/me/cards') return Promise.resolve({ data: userCards })
    return Promise.reject(new Error(`Unexpected URL: ${url}`))
  })
}

function mountCardManagement() {
  return mount(CardManagementView, {
    global: {
      stubs: {
        SiteHeader: true,
      },
    },
  })
}

beforeEach(() => {
  apiMocks.delete.mockReset()
  apiMocks.get.mockReset()
  apiMocks.post.mockReset()
  mockInitialRequests()
})

describe('CardManagementView', () => {
  it('從 API 讀取卡片目錄與持卡資料，載入期間不顯示空卡包', async () => {
    mockInitialRequests([ownedCard])

    const wrapper = mountCardManagement()

    expect(wrapper.find('.wallet-empty').exists()).toBe(false)
    expect(wrapper.get('.wallet-loading').text()).toContain('正在讀取持卡資料')

    await flushPromises()

    expect(apiMocks.get).toHaveBeenCalledWith('/cards')
    expect(apiMocks.get).toHaveBeenCalledWith('/me/cards')
    expect(wrapper.get('h1').text()).toBe('卡片管理')
    expect(wrapper.get('.wallet-card').text()).toContain(catalogCards[0]!.name)
    expect(wrapper.findAll('.catalogue-card')).toHaveLength(2)
    expect(wrapper.get('.catalogue-card__image img').attributes('src')).toBe(
      `/card-art/${catalogCards[0]!.artwork_id}.webp`,
    )
  })

  it('初始讀取失敗時顯示錯誤，且不顯示空卡包', async () => {
    apiMocks.get.mockRejectedValue(new Error('Request failed'))

    const wrapper = mountCardManagement()
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('無法讀取卡片資料')
    expect(wrapper.find('.wallet-empty').exists()).toBe(false)
    expect(wrapper.find('.catalogue-empty').exists()).toBe(false)
  })

  it('卡面載入失敗時顯示替代內容', async () => {
    const wrapper = mountCardManagement()
    await flushPromises()

    await wrapper.get('.catalogue-card__image img').trigger('error')

    expect(wrapper.get('.catalogue-card__image .card-art-fallback').text()).toContain(
      '卡面圖片無法顯示',
    )
  })

  it('使用後端卡片 UUID 新增卡片，並保存回傳的持卡 UUID', async () => {
    const createdUserCard = {
      id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      card: catalogCards[0]!,
      created_at: '2026-09-19T01:00:00Z',
    }
    apiMocks.post.mockResolvedValue({ data: createdUserCard })
    apiMocks.delete.mockResolvedValue({})
    const wrapper = mountCardManagement()
    await flushPromises()

    await wrapper.get('.catalogue-card__action').trigger('click')
    await flushPromises()

    expect(apiMocks.post).toHaveBeenCalledExactlyOnceWith('/me/cards', {
      card_id: catalogCards[0]!.id,
    })
    expect(wrapper.get('.wallet-card').text()).toContain(catalogCards[0]!.name)

    await wrapper.get('.wallet-remove').trigger('click')
    await flushPromises()

    expect(apiMocks.delete).toHaveBeenCalledExactlyOnceWith(`/me/cards/${createdUserCard.id}`)
  })

  it('使用持卡 UUID 移除既有卡片', async () => {
    mockInitialRequests([ownedCard])
    apiMocks.delete.mockResolvedValue({})
    const wrapper = mountCardManagement()
    await flushPromises()

    await wrapper.get('.wallet-remove').trigger('click')
    await flushPromises()

    expect(apiMocks.delete).toHaveBeenCalledExactlyOnceWith(`/me/cards/${ownedCard.id}`)
    expect(wrapper.find('.wallet-card').exists()).toBe(false)
    expect(wrapper.get('.wallet-empty').text()).toContain('尚未加入信用卡')
  })

  it('新增請求失敗時保留原本的卡包', async () => {
    apiMocks.post.mockRejectedValue(new Error('Request failed'))
    const wrapper = mountCardManagement()
    await flushPromises()

    await wrapper.get('.catalogue-card__action').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('持卡資料未變更')
    expect(wrapper.find('.wallet-card').exists()).toBe(false)
    expect(wrapper.get('.catalogue-card__action').attributes('data-state')).toBe('error')
  })

  it('移除請求失敗時保留卡片', async () => {
    mockInitialRequests([ownedCard])
    apiMocks.delete.mockRejectedValue(new Error('Request failed'))
    const wrapper = mountCardManagement()
    await flushPromises()

    await wrapper.get('.wallet-remove').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('持卡資料未變更')
    expect(wrapper.get('.wallet-card').text()).toContain(catalogCards[0]!.name)
    expect(wrapper.get('.wallet-remove').attributes('data-state')).toBe('error')
  })
})
