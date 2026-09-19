import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createPinia, setActivePinia } from 'pinia'
import { onAuthStateChanged, type User } from 'firebase/auth'
import { useAuthStore } from '../authStore'

const authMocks = vi.hoisted(() => ({
  auth: {},
  currentHandler: null as ((user: unknown) => void) | null,
  unsubscribe: vi.fn<() => void>(),
}))

vi.mock('@/firebase', () => ({ auth: authMocks.auth }))

vi.mock('firebase/auth', () => ({
  onAuthStateChanged: vi.fn<(_auth: unknown, onUser: (user: unknown) => void) => () => void>(
    (_auth, onUser) => {
      authMocks.currentHandler = onUser
      return authMocks.unsubscribe
    },
  ),
}))

beforeEach(() => {
  vi.clearAllMocks()
  authMocks.currentHandler = null
  setActivePinia(createPinia())
})

describe('authStore', () => {
  it('initializes one listener and reflects Firebase auth state', () => {
    const store = useAuthStore()

    store.initializeAuth()
    store.initializeAuth()

    expect(onAuthStateChanged).toHaveBeenCalledExactlyOnceWith(authMocks.auth, expect.any(Function))
    expect(store.ready).toBe(false)

    const user = { uid: 'user-1' } as User
    authMocks.currentHandler?.(user)

    expect(store.user?.uid).toBe(user.uid)
    expect(store.ready).toBe(true)

    authMocks.currentHandler?.(null)

    expect(store.user).toBeNull()
  })
})
