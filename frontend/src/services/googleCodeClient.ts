/** Google Identity Services code client: the consent half of calendar connect.
 *
 * Firebase sign-in identifies the user but grants no calendar scope, so this
 * runs a second, separate consent. It uses the auth-code flow rather than the
 * implicit one because the backend wants a long-lived refresh token, which only
 * a code exchange yields -- see app/services/google_calendar.py.
 *
 * ux_mode 'popup' hands the code back through the opener instead of a redirect,
 * so the redirect_uri at exchange time is the literal "postmessage". That is
 * already the backend default, which is why POST /me/calendar/connect can be
 * called without a redirect_uri.
 */

const GIS_SRC = 'https://accounts.google.com/gsi/client'
const CALENDAR_SCOPE = 'https://www.googleapis.com/auth/calendar.events'

interface CodeResponse {
  code?: string
  error?: string
  error_description?: string
}

interface CodeClient {
  requestCode: () => void
}

interface GoogleIdentityServices {
  accounts: {
    oauth2: {
      initCodeClient: (config: {
        client_id: string
        scope: string
        ux_mode: 'popup' | 'redirect'
        select_account?: boolean
        callback: (response: CodeResponse) => void
        error_callback?: (error: { type?: string; message?: string }) => void
      }) => CodeClient
    }
  }
}

declare global {
  interface Window {
    google?: GoogleIdentityServices
  }
}

/** Why a consent attempt produced no code.
 *
 * The distinction matters to callers that open consent on the user's behalf
 * rather than on a button press: 'declined' is a normal answer and must stay
 * silent, while 'blocked' means the popup never opened and the user needs a
 * fresh gesture to retry.
 */
export type ConsentFailureReason = 'declined' | 'blocked' | 'failed'

export class CalendarConsentError extends Error {
  readonly reason: ConsentFailureReason

  constructor(message: string, reason: ConsentFailureReason) {
    super(message)
    this.name = 'CalendarConsentError'
    this.reason = reason
  }
}

function reasonFromGisError(type?: string): ConsentFailureReason {
  if (type === 'popup_closed') return 'declined'
  if (type === 'popup_failed_to_open') return 'blocked'
  return 'failed'
}

let loader: Promise<GoogleIdentityServices> | null = null

function loadGis(): Promise<GoogleIdentityServices> {
  if (loader) return loader

  loader = new Promise((resolve, reject) => {
    if (window.google?.accounts?.oauth2) {
      resolve(window.google)
      return
    }

    const script = document.createElement('script')
    script.src = GIS_SRC
    script.async = true
    script.onload = () => {
      if (window.google?.accounts?.oauth2) resolve(window.google)
      else reject(new Error('Google Identity Services 載入後仍無法使用。'))
    }
    script.onerror = () => {
      loader = null // let a later attempt retry instead of caching the failure
      reject(new Error('無法載入 Google Identity Services，請檢查網路或擋廣告外掛。'))
    }
    document.head.appendChild(script)
  })

  return loader
}

export function googleClientId(): string {
  return import.meta.env.VITE_GOOGLE_CLIENT_ID ?? ''
}

/** Fetch the GIS script ahead of time, ignoring failures.
 *
 * Worth doing before an automatic connect: every millisecond between the
 * user's click and window.open is a millisecond closer to losing the
 * transient activation that keeps the popup out of the blocker.
 */
export function preloadGis(): void {
  void loadGis().catch(() => {})
}

/** How long to wait for GIS to call one of its callbacks before giving up.
 *
 * Neither callback is guaranteed to fire: if the popup completes consent but
 * cannot reach the opener -- a cross-origin-opener-policy header, an origin
 * missing from the OAuth client's authorized list, a popup closed by the OS --
 * GIS goes quiet and the returned promise would otherwise stay pending for the
 * life of the page, leaving the button stuck on its busy label with nothing on
 * screen. Rejecting is worse than a code but far better than silence.
 */
const CONSENT_TIMEOUT_MS = 120_000

/** Open the consent popup and resolve with a one-time authorization code. */
export async function requestCalendarCode(): Promise<string> {
  const clientId = googleClientId()
  if (!clientId) {
    throw new Error('VITE_GOOGLE_CLIENT_ID 未設定，請見 frontend/.env.example。')
  }

  const gis = await loadGis()

  return new Promise((resolve, reject) => {
    // Whichever path fires first wins; later ones are ignored, so a late
    // callback cannot reject a promise the timeout already settled, or vice versa.
    let settled = false

    const succeed = (code: string) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(code)
    }

    const fail = (message: string, reason: ConsentFailureReason) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      reject(new CalendarConsentError(message, reason))
    }

    const timer = setTimeout(() => {
      console.error(
        '[calendar-connect] Google Identity Services never invoked callback or ' +
          'error_callback. The popup closed without handing the authorization code ' +
          'back to this page. Check that this exact origin is listed under ' +
          '"Authorized JavaScript origins" for the OAuth client:',
        window.location.origin,
      )
      fail(
        `Google 授權視窗已結束，但未回傳授權碼。請確認 ${window.location.origin} ` +
          '已加入該 OAuth 用戶端的「Authorized JavaScript origins」。',
        'failed',
      )
    }, CONSENT_TIMEOUT_MS)

    const client = gis.accounts.oauth2.initCodeClient({
      client_id: clientId,
      scope: CALENDAR_SCOPE,
      ux_mode: 'popup',
      // Forcing the chooser re-shows consent, which is what makes Google return
      // a refresh_token again on a repeat connect.
      select_account: true,
      callback: (response) => {
        // Logged raw: a response carrying neither a code nor an error is the
        // case that is impossible to diagnose from the UI alone.
        console.debug('[calendar-connect] GIS callback', response)
        if (response.code) succeed(response.code)
        else
          fail(
            response.error_description || response.error || '未取得授權碼。',
            response.error === 'access_denied' ? 'declined' : 'failed',
          )
      },
      error_callback: (error) => {
        console.debug('[calendar-connect] GIS error_callback', error)
        fail(error.message || error.type || '授權視窗已關閉。', reasonFromGisError(error.type))
      },
    })
    client.requestCode()
  })
}
