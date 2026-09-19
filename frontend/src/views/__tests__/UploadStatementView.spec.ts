import { beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount } from '@vue/test-utils'
import UploadStatementView from '../UploadStatementView.vue'

const apiMocks = vi.hoisted(() => ({
  post: vi.fn<(url: string, data?: unknown) => Promise<unknown>>(),
}))

vi.mock('@/services/api', () => ({ api: apiMocks }))

function mountUploadStatement() {
  return mount(UploadStatementView, {
    global: {
      stubs: {
        SiteHeader: true,
      },
    },
  })
}

beforeEach(() => {
  apiMocks.post.mockReset()
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
})
