import { beforeEach, describe, expect, it, vi } from 'vitest'

const authMocks = vi.hoisted(() => {
  const state = {
    currentUser: null as { getIdToken: () => Promise<string> } | null,
  }
  const authStateReady = vi.fn<() => Promise<void>>()

  return {
    addScope: vi.fn<(scope: string) => void>(),
    auth: {
      authStateReady,
      get currentUser() {
        return state.currentUser
      },
    },
    authStateReady,
    state,
  }
})

vi.mock('firebase/app', () => ({
  initializeApp: vi.fn<(_config: object) => object>(() => ({})),
}))

vi.mock('firebase/auth', () => ({
  getAuth: vi.fn<(_app: unknown) => typeof authMocks.auth>(() => authMocks.auth),
  GoogleAuthProvider: class {
    addScope(scope: string) {
      authMocks.addScope(scope)
    }
  },
}))

import { getCurrentUserIdToken } from '../firebase'

beforeEach(() => {
  vi.clearAllMocks()
  authMocks.state.currentUser = null
  authMocks.authStateReady.mockResolvedValue(undefined)
})

describe('getCurrentUserIdToken', () => {
  it('waits for the initial auth state before getting the token', async () => {
    let markReady: (() => void) | undefined
    const ready = new Promise<void>((resolve) => {
      markReady = resolve
    })
    const getIdToken = vi.fn<() => Promise<string>>(async () => 'firebase-token')
    authMocks.authStateReady.mockReturnValue(ready)
    authMocks.state.currentUser = { getIdToken }

    const tokenPromise = getCurrentUserIdToken()

    expect(getIdToken).not.toHaveBeenCalled()
    markReady?.()

    await expect(tokenPromise).resolves.toBe('firebase-token')
    expect(getIdToken).toHaveBeenCalledOnce()
  })

  it('returns null when Firebase has no current user', async () => {
    await expect(getCurrentUserIdToken()).resolves.toBeNull()
    expect(authMocks.authStateReady).toHaveBeenCalledOnce()
  })

  it('propagates token acquisition failures', async () => {
    authMocks.state.currentUser = {
      getIdToken: vi.fn<() => Promise<string>>().mockRejectedValue(new Error('Token unavailable')),
    }

    await expect(getCurrentUserIdToken()).rejects.toThrow('Token unavailable')
  })
})
