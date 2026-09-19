import { browser } from 'wxt/browser';
import { defineContentScript } from 'wxt/utils/define-content-script';
import { getAdapter } from '../adapters';
import { mountRecommendation } from '../lib/mount';
import { SITE_MATCHES } from '../lib/platform';
import { checkoutUrl } from '../lib/snapshot';

export default defineContentScript({
  // WebExtension match patterns do not include ports. checkoutUrl still restricts
  // snapshot behavior to the exact localhost origin and port.
  matches: import.meta.env.MODE === 'snapshot' ? [...SITE_MATCHES, 'http://127.0.0.1/*'] : SITE_MATCHES,
  runAt: 'document_idle',
  main(ctx) {
    let requests = 0;
    const mounted = mountRecommendation(
      () => getAdapter(document, checkoutUrl(location.href)),
      import.meta.env.MODE === 'snapshot' ? () => {
        document.documentElement.dataset.rewardTestRequests = String(++requests);
      } : undefined,
    );

    // The user signs in or out in the popup, not here, so without this the panel
    // would keep showing "not signed in" until the page was reloaded by hand.
    // retry() forces a fresh request: the checkout state has not changed, so a
    // plain refresh would be deduplicated away.
    const onAuthChange = (message: unknown) => {
      if ((message as { type?: string } | null)?.type === 'auth:changed') mounted.retry();
    };
    browser.runtime.onMessage.addListener(onAuthChange);

    ctx.onInvalidated(() => {
      browser.runtime.onMessage.removeListener(onAuthChange);
      mounted.dispose();
    });
  },
});
