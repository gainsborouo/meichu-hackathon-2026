import type { CheckoutAdapter } from './types';
import { isVisible, parseAmount, present, productString, text } from './shared';

export function pchomeAdapter(doc: Document, url: string): CheckoutAdapter {
  const cart = () => doc.querySelector('main.l-main--cart');
  const productLists = () => [...doc.querySelectorAll('.c-listInfoGrid--cartList')];
  const productCards = () => [...doc.querySelectorAll('.c-prodInfoV2--cartListProd')];
  const total = () => doc.querySelector('.c-nestedList--cartBillsTotals');
  const checkoutButton = () => doc.querySelector('button[data-regression="step1-checkout-btn"]');
  const isSelected = (card: Element) => {
    const item = card.closest('li');
    const checkbox = item?.querySelector<HTMLInputElement>('input[type="checkbox"]');
    return !!item?.querySelector('.o-checkbox.is-checked') || checkbox?.checked === true;
  };
  return {
    platform: 'pchome',
    isCheckoutPage: () => new URL(url).hostname === 'ecssl.pchome.com.tw'
      && /^\/fsrwd\/cart\/?$/.test(new URL(url).pathname)
      && isVisible(cart()) && isVisible(checkoutButton()),
    getProduct() {
      const names = productCards()
        .filter(card => card.getAttribute('data-soldout') !== 'true' && isVisible(card) && isSelected(card))
        .map(card => text(card.querySelector('.c-prodInfoV2__title')));
      return productString(names);
    },
    getPayableAmount() {
      const nodes = total()?.querySelectorAll('.c-prodPrice__price') ?? [];
      return nodes.length === 1 && isVisible(nodes[0]!) ? parseAmount(text(nodes[0]!)) : null;
    },
    // PChome-only product decision: recommend on the cart page and assume card
    // payment will be available because payment choices appear on the next page.
    hasCreditCardPayment: () => true,
    getRecommendationMountPoint: total,
    getObservationRoots: () => present([...productLists(), total(), checkoutButton()]),
  };
}
