import { browser } from 'wxt/browser';
import { defineBackground } from 'wxt/utils/define-background';
import { getAuthenticatedUser, getCurrentUser, signInWithGoogle, signOutCurrentUser } from '../lib/auth';
import { getRecommendation } from '../lib/backend';
import { RecommendationError } from '../lib/types';
import { isCheckoutRequest } from '../lib/validation';
import { platformFromUrl } from '../lib/platform';
import { checkoutUrl } from '../lib/snapshot';

export default defineBackground(() => {
  // Signing in happens in the popup, so a checkout page already showing
  // "not signed in" has no way to learn that it changed.
  //
  // The query deliberately does not filter by url: Chrome only honours a `url`
  // filter when the extension holds the "tabs" permission or host permissions
  // for those sites, and this extension holds neither -- the content script is
  // injected via `matches`, which grants no tabs access. Filtering there made
  // Chrome match nothing and the broadcast silently reached no one.
  //
  // Sending to every tab is safe and needs no extra permission: only a tab
  // running our own content script has a listener, every other tab rejects the
  // message, and the payload carries no user data. Rejections are expected, so
  // each send swallows its own error rather than failing the whole broadcast.
  async function broadcastAuthChange(): Promise<void> {
    try {
      const tabs = await browser.tabs.query({});
      const delivered = await Promise.all(tabs.map(async tab => {
        if (tab.id === undefined) return false;
        try {
          await browser.tabs.sendMessage(tab.id, { type: 'auth:changed' });
          return true;
        } catch {
          return false;
        }
      }));
      // Counts only, no urls or titles. A broadcast that reaches nobody is the
      // failure mode this logs for: it looks identical to working correctly.
      console.debug('[刷哪張] auth change broadcast:',
        `${delivered.filter(Boolean).length}/${tabs.length} tabs`);
    } catch (error) {
      console.warn('[刷哪張] auth change broadcast failed:',
        error instanceof Error ? `${error.name}: ${error.message}` : String(error));
    }
  }

  browser.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (sender.id !== browser.runtime.id) {
      console.warn('[刷哪張] message rejected: sender.id mismatch',
        { got: sender.id, expected: browser.runtime.id });
      return;
    }
    const extensionPage = sender.url?.startsWith(browser.runtime.getURL('/')) === true;
    if (message?.type === 'auth:get' && extensionPage) {
      void getCurrentUser().then(user => sendResponse({ user }), () => sendResponse({ user: null }));
      return true;
    }
    if (message?.type === 'auth:sign-in' && extensionPage) {
      void signInWithGoogle().then(user => {
        sendResponse({ user });
        void broadcastAuthChange();
      }, error => {
        sendResponse({ error: error instanceof Error ? error.message : 'Google sign-in failed' });
      });
      return true;
    }
    if (message?.type === 'auth:sign-out' && extensionPage) {
      void signOutCurrentUser().then(() => {
        sendResponse({ user: null });
        // Also broadcast on sign-out, so a panel stops showing a recommendation
        // the user is no longer entitled to.
        void broadcastAuthChange();
      }, () => sendResponse({ error: 'Sign-out failed' }));
      return true;
    }
    if (message?.type !== 'recommend') return;
    // Every recommendation must come from a tab whose own url matches the
    // platform it claims, so a page cannot ask for a recommendation on another
    // site's behalf. There is no longer any exemption from this check.
    const senderPlatform = platformFromUrl(checkoutUrl(sender.url ?? 'about:blank'));
    if (!isCheckoutRequest(message.request) || senderPlatform !== message.request.platform) {
      // Rejected before any network call, so say which half failed -- otherwise
      // this is invisible and looks identical to a backend outage.
      console.warn('[刷哪張] recommendation request rejected', {
        validShape: isCheckoutRequest(message.request),
        senderPlatform,
        claimedPlatform: message.request?.platform,
      });
      sendResponse({ error: 'Invalid recommendation request' });
      return;
    }
    // The backend identifies the user from the ID token, so no userId is added
    // to the body; requiring a signed-in user still gates the request here.
    const { requestId, platform, product, payable } = message.request;
    void getAuthenticatedUser()
      .then(({ idToken }) => getRecommendation({ platform, product, payable }, idToken))
      .then(
        recommendation => sendResponse({ requestId, recommendation }),
        // `reason` rides alongside the message: an Error does not survive
        // runtime messaging, so the panel would otherwise lose the distinction.
        error => {
          // A recommendation that never reaches the backend is indistinguishable
          // from a server fault in the panel, so log why here. The message is an
          // error string, never page content or the token.
          console.warn('[刷哪張] recommendation failed:',
            error instanceof Error ? `${error.name}: ${error.message}` : String(error));
          sendResponse({
            error: error instanceof Error ? error.message : 'Recommendation unavailable',
            reason: error instanceof RecommendationError ? error.reason : 'failed',
          });
        },
      );
    return true;
  });
});
