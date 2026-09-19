import type { Platform } from './types';

export const SITE_MATCHES = [
  'https://*.momoshop.com.tw/*',
  'https://shopee.tw/*',
  'https://*.shopee.tw/*',
  'https://*.pchome.com.tw/*',
];

export function platformFromUrl(value: string): Platform | null {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:') return null;
    const matches = (domain: string) =>
      url.hostname === domain || url.hostname.endsWith(`.${domain}`);
    if (matches('momoshop.com.tw')) return 'momo';
    if (matches('shopee.tw')) return 'shopee';
    if (matches('pchome.com.tw')) return 'pchome';
  } catch { /* Invalid sender URL. */ }
  return null;
}
