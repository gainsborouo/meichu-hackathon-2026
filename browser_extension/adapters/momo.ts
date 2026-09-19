import type { CheckoutAdapter } from './types';
import { isAvailableControl, isVisible, parseAmount, present, productString, text } from './shared';

// Observed in the user's saved cart/payment snapshots, 2026-09-19.
// The URL stays the same while the cart is hidden and the payment form is shown.
export function momoAdapter(doc: Document, url: string): CheckoutAdapter {
  const payment = () => doc.querySelector('#paymentType');
  const total = () => doc.querySelector('#paySum');
  const items = () => [...doc.querySelectorAll('#orderListBox .v-product-info')];
  const onRoute = new URL(url).hostname === 'cart.momoshop.com.tw'
    && new URL(url).pathname.startsWith('/view/cart/WEB/');
  return {
    platform: 'momo',
    isCheckoutPage: () => onRoute && isVisible(payment()) && !!doc.querySelector('#paymentRadioBtn'),
    getProduct() {
      // The payment snapshot retains the previous cart in a hidden block.
      // Read ONLY checked products in that document, never cached cart data.
      const selected = items().filter(item => !!item.querySelector('input[type="checkbox"]:checked:not(:disabled)'));
      return productString(selected.map(item => text(item.querySelector('.product-caption-text'))));
    },
    getPayableAmount: () => isVisible(total()) ? parseAmount(text(total())) : null,
    hasCreditCardPayment: () => [...doc.querySelectorAll('#paymentRadioBtn input[name="rdoPayment"]')]
      .some(input => ['CARD_ID', 'CARDALLOT_ID', 'CRP_ID'].includes(input.id) && isAvailableControl(input)),
    getRecommendationMountPoint: payment,
    getObservationRoots: () => present([
      doc.querySelector('#orderListBox'), doc.querySelector('#paymentRadioBtn'), total()?.closest('table'),
    ]),
  };
}
