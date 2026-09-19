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
  return publicUser(resultUser.user);
}

export async function signOutCurrentUser(): Promise<void> {
  await signOut(auth);
}
