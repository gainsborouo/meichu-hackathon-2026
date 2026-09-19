import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { flushPromises, mount } from '@vue/test-utils'
import UploadStatementView from '../UploadStatementView.vue'

const fetchMock = vi.fn<typeof fetch>()

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
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('UploadStatementView', () => {
  it('renders a file upload form', () => {
    const wrapper = mountUploadStatement()

    expect(wrapper.get('h1').text()).toBe('上傳帳單')
    expect(wrapper.get<HTMLInputElement>('#statement-file').attributes('type')).toBe('file')
    expect(wrapper.get<HTMLInputElement>('#statement-file').attributes('multiple')).toBeDefined()
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('uploads every selected file as multipart form data', async () => {
    fetchMock.mockResolvedValue({ ok: true } as Response)
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
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(
      fetchMock.mock.calls.map(([url, request]) => ({
        url,
        method: request!.method,
        file: (request!.body as FormData).get('file'),
      })),
    ).toEqual([
      { url: '/api/v1/mine/e-statement', method: 'POST', file: files[0] },
      { url: '/api/v1/mine/e-statement', method: 'POST', file: files[1] },
    ])
    expect(wrapper.get('[role="status"]').text()).toBe('已上傳 2 份帳單。')
  })

  it('shows an error when the upload request fails', async () => {
    fetchMock.mockResolvedValue({ ok: false } as Response)
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
