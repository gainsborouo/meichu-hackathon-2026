import { describe, expect, it } from 'vitest'

import { mount } from '@vue/test-utils'
import HomeView from '../HomeView.vue'

function mountHome() {
  return mount(HomeView)
}

describe('HomeView', () => {
  it('renders the credit card search form', () => {
    const wrapper = mountHome()

    expect(wrapper.get('h1').text()).toBe('這筆消費，該刷哪張卡？')
    expect(wrapper.get('label[for="location"]').text()).toContain('消費地點')
    expect(wrapper.get('label[for="amount"]').text()).toContain('金額（以新臺幣計算）')
    expect(wrapper.get('label[for="category"]').text()).toContain('品項或類別')
    expect(wrapper.text()).not.toContain('店家、品類或用途都可以作為查詢情境。')
    expect(wrapper.get('footer').text()).toBe('© 2026 Meichu Hackathon @ Google')
    expect(wrapper.get('button[aria-label="登入功能尚未開放"]').attributes()).toHaveProperty(
      'disabled',
    )
    expect(wrapper.get('button[type="submit"]').text()).toContain('搜尋信用卡推薦')
  })

  it('validates the required fields', async () => {
    const wrapper = mountHome()

    await wrapper.get('form').trigger('submit')

    expect(wrapper.findAll('[role="alert"]').map((error) => error.text())).toEqual([
      '請輸入消費地點',
      '請輸入大於 0 的消費金額',
      '請輸入品項或類別',
    ])
    expect(wrapper.get('#location').attributes('aria-invalid')).toBe('true')
    expect(wrapper.get('#amount').attributes('aria-invalid')).toBe('true')
    expect(wrapper.get('#category').attributes('aria-invalid')).toBe('true')
  })

  it('formats the amount and strips non-numeric characters', async () => {
    const wrapper = mountHome()
    const amountInput = wrapper.get<HTMLInputElement>('#amount')

    await amountInput.setValue('NT$ 010,000')

    expect(amountInput.element.value).toBe('10,000')
  })

  it('rejects an amount of zero', async () => {
    const wrapper = mountHome()

    await wrapper.get<HTMLInputElement>('#location').setValue('線上平台')
    await wrapper.get<HTMLInputElement>('#amount').setValue('0')
    await wrapper.get<HTMLInputElement>('#category').setValue('影音娛樂')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.get('[role="alert"]').text()).toBe('請輸入大於 0 的消費金額')
  })

  it('clears all inputs', async () => {
    const wrapper = mountHome()
    const locationInput = wrapper.get<HTMLInputElement>('#location')
    const amountInput = wrapper.get<HTMLInputElement>('#amount')
    const categoryInput = wrapper.get<HTMLInputElement>('#category')

    await locationInput.setValue('線上平台')
    await amountInput.setValue('10000')
    await categoryInput.setValue('影音娛樂')
    await wrapper.get('button[aria-label="清除消費地點"]').trigger('click')
    await wrapper.get('button[aria-label="清除消費金額"]').trigger('click')
    await wrapper.get('button[aria-label="清除品項或類別"]').trigger('click')

    expect(locationInput.element.value).toBe('')
    expect(amountInput.element.value).toBe('')
    expect(categoryInput.element.value).toBe('')
  })

  it('keeps valid values after submission without showing errors', async () => {
    const wrapper = mountHome()
    const locationInput = wrapper.get<HTMLInputElement>('#location')
    const amountInput = wrapper.get<HTMLInputElement>('#amount')
    const categoryInput = wrapper.get<HTMLInputElement>('#category')

    await locationInput.setValue('線上平台')
    await amountInput.setValue('10000')
    await categoryInput.setValue('影音娛樂')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(locationInput.element.value).toBe('線上平台')
    expect(amountInput.element.value).toBe('10,000')
    expect(categoryInput.element.value).toBe('影音娛樂')
  })
})
