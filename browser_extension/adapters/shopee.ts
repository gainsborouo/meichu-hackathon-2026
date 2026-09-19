import type { CheckoutAdapter } from './types';
import { isAvailableControl, isVisible, parseAmount, present, productString, text } from './shared';

// Semantic names and relationships from 結帳.html. No generated CSS classes.
export function shopeeAdapter(doc: Document, url: string): CheckoutAdapter {
  const main = () => doc.querySelector('[role="main"]');
  const payment = () => main()?.querySelector('.checkout-payment-method-main') ?? null;
  const amountNode = () => {
    const headings = [...(main()?.querySelectorAll('h3') ?? [])].filter(h => text(h) === '總付款金額');
    return headings.length === 1 ? headings[0]!.nextElementSibling : null;
  };
  const names = () => [...(main()?.querySelectorAll('picture') ?? [])]
    .filter(picture => picture.querySelector('img[alt="product image"]'))
    .map(picture => picture.nextElementSibling)
    .filter((e): e is Element => !!e && e.tagName === 'SPAN' && e.children.length === 1 && e.firstElementChild?.tagName === 'SPAN');
  return {
    platform: 'shopee',
    isCheckoutPage: () => /^\/checkout\/?$/.test(new URL(url).pathname) && isVisible(payment()),
    getProduct: () => productString(names().filter(isVisible).map(text)),
    getPayableAmount: () => isVisible(amountNode()) ? parseAmount(text(amountNode())) : null,
    hasCreditCardPayment: () => [...(payment()?.querySelectorAll('[role="radio"]') ?? [])]
      .some(control => ['信用卡/金融卡', '信用卡分期付款'].includes(control.getAttribute('aria-label') ?? '')
        && !control.classList.contains('product-variation--disabled') && isAvailableControl(control)),
    getRecommendationMountPoint: () => payment()?.parentElement ?? null,
    getObservationRoots: () => present([...names(), amountNode(), payment()?.querySelector('[role="radiogroup"]')]),
  };
}
