// Panel and popup copy in the two languages we support.
//
// This is a plain module rather than WXT's `_locales` / browser.i18n for one
// reason: the same choice has to travel to the backend. Its `locale` field
// decides the language of `reason`, `cap_description` and `explanation`, which
// the panel renders verbatim -- so a UI in English beside a Chinese `reason`
// would be worse than either. browser.i18n resolves messages but does not hand
// back which locale it picked, so we resolve it ourselves and send the same value.

import { browser } from 'wxt/browser';

export type Locale = 'zh-TW' | 'en-US';

/** The `locale` values the backend accepts (schemas/recommendations.py). */
const SUPPORTED: readonly Locale[] = ['zh-TW', 'en-US'];

/**
 * The UI language implied by the browser's own preference.
 *
 * Anything Chinese maps to zh-TW: the backend has no other Chinese variant, and
 * showing English to a zh-CN reader would be a worse mismatch than traditional
 * characters. Everything else falls back to English.
 */
export function resolveLocale(languages: readonly string[] = navigator.languages ?? []): Locale {
  for (const tag of [...languages, navigator.language ?? '']) {
    const mapped = fromTag(tag);
    if (mapped) return mapped;
  }
  return 'en-US';
}

export function isLocale(value: unknown): value is Locale {
  return SUPPORTED.includes(value as Locale);
}

/** Maps any language tag onto the two locales we support, or null if unknown. */
function fromTag(tag: string): Locale | null {
  const lower = tag.toLowerCase();
  if (lower.startsWith('zh')) return 'zh-TW';
  if (lower.startsWith('en')) return 'en-US';
  return null;
}

/**
 * The language of the page the panel is embedded in, from its `lang` attribute.
 *
 * This is the better default for a panel sitting inside a checkout page: a
 * shopper reading a Chinese store page wants Chinese advice even if their browser
 * is set to English. Returns null when the page declares nothing useful, so the
 * caller can fall back.
 *
 * Only callable where there is a document -- the background has none, which is
 * why the content script reads this and sends it along (see lib/types.ts).
 */
export function pageLocale(doc: Document = document): Locale | null {
  const declared = doc.documentElement.getAttribute('lang')
    ?? doc.querySelector('meta[http-equiv="content-language"]')?.getAttribute('content')
    ?? '';
  return declared ? fromTag(declared) : null;
}

const STORAGE_KEY = 'locale';
// The language of the checkout page most recently seen by a content script. The
// popup has no page of its own to follow and no `tabs` permission to inspect one,
// so "follow page" there means the page the user was last looking at.
const LAST_PAGE_KEY = 'lastPageLocale';

/** Records the page language, so the popup can follow it too. */
export async function rememberPageLocale(locale: Locale): Promise<void> {
  try {
    await browser.storage.local.set({ [LAST_PAGE_KEY]: locale });
  } catch {
    /* Storage unavailable; the popup falls back to its own navigator. */
  }
}

/**
 * The language to use in the popup.
 *
 * An explicit choice wins. Otherwise it follows the last checkout page seen,
 * because that is what "follow page" means for a window with no page -- and it
 * keeps the popup consistent with the panel the user just looked at. Falls back to
 * the browser when no page has been seen yet.
 */
export async function popupLocale(): Promise<Locale> {
  try {
    const stored = await browser.storage.local.get([STORAGE_KEY, LAST_PAGE_KEY]);
    if (isLocale(stored[STORAGE_KEY])) return stored[STORAGE_KEY];
    if (isLocale(stored[LAST_PAGE_KEY])) return stored[LAST_PAGE_KEY];
  } catch {
    /* Fall through to the browser preference. */
  }
  return resolveLocale();
}

/**
 * The language to use: the user's explicit choice if they made one, otherwise
 * whatever the browser implies.
 *
 * Read from storage rather than resolved per call, because a checkout page has
 * to render in the chosen language even though the browser's own setting says
 * something else. Storage failures fall back rather than throw: a missing
 * preference is not a reason to show no recommendation.
 */
export async function currentLocale(fallback?: Locale | null): Promise<Locale> {
  try {
    const stored = await browser.storage.local.get(STORAGE_KEY);
    if (isLocale(stored[STORAGE_KEY])) return stored[STORAGE_KEY];
  } catch {
    /* Storage unavailable; a fallback is a fine answer. */
  }
  // The page's own language when the caller knows it, else the browser's.
  return fallback ?? resolveLocale();
}

/** Persists the choice. `null` clears it, returning to the browser preference. */
export async function setLocale(locale: Locale | null): Promise<void> {
  if (locale === null) await browser.storage.local.remove(STORAGE_KEY);
  else await browser.storage.local.set({ [STORAGE_KEY]: locale });
}

interface Messages {
  brandName: string;
  loadingHeadline: string;
  loadingMessage: string;
  loadingStage: Record<'live_card_lookup' | 'reprocessing' | 'official_verification', string>;
  baseBenefitNote: string;
  wait: {
    heading: string;
    extra: (amount: string, date: string) => string;
    buyNow: string;
    remindMe: string;
    saving: string;
    saved: string;
    savedAlready: string;
    notConnected: string;
    signedOut: string;
    failed: string;
  };
  successLabel: string;
  rewardEstimate: (amount: string, rate: string) => string;
  registrationNeeded: string;
  registrationLink: string;
  unverifiedNotice: string;
  retry: string;
  unavailable: {
    'credit-unavailable': string;
    'product-missing': string;
    'amount-missing': string;
  };
  failure: {
    'signed-out': { headline: string; message: string };
    'no-cards': { headline: string; message: string };
    failed: { headline: string; message: string };
  };
  popup: {
    language: string;
    languageAuto: string;
    registrationCampaigns: string;
    registrationCampaignsHint: string;
    registrationCampaignsFailed: string;
    checkingSignIn: string;
    signedInHint: string;
    signedOutHint: string;
    signIn: string;
    signingIn: string;
    signOut: string;
    genericUser: string;
    signInFailed: string;
  };
}

const zhTW: Messages = {
  brandName: '最佳一刷',
  loadingHeadline: '正在挑選信用卡…',
  loadingMessage: '正在比對你持有的信用卡優惠並確認活動內容，最長可能需要一到兩分鐘。',
  loadingStage: {
    live_card_lookup: '資料庫沒有你持卡的現行優惠，正在查詢官方頁面，這一步可能需要兩分鐘以上。',
    reprocessing: '已取得最新活動，正在重新比對。',
    official_verification: '正在向官方頁面確認活動內容。',
  },
  baseBenefitNote: '這是這張卡的基本回饋，不是限時活動。',
  wait: {
    heading: '等一下更划算',
    extra: (amount, date) => `${date} 起有更好的優惠，多賺約 ${amount}`,
    buyNow: '現在就刷',
    remindMe: '提醒我那天再買',
    saving: '正在建立提醒…',
    saved: '已加入行事曆提醒。',
    savedAlready: '這個活動已經提醒過了，不會重複建立。',
    notConnected: '尚未連結 Google 行事曆，請先在網站完成連結後再試。',
    signedOut: '登入狀態已失效，請重新登入後再試。',
    failed: '無法建立提醒，請稍後再試。',
  },
  successLabel: '建議使用這張信用卡',
  rewardEstimate: (amount, rate) => `預估回饋 ${amount}（${rate}）`,
  registrationNeeded: '這張卡的回饋需要先登錄活動。',
  registrationLink: '前往登錄',
  unverifiedNotice: '此優惠內容尚未經官方頁面驗證，請以銀行公告為準。',
  retry: '重新推薦',
  unavailable: {
    'credit-unavailable': '此頁沒有可用的信用卡付款選項，未發送推薦請求。',
    'product-missing': '此頁尚無法確認商品，未發送推薦請求。',
    'amount-missing': '此頁尚無法確認應付金額，未發送推薦請求。',
  },
  failure: {
    'signed-out': {
      headline: '尚未登入',
      message: '請點擊瀏覽器工具列的「最佳一刷」圖示並使用 Google 登入，登入後回到此頁即可取得推薦。',
    },
    'no-cards': {
      headline: '卡包還沒有信用卡',
      message: '已登入，但帳號中沒有可比較的信用卡。請先加入你持有的信用卡，再回到此頁。',
    },
    failed: {
      headline: '暫時無法取得推薦',
      message: '連線或伺服器發生問題，稍後再試一次。',
    },
  },
  popup: {
    language: '顯示語言',
    languageAuto: '跟隨網頁語言',
    registrationCampaigns: '接受需登錄的活動',
    registrationCampaignsHint: '開啟後會納入需要先登錄的活動，並在需要登錄時通知你；關閉則只推薦免登錄的回饋。',
    registrationCampaignsFailed: '無法更新設定，請稍後再試。',
    checkingSignIn: '正在確認登入狀態…',
    signedInHint: '已登入。在支援的購物頁結帳時會顯示信用卡推薦。',
    signedOutHint: '登入後才能在支援的購物頁取得信用卡推薦。',
    signIn: '使用 Google 登入',
    signingIn: '正在開啟 Google…',
    signOut: '登出',
    genericUser: 'Google 使用者',
    signInFailed: 'Google 登入失敗',
  },
};

const enUS: Messages = {
  brandName: 'Swipe Right',
  loadingHeadline: 'Choosing a card…',
  loadingMessage:
    'Comparing the offers on your cards and confirming the campaign terms. This can take a minute or two.',
  loadingStage: {
    live_card_lookup:
      'No current offers on file for your cards, so the official pages are being checked. This step can take over two minutes.',
    reprocessing: 'Latest campaigns retrieved; comparing again.',
    official_verification: 'Confirming the terms against the official page.',
  },
  baseBenefitNote: 'This is the card’s standing reward, not a limited-time campaign.',
  wait: {
    heading: 'Waiting earns more',
    extra: (amount, date) => `A better offer starts ${date}, worth about ${amount} more`,
    buyNow: 'Buy now',
    remindMe: 'Remind me then',
    saving: 'Creating the reminder…',
    saved: 'Reminder added to your calendar.',
    savedAlready: 'This campaign was already reminded; no duplicate was created.',
    notConnected: 'No Google Calendar linked yet. Link it on the website, then try again.',
    signedOut: 'Your session has expired. Sign in again and retry.',
    failed: 'Could not create the reminder. Try again shortly.',
  },
  successLabel: 'Pay with this card',
  rewardEstimate: (amount, rate) => `Est. reward ${amount} (${rate})`,
  registrationNeeded: 'This card requires registering for the campaign first.',
  registrationLink: 'Register',
  unverifiedNotice:
    'These terms have not been confirmed against an official page. Check the bank’s own announcement.',
  retry: 'Try again',
  unavailable: {
    'credit-unavailable': 'No credit-card payment option on this page, so nothing was requested.',
    'product-missing': 'The items on this page could not be read, so nothing was requested.',
    'amount-missing': 'The amount due on this page could not be read, so nothing was requested.',
  },
  failure: {
    'signed-out': {
      headline: 'Not signed in',
      message:
        'Open “Swipe Right” from the browser toolbar and sign in with Google, then come back to this page.',
    },
    'no-cards': {
      headline: 'No cards in your wallet',
      message:
        'You are signed in, but your account has no cards to compare. Add the cards you hold, then come back to this page.',
    },
    failed: {
      headline: 'No recommendation right now',
      message: 'The connection or the server had a problem. Try again shortly.',
    },
  },
  popup: {
    language: 'Language',
    languageAuto: 'Follow page',
    registrationCampaigns: 'Include campaigns needing registration',
    registrationCampaignsHint:
      'When on, offers that require registering first are included and you are told when to register. When off, only rewards needing no registration are recommended.',
    registrationCampaignsFailed: 'Could not update the setting. Try again shortly.',
    checkingSignIn: 'Checking sign-in status…',
    signedInHint: 'Signed in. A card recommendation appears when you check out on a supported store.',
    signedOutHint: 'Sign in to get card recommendations on supported stores.',
    signIn: 'Sign in with Google',
    signingIn: 'Opening Google…',
    signOut: 'Sign out',
    genericUser: 'Google user',
    signInFailed: 'Google sign-in failed',
  },
};

const MESSAGES: Record<Locale, Messages> = { 'zh-TW': zhTW, 'en-US': enUS };

export function messages(locale: Locale = resolveLocale()): Messages {
  return MESSAGES[locale] ?? enUS;
}

export { SUPPORTED as SUPPORTED_LOCALES };
export type { Messages };
