// Only used by explicit `--mode snapshot` builds for localhost browser verification.
// No localhost permission or URL mapping is active in production builds.
export function checkoutUrl(url: string): string {
  if (import.meta.env.MODE !== 'snapshot') return url;
  const parsed = new URL(url);
  if (parsed.origin !== 'http://127.0.0.1:8766') return url;
  const routes: Record<string, string> = {
    '/momo/cart': 'https://cart.momoshop.com.tw/view/cart/WEB/newNormal',
    '/momo/payment': 'https://cart.momoshop.com.tw/view/cart/WEB/newNormal',
    '/shopee/cart': 'https://shopee.tw/cart',
    '/shopee/payment': 'https://shopee.tw/checkout',
    '/pchome/cart': 'https://ecssl.pchome.com.tw/fsrwd/cart',
    '/pchome/payment': 'https://ecssl.pchome.com.tw/fsrwd/cart/payinfo',
  };
  return routes[parsed.pathname] ?? url;
}
