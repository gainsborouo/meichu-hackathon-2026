import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AxiosError } from 'axios'

import { connectCalendarIfNeeded } from '../calendarConnect'
import { CalendarConsentError } from '../googleCodeClient'

const mocks = vi.hoisted(() => ({
  googleClientId: vi.fn<() => string>(),
  requestCalendarCode: vi.fn<() => Promise<string>>(),
  get: vi.fn<(url: string) => Promise<{ data: { connected: boolean } }>>(),
  post: vi.fn<(url: string, body: unknown) => Promise<{ data: unknown }>>(),
}))

vi.mock('@/services/googleCodeClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../googleCodeClient')>()
  return {
    ...actual,
    googleClientId: mocks.googleClientId,
    requestCalendarCode: mocks.requestCalendarCode,
  }
})

vi.mock('@/services/api', () => ({
  api: { get: mocks.get, post: mocks.post },
}))

beforeEach(() => {
  vi.clearAllMocks()
  mocks.googleClientId.mockReturnValue('client-id')
  mocks.get.mockResolvedValue({ data: { connected: false } })
  mocks.post.mockResolvedValue({ data: {} })
  mocks.requestCalendarCode.mockResolvedValue('auth-code')
})

describe('connectCalendarIfNeeded', () => {
  it('exchanges the code when no grant is on file', async () => {
    await expect(connectCalendarIfNeeded()).resolves.toEqual({ status: 'connected' })
    expect(mocks.post).toHaveBeenCalledWith('/me/calendar/connect', { code: 'auth-code' })
  })

  it('does not prompt when a grant already exists', async () => {
    mocks.get.mockResolvedValue({ data: { connected: true } })

    await expect(connectCalendarIfNeeded()).resolves.toEqual({ status: 'already-connected' })
    expect(mocks.requestCalendarCode).not.toHaveBeenCalled()
  })

  it('does not prompt when no client id is configured', async () => {
    mocks.googleClientId.mockReturnValue('')

    await expect(connectCalendarIfNeeded()).resolves.toEqual({ status: 'unavailable' })
    expect(mocks.get).not.toHaveBeenCalled()
    expect(mocks.requestCalendarCode).not.toHaveBeenCalled()
  })

  it('reports a closed popup as declined rather than an error', async () => {
    mocks.requestCalendarCode.mockRejectedValue(
      new CalendarConsentError('授權視窗已關閉。', 'declined'),
    )

    await expect(connectCalendarIfNeeded()).resolves.toEqual({ status: 'declined' })
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('reports a popup that never opened as blocked', async () => {
    mocks.requestCalendarCode.mockRejectedValue(new CalendarConsentError('blocked', 'blocked'))

    await expect(connectCalendarIfNeeded()).resolves.toEqual({
      status: 'blocked',
      message: 'blocked',
    })
  })

  it('does not prompt when the status check fails', async () => {
    mocks.get.mockRejectedValue(new Error('network down'))

    await expect(connectCalendarIfNeeded()).resolves.toEqual({
      status: 'failed',
      message: 'network down',
    })
    expect(mocks.requestCalendarCode).not.toHaveBeenCalled()
  })

  it('surfaces the backend detail when the exchange fails', async () => {
    mocks.post.mockRejectedValue(
      new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, {
        status: 400,
        data: { detail: 'invalid_grant' },
      } as never),
    )

    await expect(connectCalendarIfNeeded()).resolves.toEqual({
      status: 'failed',
      message: 'invalid_grant',
    })
  })
})
