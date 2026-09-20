import { afterEach, expect, it, vi } from 'vitest';

const { storage } = vi.hoisted(() => ({
  storage: { get: vi.fn(), set: vi.fn(), remove: vi.fn() },
}));
vi.mock('wxt/browser', () => ({ browser: { storage: { local: storage } } }));

afterEach(() => vi.clearAllMocks());

// The popup has no page of its own, so "follow page" there means the checkout page
// the user was last looking at. Without this it could only follow the browser,
// which is how the popup stayed English beside a Chinese panel.
it('popup follows the last checkout page when no choice was made', async () => {
  const { popupLocale } = await import('../lib/i18n');
  storage.get.mockResolvedValue({ lastPageLocale: 'zh-TW' });
  await expect(popupLocale()).resolves.toBe('zh-TW');
});

it('an explicit choice outranks the last page', async () => {
  const { popupLocale } = await import('../lib/i18n');
  storage.get.mockResolvedValue({ locale: 'en-US', lastPageLocale: 'zh-TW' });
  await expect(popupLocale()).resolves.toBe('en-US');
});

it('falls back to the browser when no page has been seen', async () => {
  const { popupLocale, resolveLocale } = await import('../lib/i18n');
  storage.get.mockResolvedValue({});
  await expect(popupLocale()).resolves.toBe(resolveLocale());
});

it('ignores unsupported stored values rather than trusting them', async () => {
  const { popupLocale, resolveLocale } = await import('../lib/i18n');
  storage.get.mockResolvedValue({ locale: 'de-DE', lastPageLocale: 'ja-JP' });
  await expect(popupLocale()).resolves.toBe(resolveLocale());
});

it('survives storage being unavailable', async () => {
  const { popupLocale, rememberPageLocale, resolveLocale } = await import('../lib/i18n');
  storage.get.mockRejectedValue(new Error('no storage'));
  await expect(popupLocale()).resolves.toBe(resolveLocale());
  // Recording must never throw either: it is a convenience, not a requirement.
  storage.set.mockRejectedValue(new Error('no storage'));
  await expect(rememberPageLocale('zh-TW')).resolves.toBeUndefined();
});
