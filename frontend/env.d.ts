/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Google OAuth client for the Calendar consent popup; see .env.example. */
  readonly VITE_GOOGLE_CLIENT_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
