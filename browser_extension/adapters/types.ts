import type { Platform } from '../lib/types';

export interface CheckoutAdapter {
  platform: Platform;
  isCheckoutPage(): boolean;
  getProduct(): string | null;
  getPayableAmount(): number | null;
  hasCreditCardPayment(): boolean;
  /** The widget is inserted AFTER this block in normal document flow. */
  getRecommendationMountPoint(): Element | null;
  /** Only product, total, and payment-option nodes; never the whole document. */
  getObservationRoots(): Element[];
}
