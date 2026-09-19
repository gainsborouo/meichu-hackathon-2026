import type { CheckoutRequest } from './types';

// Request validation shared by the content script boundary and the background.
// The recommendation itself now comes from the backend -- see lib/backend.ts.

export function isCheckoutRequest(value: unknown): value is CheckoutRequest {
  if (!value || typeof value !== 'object') return false;
  const data = value as Record<string, unknown>;
  return Object.keys(data).sort().join(',') === 'payable,platform,product,requestId'
    && typeof data.requestId === 'string' && data.requestId.length > 0 && data.requestId.length < 100
    && ['momo', 'shopee', 'pchome'].includes(data.platform as string)
    && typeof data.product === 'string' && data.product.trim().length > 0 && data.product.length <= 10_000
    && typeof data.payable === 'number' && Number.isFinite(data.payable) && data.payable > 0;
}
