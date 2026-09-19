import { defineStore } from 'pinia'
import { onAuthStateChanged, type Unsubscribe, type User } from 'firebase/auth'
import { ref } from 'vue'

import { auth } from '@/firebase'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const ready = ref(false)
  let unsubscribe: Unsubscribe | null = null

  function initializeAuth() {
    if (unsubscribe) return

    unsubscribe = onAuthStateChanged(auth, (currentUser) => {
      user.value = currentUser
      ready.value = true
    })
  }

  return { user, ready, initializeAuth }
})
