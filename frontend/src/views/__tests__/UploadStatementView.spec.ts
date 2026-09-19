import { beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount } from '@vue/test-utils'
import type { User } from 'firebase/auth'
import { createPinia } from 'pinia'
import { useAuthStore } from '../../stores/authStore'
import UploadStatementView from '../UploadStatementView.vue'

const apiMocks = vi.hoisted(() => ({
  get: vi.fn<(url: string, config?: unknown) => Promise<{ data: unknown }>>(),
  post: vi.fn<(url: string, data?: unknown) => Promise<unknown>>(),
}))

vi.mock('@/services/api', () => ({ api: apiMocks }))

const signedInUser = { uid: 'user-1' } as User

function mountUploadStatement(user: User | null = signedInUser, authReady = true) {
  const pinia = createPinia()
  const authStore = useAuthStore(pinia)
  authStore.user = user
  authStore.ready = authReady

  return mount(UploadStatementView, {
    global: {
      plugins: [pinia],
      stubs: {
        SiteHeader: true,
      },
    },
  })
}

beforeEach(() => {
  apiMocks.get.mockReset()
  apiMocks.post.mockReset()
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: {
      configurable: true,
      value: vi.fn<(this: HTMLDialogElement) => void>(function (this: HTMLDialogElement) {
        this.setAttribute('open', '')
      }),
    },
    close: {
      configurable: true,
      value: vi.fn<(this: HTMLDialogElement) => void>(function (this: HTMLDialogElement) {
        this.removeAttribute('open')
      }),
    },
  })
})

describe('UploadStatementView', () => {
  it('renders a file upload form', () => {
    const wrapper = mountUploadStatement()

    expect(wrapper.get('h1').text()).toBe('上傳帳單')
    expect(wrapper.get<HTMLInputElement>('#statement-file').attributes('type')).toBe('file')
    expect(wrapper.get<HTMLInputElement>('#statement-file').attributes('multiple')).toBeDefined()
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('uploads all selected files in one multipart request', async () => {
    apiMocks.post.mockResolvedValue({})
    const wrapper = mountUploadStatement()
    const input = wrapper.get<HTMLInputElement>('#statement-file')
    const files = [
      new File(['first'], 'first.pdf', { type: 'application/pdf' }),
      new File(['second'], 'second.pdf', { type: 'application/pdf' }),
    ]

    Object.defineProperty(input.element, 'files', {
      configurable: true,
      value: files,
    })
    await input.trigger('change')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('first.pdf、second.pdf')
    expect(apiMocks.post).toHaveBeenCalledTimes(1)
    const [url, body] = apiMocks.post.mock.calls[0]!
    expect(url).toBe('/me/statements')
    expect((body as FormData).getAll('files')).toEqual(files)
    expect((body as FormData).has('file')).toBe(false)
    expect(wrapper.get('[role="status"]').text()).toBe('已上傳 2 份帳單。')
  })

  it('shows an error when the upload request fails', async () => {
    apiMocks.post.mockRejectedValue(new Error('Request failed'))
    const wrapper = mountUploadStatement()
    const input = wrapper.get<HTMLInputElement>('#statement-file')
    const file = new File(['statement'], 'statement.pdf', { type: 'application/pdf' })

    Object.defineProperty(input.element, 'files', {
      configurable: true,
      value: [file],
    })
    await input.trigger('change')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toBe('上傳失敗，請稍後再試。')
  })

  it('disables the analysis button until the user is signed in', () => {
    const loadingAuth = mountUploadStatement(null, false)
    const signedOut = mountUploadStatement(null)

    expect(loadingAuth.get('.analysis-trigger').text()).toContain('確認登入狀態…')
    expect(loadingAuth.get('.analysis-trigger').attributes('disabled')).toBeDefined()
    expect(signedOut.get('.analysis-trigger').text()).toContain('登入後查看')
    expect(signedOut.get('.analysis-trigger').attributes('disabled')).toBeDefined()
  })

  it('loads and safely renders the Markdown analysis before opening the dialog', async () => {
    let resolveRequest!: (value: { data: string }) => void
    apiMocks.get.mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve
      }),
    )
    const wrapper = mountUploadStatement()
    const button = wrapper.get('.analysis-trigger')

    await button.trigger('click')

    expect(button.text()).toContain('載入中')
    expect(button.attributes('disabled')).toBeDefined()
    expect(wrapper.get('dialog').attributes('open')).toBeUndefined()

    resolveRequest({
      data: [
        '# 八月消費',
        '',
        '| 類別 | 金額 |',
        '| --- | ---: |',
        '| 餐飲 | 1,200 |',
        '',
        '[資料來源](https://example.com/report)',
        '',
        '<script>alert("unsafe")</script>',
        '',
        '![追蹤圖片](https://example.com/tracker.png)',
        '',
        '[危險連結](javascript:alert(1))',
      ].join('\n'),
    })
    await flushPromises()

    expect(apiMocks.get).toHaveBeenCalledWith('/me/statements', {
      signal: expect.any(AbortSignal),
    })
    expect(wrapper.get('dialog').attributes('open')).toBeDefined()
    expect(wrapper.get('.analysis-markdown h1').text()).toBe('八月消費')
    expect(wrapper.get('.analysis-markdown table').text()).toContain('餐飲')
    expect(wrapper.find('.analysis-markdown script').exists()).toBe(false)
    expect(wrapper.find('.analysis-markdown img').exists()).toBe(false)
    expect(wrapper.get('.analysis-markdown').text()).toContain('追蹤圖片')

    const links = wrapper.findAll<HTMLAnchorElement>('.analysis-markdown a')
    expect(links).toHaveLength(1)
    expect(links[0]!.attributes('target')).toBe('_blank')
    expect(links[0]!.attributes('rel')).toBe('noopener noreferrer')
  })

  it('shows the empty state for a blank response', async () => {
    apiMocks.get.mockResolvedValue({ data: '  \n' })
    const wrapper = mountUploadStatement()

    await wrapper.get('.analysis-trigger').trigger('click')
    await flushPromises()

    expect(wrapper.get('dialog').attributes('open')).toBeDefined()
    expect(wrapper.get('.analysis-empty').text()).toBe('尚無統計數據，請先上傳帳單。')
  })

  it('keeps request errors on the page and retries on the next click', async () => {
    apiMocks.get
      .mockRejectedValueOnce(new Error('Request failed'))
      .mockResolvedValueOnce({ data: '# 已重新載入' })
    const wrapper = mountUploadStatement()

    await wrapper.get('.analysis-trigger').trigger('click')
    await flushPromises()

    expect(wrapper.get('.analysis-entry__error').text()).toBe('無法取得帳單分析，請稍後再試。')
    expect(wrapper.get('dialog').attributes('open')).toBeUndefined()

    await wrapper.get('.analysis-trigger').trigger('click')
    await flushPromises()

    expect(apiMocks.get).toHaveBeenCalledTimes(2)
    expect(wrapper.get('dialog').attributes('open')).toBeDefined()
    expect(wrapper.find('.analysis-entry__error').exists()).toBe(false)
  })

  it('supports every close action and fetches again when reopened', async () => {
    apiMocks.get.mockResolvedValue({ data: '# 帳單分析' })
    const wrapper = mountUploadStatement()

    await wrapper.get('.analysis-trigger').trigger('click')
    await flushPromises()
    await wrapper.get('button[aria-label="關閉帳單分析"]').trigger('click')

    expect(wrapper.get('dialog').attributes('open')).toBeUndefined()

    await wrapper.get('.analysis-trigger').trigger('click')
    await flushPromises()
    await wrapper.get('dialog').trigger('cancel')

    expect(wrapper.get('dialog').attributes('open')).toBeUndefined()

    await wrapper.get('.analysis-trigger').trigger('click')
    await flushPromises()
    await wrapper.get('dialog').trigger('click')

    expect(wrapper.get('dialog').attributes('open')).toBeUndefined()

    await wrapper.get('.analysis-trigger').trigger('click')
    await flushPromises()

    expect(apiMocks.get).toHaveBeenCalledTimes(4)
    expect(wrapper.get('dialog').attributes('open')).toBeDefined()
  })
})
