/** Calendar connect as a post-login step rather than a page the user must find.
 *
 * Firebase sign-in and calendar consent are two separate grants (see
 * googleCodeClient.ts), so a freshly signed-in user still has no calendar
 * access. This runs the second grant straight after the first, and reports
 * what happened instead of throwing, because the caller is a login handler:
 * failing to connect a calendar must never look like a failed login.
 */

import { AxiosError } from 'axios'

import { api } from '@/services/api'
import {
  CalendarConsentError,
  googleClientId,
  requestCalendarCode,
} from '@/services/googleCodeClient'

export type CalendarConnectOutcome =
  /** Consent granted and the code exchanged. */
  | { status: 'connected' }
  /** Nothing to do -- a refresh token was already on file. */
  | { status: 'already-connected' }
  /** No VITE_GOOGLE_CLIENT_ID, so there is no consent to run. */
  | { status: 'unavailable' }
  /** The user closed or refused the consent popup. Not an error. */
  | { status: 'declined' }
  /** The popup never opened; retrying from a click will work. */
  | { status: 'blocked'; message: string }
  /** Anything else, including a failed code exchange. */
  | { status: 'failed'; message: string }

function describe(err: unknown): string {
  if (err instanceof AxiosError) {
    const detail = err.response?.data?.detail
    if (detail) return typeof detail === 'string' ? detail : JSON.stringify(detail)
    return err.message
  }
  return err instanceof Error ? err.message : String(err)
}

/** Check for an existing grant, and run consent only when one is missing. */
export async function connectCalendarIfNeeded(): Promise<CalendarConnectOutcome> {
  if (!googleClientId()) return { status: 'unavailable' }

  try {
    const { data } = await api.get<{ connected: boolean }>('/me/calendar')
    if (data.connected) return { status: 'already-connected' }
  } catch (err) {
    // A status check that fails tells us nothing about whether consent is
    // needed, so prompting would risk a popup the user does not owe us.
    return { status: 'failed', message: describe(err) }
  }

  let code: string
  try {
    code = await requestCalendarCode()
  } catch (err) {
    if (err instanceof CalendarConsentError) {
      if (err.reason === 'declined') return { status: 'declined' }
      if (err.reason === 'blocked') return { status: 'blocked', message: err.message }
    }
    return { status: 'failed', message: describe(err) }
  }

  try {
    await api.post('/me/calendar/connect', { code })
  } catch (err) {
    return { status: 'failed', message: describe(err) }
  }

  return { status: 'connected' }
}
