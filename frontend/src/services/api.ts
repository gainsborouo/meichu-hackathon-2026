import axios from 'axios'

import { getCurrentUserIdToken } from '@/firebase'

export const api = axios.create({
  baseURL: '/api/v1',
})

api.interceptors.request.use(async (config) => {
  const token = await getCurrentUserIdToken()

  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  } else {
    config.headers.delete('Authorization')
  }

  return config
})
