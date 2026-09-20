import type { Locale } from './i18n'

export interface BilingualCardName {
  bank_name: string | null
  name: string
  issuer_en: string | null
  name_en: string | null
}

export function localizedBankName(card: BilingualCardName, locale: Locale) {
  return locale === 'en-US'
    ? (card.issuer_en ?? card.bank_name)
    : (card.bank_name ?? card.issuer_en)
}

export function localizedCardName(card: BilingualCardName, locale: Locale) {
  return locale === 'en-US' ? (card.name_en ?? card.name) : card.name
}
