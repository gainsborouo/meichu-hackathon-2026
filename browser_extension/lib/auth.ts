import { initializeApp } from 'firebase/app';
import {
  getAuth,
  GoogleAuthProvider,
  signInWithCredential,
  signOut,
  type User,
} from 'firebase/auth/web-extension';
import { browser } from 'wxt/browser';
import { AUTH_HELPER_URL, firebaseConfig } from './firebase-config';
import { RecommendationError } from './types';

const auth = getAuth(initializeApp(firebaseConfig));

export interface AuthUser {
  userId: string;
  displayName: string | null;
  email: string | null;
  photoURL: string | null;
}

function publicUser(user: User): AuthUser {
  return {
    userId: user.uid,
    displayName: user.displayName,
    email: user.email,
    photoURL: user.photoURL,
  };
}

export async function getCurrentUser(): Promise<AuthUser | null> {
  await auth.authStateReady();
  return auth.currentUser ? publicUser(auth.currentUser) : null;
}

export async function getAuthenticatedUser(): Promise<{ user: AuthUser; idToken: string }> {
  await auth.authStateReady();
  if (!auth.currentUser) throw new RecommendationError('signed-out', 'Authentication required');
  return { user: publicUser(auth.currentUser), idToken: await auth.currentUser.getIdToken() };
}

export async function signInWithGoogle(): Promise<AuthUser> {
  const redirectUri = browser.identity.getRedirectURL('firebase');
  const state = crypto.randomUUID();
  const authUrl = new URL(AUTH_HELPER_URL);
  authUrl.searchParams.set('redirect_uri', redirectUri);
  authUrl.searchParams.set('state', state);
  const result = await browser.identity.launchWebAuthFlow({ url: authUrl.href, interactive: true });
  if (!result) throw new Error('Google sign-in did not return a result');

  const expected = new URL(redirectUri);
  const returned = new URL(result);
  if (returned.origin !== expected.origin || returned.pathname !== expected.pathname) {
    throw new Error('Unexpected authentication redirect');
  }
  const values = new URLSearchParams(returned.hash.slice(1));
  if (values.get('state') !== state) throw new Error('Invalid authentication state');
  const providerError = values.get('error');
  if (providerError) throw new Error(providerError);
  // Treat blank tokens as absent so a partial redirect cannot build an empty credential.
  const idToken = values.get('id_token') || null;
  const accessToken = values.get('access_token') || null;
  if (!idToken && !accessToken) throw new Error('Google sign-in returned no credential');

  const credential = GoogleAuthProvider.credential(idToken, accessToken);
  const resultUser = await signInWithCredential(auth, credential);
  // Keep the Google access token: it carries the calendar.events scope the consent
  // screen asked for, and Firebase does not hand it back later -- signInWithCredential
  // returns it once and `auth.currentUser` has no trace of it. Held in memory only,
  // never in storage: it is a bearer credential with about an hour of life, and a
  // service worker restart losing it is the correct outcome.
  googleAccessToken = accessToken;
  return publicUser(resultUser.user);
}

// Set at sign-in, cleared at sign-out. Null means "we never had one, or it is gone".
let googleAccessToken: string | null = null;

/**
 * The Google access token from sign-in, if we still hold one.
 *
 * Scoped for Calendar, so the backend can use it on the user's behalf. Null after a
 * background restart even while the Firebase session is still valid, because the
 * token lives only in memory -- callers must treat absence as ordinary.
 */
export function getGoogleAccessToken(): string | null {
  return googleAccessToken;
}

export async function signOutCurrentUser(): Promise<void> {
  googleAccessToken = null;
  await signOut(auth);
}
