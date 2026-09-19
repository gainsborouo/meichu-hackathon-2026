import { beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount } from '@vue/test-utils'
import HomeView from '../HomeView.vue'

type MockAuthUser = {
  displayName: string | null
  email: string | null
  photoURL: string | null
}

const authMocks = vi.hoisted(() => ({
  auth: {},
  currentUser: null as unknown,
  googleProvider: {},
  signInWithPopup: vi.fn<(auth: unknown, provider: unknown) => Promise<unknown>>(),
  signOut: vi.fn<(auth: unknown) => Promise<void>>(),
  unsubscribe: vi.fn<() => void>(),
}))

vi.mock('@/firebase', () => ({
  auth: authMocks.auth,
  googleProvider: authMocks.googleProvider,
}))

vi.mock('firebase/auth', () => ({
  onAuthStateChanged: vi.fn<(auth: unknown, onUser: (user: unknown) => void) => () => void>(
    (_auth, onUser) => {
      onUser(authMocks.currentUser)
      return authMocks.unsubscribe
    },
  ),
  signInWithPopup: authMocks.signInWithPopup,
  signOut: authMocks.signOut,
}))

function mountHome(user: MockAuthUser | null = null) {
  authMocks.currentUser = user
  return mount(HomeView)
}

beforeEach(() => {
  vi.clearAllMocks()
  authMocks.currentUser = null
  authMocks.signInWithPopup.mockResolvedValue({})
  authMocks.signOut.mockResolvedValue(undefined)
})

describe('HomeView', () => {
  it('renders the credit card search form', () => {
    const wrapper = mountHome()

    expect(wrapper.get('h1').text()).toBe('這筆消費，該刷哪張卡？')
    expect(wrapper.get('label[for="location"]').text()).toContain('消費地點')
    expect(wrapper.get('label[for="amount"]').text()).toContain('金額（以新臺幣計算）')
    expect(wrapper.get('label[for="category"]').text()).toContain('品項或類別')
    expect(wrapper.text()).not.toContain('店家、品類或用途都可以作為查詢情境。')
    expect(wrapper.get('footer').text()).toBe('© 2026 Meichu Hackathon @ Google')
    expect(
      wrapper.get('button[aria-label="使用 Google 帳號登入"]').attributes('disabled'),
    ).toBeUndefined()
    expect(
      wrapper
        .get('button[aria-label="使用 Google 帳號登入"]')
        .find('.topbar__google-icon')
        .exists(),
    ).toBe(true)
    expect(wrapper.get('button[type="submit"]').text()).toContain('搜尋信用卡推薦')
  })

  it('signs in with Google from the existing login button', async () => {
    const wrapper = mountHome()

    await wrapper.get('button[aria-label="使用 Google 帳號登入"]').trigger('click')
    await flushPromises()

    expect(authMocks.signInWithPopup).toHaveBeenCalledExactlyOnceWith(
      authMocks.auth,
      authMocks.googleProvider,
    )
  })

  it('shows the Google profile and signs out', async () => {
    const wrapper = mountHome({
      displayName: '王小明',
      email: 'user@example.com',
      photoURL: 'https://example.com/avatar.png',
    })

    expect(wrapper.get('.topbar__identity-name').text()).toBe('王小明')
    expect(wrapper.get<HTMLImageElement>('.topbar__avatar').attributes('src')).toBe(
      'https://example.com/avatar.png',
    )

    await wrapper.get('.topbar__login').trigger('click')
    await flushPromises()

    expect(authMocks.signOut).toHaveBeenCalledExactlyOnceWith(authMocks.auth)
  })

  it('uses profile fallbacks when Google omits the name or avatar', () => {
    const wrapper = mountHome({
      displayName: null,
      email: 'user@example.com',
      photoURL: null,
    })

    expect(wrapper.get('.topbar__identity-name').text()).toBe('user@example.com')
    expect(wrapper.find('img.topbar__avatar').exists()).toBe(false)
    expect(wrapper.find('.topbar__avatar--fallback').exists()).toBe(true)
  })

  it('shows an accessible error when the login popup is blocked', async () => {
    authMocks.signInWithPopup.mockRejectedValue({ code: 'auth/popup-blocked' })
    const wrapper = mountHome()

    await wrapper.get('button[aria-label="使用 Google 帳號登入"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('.topbar__auth-error').attributes('role')).toBe('alert')
    expect(wrapper.get('.topbar__auth-error').text()).toBe(
      '瀏覽器阻擋登入視窗，請允許彈出式視窗後再試。',
    )
  })

  it('does not show an error when the user closes the login popup', async () => {
    authMocks.signInWithPopup.mockRejectedValue({ code: 'auth/popup-closed-by-user' })
    const wrapper = mountHome()

    await wrapper.get('button[aria-label="使用 Google 帳號登入"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('.topbar__auth-error').exists()).toBe(false)
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
