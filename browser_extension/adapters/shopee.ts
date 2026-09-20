import type { CheckoutAdapter } from './types';
import { isAvailableControl, isVisible, parseAmount, present, productString, text } from './shared';

// Semantic names and relationships from the checkout page. No generated CSS
// classes -- Shopee's are hashed and change between builds.
//
// The page is available in more than one language and the extension cannot
// choose which: a shopper's Shopee account language is their own setting. So
// every label this adapter matches on is listed per language rather than
// hardcoded, and a new language means adding entries here, not new logic.
const TOTAL_HEADINGS = ['總付款金額', 'Total Payment:', 'Total Payment'];
const CREDIT_LABELS = [
  '信用卡/金融卡',
  '信用卡分期付款',
  'Credit / Debit Card',
  'Credit Card Installment',
];

/** Trailing colons and surrounding space differ between languages. */
const normalizeLabel = (value: string) => value.replace(/[:：]\s*$/, '').trim();

export function shopeeAdapter(doc: Document, url: string): CheckoutAdapter {
  const main = () => doc.querySelector('[role="main"]');
  const payment = () => main()?.querySelector('.checkout-payment-method-main') ?? null;
  const amountNode = () => {
    // The same label appears on an h2 (the section) and an h3 (the figure), so
    // take the deepest match: only the h3 is followed by the amount itself.
    // Requiring exactly one match would reject the page outright.
    const wanted = TOTAL_HEADINGS.map(normalizeLabel);
    const matches = [...(main()?.querySelectorAll('h1, h2, h3, h4') ?? [])]
      .filter(h => wanted.includes(normalizeLabel(text(h))))
      .filter(h => h.nextElementSibling && parseAmount(text(h.nextElementSibling)) !== null);
    return matches.length ? matches[matches.length - 1]!.nextElementSibling : null;
  };
  const names = () => [...(main()?.querySelectorAll('picture') ?? [])]
    .filter(picture => picture.querySelector('img[alt="product image"]'))
    .map(picture => picture.nextElementSibling)
    // The product name sits in the element after the thumbnail. Its inner markup
    // varies (one span in one language, two when the name is split), so trust the
    // position and the presence of text rather than an exact child count.
    .filter((e): e is Element => !!e && e.tagName === 'SPAN' && text(e).length > 0);

  // textContent concatenates sibling spans with nothing between them, which
  // would run two halves of a name together ("BeddingClass A"). Read the spans
  // and join them, so the name reaches the backend as it reads on the page.
  const nameText = (element: Element) => {
    const parts = [...element.children]
      .filter(child => child.tagName === 'SPAN')
      .map(child => text(child))
      .filter(Boolean);
    return parts.length > 1 ? parts.join(' ') : text(element);
  };
  return {
    platform: 'shopee',
    isCheckoutPage: () => /^\/checkout\/?$/.test(new URL(url).pathname) && isVisible(payment()),
    getProduct: () => productString(names().filter(isVisible).map(nameText)),
    getPayableAmount: () => isVisible(amountNode()) ? parseAmount(text(amountNode())) : null,
    hasCreditCardPayment: () => [...(payment()?.querySelectorAll('[role="radio"]') ?? [])]
      .some(control => CREDIT_LABELS.includes((control.getAttribute('aria-label') ?? '').trim())
        && !control.classList.contains('product-variation--disabled') && isAvailableControl(control)),
    getRecommendationMountPoint: () => payment()?.parentElement ?? null,
    getObservationRoots: () => present([...names(), amountNode(), payment()?.querySelector('[role="radiogroup"]')]),
  };
}
