import { beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount } from '@vue/test-utils'
import type { User } from 'firebase/auth'
import { createPinia } from 'pinia'
import { i18n, LOCALE_STORAGE_KEY, setLocale } from '../../i18n'
import { useAuthStore } from '../../stores/authStore'
import HomeView from '../HomeView.vue'

type MockAuthUser = {
  displayName: string | null
  email: string | null
  photoURL: string | null
}

const authMocks = vi.hoisted(() => ({
  auth: {},
  googleProvider: {},
  signInWithPopup: vi.fn<(auth: unknown, provider: unknown) => Promise<unknown>>(),
  signOut: vi.fn<(auth: unknown) => Promise<void>>(),
}))

const routerMocks = vi.hoisted(() => ({
  push: vi.fn<(location: unknown) => Promise<void>>(),
}))

const apiMocks = vi.hoisted(() => ({
  get: vi.fn<(url: string) => Promise<{ data: { registration_campaigns_enabled: boolean } }>>(),
  patch:
    vi.fn<
      (
        url: string,
        data: { registration_campaigns_enabled: boolean },
      ) => Promise<{ data: { registration_campaigns_enabled: boolean } }>
    >(),
}))

vi.mock('@/firebase', () => ({
  auth: authMocks.auth,
  googleProvider: authMocks.googleProvider,
}))

vi.mock('firebase/auth', () => ({
  signInWithPopup: authMocks.signInWithPopup,
  signOut: authMocks.signOut,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerMocks.push }),
}))

vi.mock('@/services/api', () => ({ api: apiMocks }))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })

  return { promise, resolve }
}

function mountHome(user: MockAuthUser | null = null) {
  const pinia = createPinia()
  const authStore = useAuthStore(pinia)
  authStore.user = user as User | null
  authStore.ready = true

  return mount(HomeView, {
    global: {
      plugins: [pinia, i18n],
    },
  })
}

beforeEach(() => {
  setLocale('zh-TW', false)
  vi.clearAllMocks()
  authMocks.signInWithPopup.mockResolvedValue({})
  authMocks.signOut.mockResolvedValue(undefined)
  routerMocks.push.mockResolvedValue(undefined)
  apiMocks.get.mockResolvedValue({ data: { registration_campaigns_enabled: false } })
  apiMocks.patch.mockImplementation(async (_url, data) => ({ data }))
})

describe('HomeView', () => {
  it('renders the credit card search form', () => {
    const wrapper = mountHome()

    expect(wrapper.get('h1').text()).toBe('這筆消費，該刷哪張卡？')
    expect(wrapper.get('label[for="location"]').text()).toContain('消費地點')
    expect(wrapper.get('label[for="amount"]').text()).toContain('金額（以新臺幣計算）')
    expect(wrapper.get('label[for="category"]').text()).toContain('品項或類別')
    expect(wrapper.get('label[for="registration-campaigns-toggle"]').text()).toBe(
      '是否已登錄信用卡活動',
    )
    expect(wrapper.get('#registration-campaigns-toggle').attributes('role')).toBe('switch')
    expect(wrapper.get('#registration-preference-message').text()).toBe('')
    expect(wrapper.get('label[for="home-web-search"]').text()).toBe('啟用網路搜尋')
    const webSearchToggle = wrapper.get('#home-web-search')
    expect(webSearchToggle.attributes()).toMatchObject({
      role: 'switch',
      'aria-checked': 'true',
    })
    expect(webSearchToggle.attributes('aria-describedby')).toBeUndefined()
    expect(wrapper.find('#home-web-search-hint').exists()).toBe(false)
    expect(wrapper.get('a[href="/upload-statement"]').text()).toContain('上傳帳單')
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

  it('switches the interface and existing validation messages without reloading', async () => {
    localStorage.removeItem(LOCALE_STORAGE_KEY)
    const wrapper = mountHome()

    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('[role="alert"]').text()).toBe('請輸入消費地點')

    await wrapper.get<HTMLSelectElement>('.topbar__language-select').setValue('en-US')

    expect(wrapper.get('h1').text()).toBe('Which card should you usefor this purchase?')
    expect(wrapper.get('[role="alert"]').text()).toBe('Enter a store or location')
    expect(wrapper.get('label[for="registration-campaigns-toggle"]').text()).toBe(
      'Registered for credit card campaigns',
    )
    expect(wrapper.get('label[for="home-web-search"]').text()).toBe('Enable web search')
    expect(wrapper.get('button[type="submit"]').text()).toContain('Find the Best Card')
    expect(document.documentElement.lang).toBe('en-US')
    expect(document.title).toBe('SwipeRight')
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en-US')
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
    expect(routerMocks.push).not.toHaveBeenCalled()
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

  it('navigates to recommendations with valid search values', async () => {
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
    expect(routerMocks.push).toHaveBeenCalledExactlyOnceWith({
      name: 'recommendations',
      query: {
        platform: '線上平台',
        price: '10000',
        category: '影音娛樂',
        webSearch: '1',
      },
    })
  })

  it('adds webSearch=0 to the route when web search is disabled', async () => {
    const wrapper = mountHome()
    const toggle = wrapper.get('#home-web-search')

    await wrapper.get<HTMLInputElement>('#location').setValue('線上平台')
    await wrapper.get<HTMLInputElement>('#amount').setValue('10000')
    await wrapper.get<HTMLInputElement>('#category').setValue('影音娛樂')
    await toggle.trigger('click')
    await wrapper.get('form').trigger('submit')

    expect(toggle.attributes('aria-checked')).toBe('false')
    expect(routerMocks.push).toHaveBeenCalledExactlyOnceWith({
      name: 'recommendations',
      query: {
        platform: '線上平台',
        price: '10000',
        category: '影音娛樂',
        webSearch: '0',
      },
    })
  })

  it('saves the registration setting before allowing the search form to submit', async () => {
    const update = deferred<{ data: { registration_campaigns_enabled: boolean } }>()
    apiMocks.patch.mockReturnValueOnce(update.promise)
    const wrapper = mountHome({
      displayName: '王小明',
      email: 'user@example.com',
      photoURL: null,
    })
    await flushPromises()

    expect(apiMocks.get).toHaveBeenCalledWith('/me')

    await wrapper.get<HTMLInputElement>('#location').setValue('線上平台')
    await wrapper.get<HTMLInputElement>('#amount').setValue('10000')
    await wrapper.get<HTMLInputElement>('#category').setValue('影音娛樂')
    await wrapper.get('.registration-switch__track').trigger('click')
    await flushPromises()

    expect(apiMocks.patch).toHaveBeenCalledWith('/me', {
      registration_campaigns_enabled: true,
    })
    expect(wrapper.get('.search-panel__submit').attributes('disabled')).toBeDefined()
    await wrapper.get('form').trigger('submit')
    expect(routerMocks.push).not.toHaveBeenCalled()

    update.resolve({ data: { registration_campaigns_enabled: true } })
    await flushPromises()
    await wrapper.get('form').trigger('submit')

    expect(routerMocks.push).toHaveBeenCalledOnce()
  })
})
