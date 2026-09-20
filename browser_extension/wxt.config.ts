import { defineConfig } from 'wxt';

// host_permissions needs a bare origin pattern, not the full URL, and it must
// be derived from the same value lib/backend-config.ts uses so the two cannot
// drift apart. Keep the fallback in sync with DEFAULT_BACKEND_URL there.
function backendOrigin(url: string | undefined): string {
  return new URL(url || 'http://localhost:8000').origin;
}

export default defineConfig({
  modules: ['@wxt-dev/module-vue'],
  manifestVersion: 3,
  webExt: {
    binaries: {
      chrome: process.env.CHROME_BINARY ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      firefox: process.env.FIREFOX_BINARY ?? '/Applications/Zen.app/Contents/MacOS/zen',
    },
    startUrls: process.env.SNAPSHOT_E2E === '1' ? ['http://127.0.0.1:8766/momo/payment'] : undefined,
    firefoxArgs: process.env.SNAPSHOT_E2E === '1' ? ['--remote-debugging-port', '9226'] : undefined,
  },
  manifest: ({ browser }) => ({
    // The manifest is static, so its name and description come from _locales
    // rather than lib/i18n.ts. default_locale must be set for these to resolve.
    name: '__MSG_extName__',
    description: '__MSG_extDescription__',
    default_locale: 'zh_TW',
    icons: {
      16: 'icon-16.png',
      32: 'icon-32.png',
      48: 'icon-48.png',
      128: 'icon-128.png',
    },
    action: {
      default_icon: {
        16: 'icon-16.png',
        32: 'icon-32.png',
      },
    },
    // storage: remembers the language the user picked in the popup, so a
    // checkout page renders in it regardless of the browser's own setting.
    permissions: ['identity', 'storage'],
    // Firefox MV3 applies a default extension_pages CSP whose connect-src does
    // not include http:, so a plain-http backend (local development) is blocked
    // before the request leaves the extension -- Chrome allows it. Declaring the
    // policy explicitly re-adds the backend origin. An https backend needs no
    // entry here, but keeping it derived from the same value means the dev and
    // deployed builds cannot disagree.
    content_security_policy: {
      extension_pages: [
        "script-src 'self'",
        "object-src 'self'",
        `connect-src 'self' https: ${backendOrigin(process.env.WXT_BACKEND_URL)}`,
      ].join('; '),
    },
    host_permissions: [
      'https://identitytoolkit.googleapis.com/*',
      'https://securetoken.googleapis.com/*',
      // The recommendation backend. Must match WXT_BACKEND_URL in
      // lib/backend-config.ts, or the fetch is blocked at runtime.
      `${backendOrigin(process.env.WXT_BACKEND_URL)}/*`,
    ],
    ...(browser === 'firefox'
      ? { browser_specific_settings: { gecko: {
        id: 'card-rewards@meichu.example',
        data_collection_permissions: { required: ['authenticationInfo'] },
      } } }
      : {}),
  }),
});
