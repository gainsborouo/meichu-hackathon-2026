import { afterEach, describe, expect, it } from 'vitest'

import { LOCALE_STORAGE_KEY, resolveInitialLocale, setLocale } from '../i18n'
import enUS from '../locales/en-US'
import zhTW from '../locales/zh-TW'

function messageKeys(value: object, prefix = ''): string[] {
  return Object.entries(value).flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return typeof child === 'object' && child !== null ? messageKeys(child, path) : [path]
  })
}

afterEach(() => {
  localStorage.removeItem(LOCALE_STORAGE_KEY)
  setLocale('zh-TW', false)
})

describe('i18n', () => {
  it('prefers a saved locale and otherwise maps Chinese browsers to zh-TW', () => {
    expect(resolveInitialLocale('en-US', ['zh-TW'])).toBe('en-US')
    expect(resolveInitialLocale(null, ['zh-HK', 'en-US'])).toBe('zh-TW')
    expect(resolveInitialLocale(null, ['ja-JP'])).toBe('en-US')
    expect(resolveInitialLocale('invalid', ['en-US'])).toBe('en-US')
  })

  it('keeps both locale dictionaries structurally complete', () => {
    expect(messageKeys(enUS).sort()).toEqual(messageKeys(zhTW).sort())
  })

  it('persists the locale and updates document metadata', () => {
    const description = document.createElement('meta')
    description.name = 'description'
    document.head.append(description)

    setLocale('en-US')

    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en-US')
    expect(document.documentElement.lang).toBe('en-US')
    expect(document.title).toBe('SwipeRight')
    expect(description.content).toContain('best credit card')

    description.remove()
  })
})
