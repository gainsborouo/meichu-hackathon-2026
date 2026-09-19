import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { api } from '../api'

const firebaseMocks = vi.hoisted(() => ({
  getCurrentUserIdToken: vi.fn<() => Promise<string | null>>(),
}))

vi.mock('@/firebase', () => ({
  getCurrentUserIdToken: firebaseMocks.getCurrentUserIdToken,
}))

const adapter = vi.fn<(config: InternalAxiosRequestConfig) => Promise<AxiosResponse>>(
  async (config) => ({
    config,
    data: null,
    headers: {},
    status: 200,
    statusText: 'OK',
  }),
)

beforeEach(() => {
  vi.clearAllMocks()
  firebaseMocks.getCurrentUserIdToken.mockResolvedValue(null)
  api.defaults.adapter = adapter
})

describe('api request interceptor', () => {
  it('overwrites Authorization with the current Firebase ID token', async () => {
    firebaseMocks.getCurrentUserIdToken.mockResolvedValue('fresh-token')

    await api.get('/test', {
      headers: { Authorization: 'Bearer stale-token' },
    })

    const config = adapter.mock.calls[0]![0]
    expect(config.baseURL).toBe('/api/v1')
    expect(config.headers.get('Authorization')).toBe('Bearer fresh-token')
  })

  it('sends unauthenticated requests without Authorization', async () => {
    await api.get('/test', {
      headers: { Authorization: 'Bearer stale-token' },
    })

    const config = adapter.mock.calls[0]![0]
    expect(config.headers.has('Authorization')).toBe(false)
  })

  it('does not send the request when token acquisition fails', async () => {
    firebaseMocks.getCurrentUserIdToken.mockRejectedValue(new Error('Token unavailable'))

    await expect(api.get('/test')).rejects.toThrow('Token unavailable')
    expect(adapter).not.toHaveBeenCalled()
  })
})
