// Firebase Web configuration is public application metadata. The Admin SDK
// service-account JSON and its private key must never be added to this project.
export const firebaseConfig = {
  apiKey: 'AIzaSyBymNUB1CoVj-f9D7TEDSGnmOC9jJ9gDfI',
  authDomain: 'meichu-2026.firebaseapp.com',
  projectId: 'meichu-2026',
  storageBucket: 'meichu-2026.firebasestorage.app',
  messagingSenderId: '419829464790',
  appId: '1:419829464790:web:c65574b43fb2f6a7429377',
} as const;

// This is our hosted extension sign-in page. Firebase's authDomain identifies
// the host, while /extension-auth.html is the custom page in auth_host/public.
export const AUTH_HELPER_URL = new URL(
  '/extension-auth.html',
  `https://${firebaseConfig.authDomain}`,
).href;
