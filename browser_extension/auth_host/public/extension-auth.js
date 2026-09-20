import { initializeApp } from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js';
import {
  getAuth,
  getRedirectResult,
  GoogleAuthProvider,
  signInWithRedirect,
} from 'https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js';

// Firebase sign-in alone grants no Calendar access, so the consent screen asks for
// it here. This yields an access token the user has consented to; the backend's
// POST /me/calendar/connect wants an authorization CODE to exchange for a refresh
// token, which this implicit flow does not produce -- so requesting the scope
// makes the consent happen, and connecting the calendar still needs its own code
// flow. See browser_extension/README.md.
const CALENDAR_SCOPE = 'https://www.googleapis.com/auth/calendar.events';

function googleProvider() {
  const provider = new GoogleAuthProvider();
  provider.addScope(CALENDAR_SCOPE);
  return provider;
}

const query = new URLSearchParams(location.search);
const redirectUri = query.get('redirect_uri');
const state = query.get('state');
const button = document.querySelector('#login');
const errorNode = document.querySelector('#error');

async function createAuth() {
  // Firebase Hosting generates this config for the project that owns the site.
  // This keeps the hosted helper on the same project as the extension/frontend
  // without maintaining another copied config object.
  const response = await fetch('/__/firebase/init.json', { cache: 'no-store' });
  if (!response.ok) throw new Error('Firebase Hosting config is unavailable');
  return getAuth(initializeApp(await response.json()));
}

function isExtensionRedirect(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:'
      && (url.hostname.endsWith('.chromiumapp.org') || url.hostname.endsWith('.extensions.allizom.org'));
  } catch {
    return false;
  }
}

function returnToExtension(values) {
  const redirect = new URL(redirectUri);
  redirect.hash = new URLSearchParams({ ...values, state }).toString();
  location.replace(redirect.href);
}

// Only forward credential fields Google actually returned. Sending an empty
// string would make the extension rebuild a credential from a blank token.
function credentialValues(credential) {
  const values = {};
  if (credential?.idToken) values.id_token = credential.idToken;
  if (credential?.accessToken) values.access_token = credential.accessToken;
  return values;
}

function showError(message) {
  errorNode.textContent = message;
  button.disabled = false;
}

if (!redirectUri || !state || !isExtensionRedirect(redirectUri)) {
  button.disabled = true;
  errorNode.textContent = 'Invalid extension sign-in request.';
} else {
  // signInWithRedirect navigates this same window to Google and back, because a
  // nested popup opened inside launchWebAuthFlow's window is commonly blocked.
  // Google therefore returns here on load, not to a popup callback.
  button.disabled = true;
  let auth;

  try {
    auth = await createAuth();
    const result = await getRedirectResult(auth);

    if (result) {
      const values = credentialValues(GoogleAuthProvider.credentialFromResult(result));
      if (!values.id_token && !values.access_token) throw new Error('missing-credential');
      returnToExtension(values);
    } else {
      button.disabled = false;
    }
  } catch {
    showError('Google sign-in failed. Please try again shortly.');
  }

  button.addEventListener('click', async () => {
    button.disabled = true;
    errorNode.textContent = '';
    try {
      await signInWithRedirect(auth ?? (await createAuth()), googleProvider());
    } catch {
      showError('Google sign-in failed. Please try again shortly.');
    }
  });
}
