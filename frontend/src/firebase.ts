import { initializeApp } from 'firebase/app'
import { getAuth, GoogleAuthProvider } from 'firebase/auth'

const firebaseConfig = {
  apiKey: 'AIzaSyBymNUB1CoVj-f9D7TEDSGnmOC9jJ9gDfI',
  authDomain: 'meichu-2026.firebaseapp.com',
  projectId: 'meichu-2026',
  storageBucket: 'meichu-2026.firebasestorage.app',
  messagingSenderId: '419829464790',
  appId: '1:419829464790:web:c65574b43fb2f6a7429377',
}

const app = initializeApp(firebaseConfig)

export const auth = getAuth(app)
export const googleProvider = new GoogleAuthProvider()
googleProvider.addScope('https://www.googleapis.com/auth/calendar.events')

export async function getCurrentUserIdToken(): Promise<string | null> {
  await auth.authStateReady()

  return auth.currentUser?.getIdToken() ?? null
}
