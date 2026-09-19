import { createI18n } from 'vue-i18n'

import enUS from './locales/en-US'
import zhTW from './locales/zh-TW'

export const supportedLocales = ['zh-TW', 'en-US'] as const
export type Locale = (typeof supportedLocales)[number]

export const LOCALE_STORAGE_KEY = 'credit-card-recommendation.locale'

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && supportedLocales.includes(value as Locale)
}

export function resolveInitialLocale(
  storedLocale: string | null,
  browserLanguages: readonly string[],
): Locale {
  if (isLocale(storedLocale)) return storedLocale
  return browserLanguages.some((language) => language.toLowerCase().startsWith('zh'))
    ? 'zh-TW'
    : 'en-US'
}

function storedLocale() {
  if (typeof localStorage === 'undefined') return null

  try {
    return localStorage.getItem(LOCALE_STORAGE_KEY)
  } catch {
    return null
  }
}

function browserLanguages() {
  if (typeof navigator === 'undefined') return []
  return navigator.languages.length ? navigator.languages : [navigator.language]
}

const initialLocale = resolveInitialLocale(storedLocale(), browserLanguages())

export const i18n = createI18n({
  legacy: false,
  locale: initialLocale,
  fallbackLocale: 'zh-TW',
  messages: {
    'zh-TW': zhTW,
    'en-US': enUS,
  },
})

function updateDocumentMetadata(locale: Locale) {
  if (typeof document === 'undefined') return

  document.documentElement.lang = locale
  document.title = i18n.global.t('metadata.title')
  document
    .querySelector<HTMLMetaElement>('meta[name="description"]')
    ?.setAttribute('content', i18n.global.t('metadata.description'))
}

export function setLocale(locale: Locale, persist = true) {
  i18n.global.locale.value = locale

  if (persist && typeof localStorage !== 'undefined') {
    try {
      localStorage.setItem(LOCALE_STORAGE_KEY, locale)
    } catch {
      // 語言仍可在本次工作階段生效，不因瀏覽器封鎖儲存空間而中斷。
    }
  }

  updateDocumentMetadata(locale)
}

updateDocumentMetadata(initialLocale)
