import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount } from '@vue/test-utils'
import { creditCardArtworkCatalog } from '../../data/creditCardArtwork'
import CardManagementView from '../CardManagementView.vue'

const fetchMock = vi.fn<typeof fetch>()
const firstCard = creditCardArtworkCatalog[0]!

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
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('CardManagementView', () => {
  it('renders the card catalogue and an empty wallet', () => {
    const wrapper = mountCardManagement()

    expect(wrapper.get('h1').text()).toBe('卡片管理')
    expect(wrapper.get('label[for="card-search"]').text()).toBe('搜尋信用卡')
    expect(wrapper.get('.wallet-empty').text()).toContain('尚未加入信用卡')
    expect(wrapper.findAll('.catalogue-card').length).toBeGreaterThan(0)
    expect(wrapper.get('.catalogue-card__image img').attributes('src')).toBe(
      `/card-art/${firstCard.id}.webp`,
    )
    expect(wrapper.text()).not.toContain(firstCard.network)
    expect(wrapper.text()).not.toContain(firstCard.tier)
  })

  it('adds a card after the API accepts the request', async () => {
    fetchMock.mockResolvedValue({ ok: true } as Response)
    const wrapper = mountCardManagement()

    await wrapper.get('.catalogue-card__action').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledExactlyOnceWith('/api/v1/mine/cards', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify([{ issuer: firstCard.issuer, name: firstCard.cardName }]),
    })
    expect(wrapper.get('.wallet-card').text()).toContain(firstCard.cardName)
    expect(wrapper.get('.catalogue-card__action').attributes('data-state')).toBe('success')
  })

  it('removes a card after the API accepts the request', async () => {
    fetchMock.mockResolvedValue({ ok: true } as Response)
    const wrapper = mountCardManagement()

    await wrapper.get('.catalogue-card__action').trigger('click')
    await flushPromises()
    await wrapper.get('.wallet-remove').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/mine/cards', {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify([{ issuer: firstCard.issuer, name: firstCard.cardName }]),
    })
    expect(wrapper.find('.wallet-card').exists()).toBe(false)
    expect(wrapper.get('.wallet-empty').text()).toContain('尚未加入信用卡')
  })

  it('keeps the wallet unchanged when the backend rejects an add request', async () => {
    fetchMock.mockResolvedValue({ ok: false } as Response)
    const wrapper = mountCardManagement()

    await wrapper.get('.catalogue-card__action').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('持卡資料未變更')
    expect(wrapper.find('.wallet-card').exists()).toBe(false)
    expect(wrapper.get('.catalogue-card__action').attributes('data-state')).toBe('error')
  })

  it('keeps a card in the wallet when the backend rejects its removal', async () => {
    fetchMock
      .mockResolvedValueOnce({ ok: true } as Response)
      .mockResolvedValueOnce({ ok: false } as Response)
    const wrapper = mountCardManagement()

    await wrapper.get('.catalogue-card__action').trigger('click')
    await flushPromises()
    await wrapper.get('.wallet-remove').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('持卡資料未變更')
    expect(wrapper.get('.wallet-card').text()).toContain(firstCard.cardName)
    expect(wrapper.get('.wallet-remove').attributes('data-state')).toBe('error')
  })
})
