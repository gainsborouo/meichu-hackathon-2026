import { pageLocale } from '../lib/i18n';
import type { Locale } from '../lib/i18n';
import { platformFromUrl } from '../lib/platform';
import type { Inspection } from '../lib/types';
import { momoAdapter } from './momo';
import { pchomeAdapter } from './pchome';
import { shopeeAdapter } from './shopee';
import type { CheckoutAdapter } from './types';

export { parseAmount } from './shared';
export type { CheckoutAdapter } from './types';

export function getAdapter(doc: Document, url: string): CheckoutAdapter | null {
  const platform = platformFromUrl(url);
  if (platform === 'momo') return momoAdapter(doc, url);
  if (platform === 'pchome') return pchomeAdapter(doc, url);
  if (platform === 'shopee') return shopeeAdapter(doc, url);
  return null;
}

export function inspectAdapter(
  adapter: CheckoutAdapter | null,
  // The language the panel will render in, resolved where the document is. The
  // background cannot work this out: it has no document to read `lang` from, and
  // its own `navigator` is the service worker's, which is not necessarily the
  // page's. Deciding twice is how the card ended up with English labels beside a
  // Chinese `reason`, so it is decided once, here, and carried on the request.
  locale: Locale | null = null,
): Inspection {
  if (!adapter?.isCheckoutPage()) return { status: 'outside' };
  if (!adapter.hasCreditCardPayment()) return { status: 'no-credit-card', reason: 'credit-unavailable' };
  const product = adapter.getProduct();
  if (!product) return { status: 'incomplete', reason: 'product-missing' };
  const payable = adapter.getPayableAmount();
  if (payable === null) return { status: 'incomplete', reason: 'amount-missing' };
  return {
    status: 'ready',
    context: { platform: adapter.platform, product, payable, pageLocale: locale },
  };
}

export function inspectCheckout(doc: Document, url: string): Inspection {
  return inspectAdapter(getAdapter(doc, url), pageLocale(doc));
}
