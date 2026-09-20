<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { browser } from 'wxt/browser';
import { isLocale, messages, popupLocale, setLocale } from '../../lib/i18n';
import type { Locale } from '../../lib/i18n';
import type { AuthUser } from '../../lib/auth';

// '' means "follow the browser" -- stored as an absent preference, not as a
// third locale, so changing the browser's language still takes effect.
const choice = ref<Locale | ''>('');
// The language actually in effect. With no explicit choice this follows the last
// checkout page a content script saw, because the popup has no page of its own --
// otherwise "follow page" here could only ever mean the browser's setting.
const effective = ref<Locale>('en-US');
const t = computed(() => messages(isLocale(choice.value) ? choice.value : effective.value));

async function applyLanguage() {
  await setLocale(isLocale(choice.value) ? choice.value : null);
  effective.value = await popupLocale();
}
const user = ref<AuthUser | null>(null);

// Server state, so it is read back rather than assumed, and only shown once
// known -- a toggle that defaults to off would misrepresent the account.
const registrationCampaigns = ref<boolean | null>(null);
const registrationBusy = ref(false);
const settingsError = ref('');

async function loadSettings() {
  settingsError.value = '';
  const response = await browser.runtime.sendMessage({ type: 'settings:get' });
  registrationCampaigns.value = response?.settings?.registration_campaigns_enabled ?? null;
}

async function toggleRegistrationCampaigns(event: Event) {
  const wanted = (event.target as HTMLInputElement).checked;
  registrationBusy.value = true;
  settingsError.value = '';
  try {
    const response = await browser.runtime.sendMessage({
      type: 'settings:set-registration-campaigns',
      enabled: wanted,
    });
    if (response?.error) throw new Error(response.error);
    // Render what the server stored, not what was requested.
    registrationCampaigns.value = response?.settings?.registration_campaigns_enabled ?? null;
  } catch {
    settingsError.value = t.value.popup.registrationCampaignsFailed;
    // Put the checkbox back: nothing changed server-side.
    registrationCampaigns.value = !wanted;
  } finally {
    registrationBusy.value = false;
  }
}
const status = ref<'loading' | 'ready' | 'signing-in'>('loading');
const error = ref('');
async function refreshUser() {
  status.value = 'loading';
  const response = await browser.runtime.sendMessage({ type: 'auth:get' });
  user.value = response?.user ?? null;
  status.value = 'ready';
  if (user.value) void loadSettings();
  else registrationCampaigns.value = null;
}
async function login() {
  status.value = 'signing-in';
  error.value = '';
  try {
    const response = await browser.runtime.sendMessage({ type: 'auth:sign-in' });
    if (response?.error) throw new Error(response.error);
    user.value = response?.user ?? null;
    if (user.value) void loadSettings();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t.value.popup.signInFailed;
  } finally {
    status.value = 'ready';
  }
}
async function logout() {
  error.value = '';
  const response = await browser.runtime.sendMessage({ type: 'auth:sign-out' });
  if (response?.error) error.value = response.error;
  else {
    user.value = null;
    registrationCampaigns.value = null;
    settingsError.value = '';
  }
}
onMounted(() => {
  void refreshUser();
  // Reflect what is stored, so the select does not claim "follow browser" when a
  // choice was already made.
  void (async () => {
    const stored = await browser.storage.local.get('locale');
    choice.value = isLocale(stored.locale) ? stored.locale : '';
    effective.value = await popupLocale();
  })();
});
</script>

<template>
  <main>
    <h1>{{ t.brandName }}</h1>
    <label class="language">
      <span>{{ t.popup.language }}</span>
      <select v-model="choice" @change="applyLanguage">
        <option value="">{{ t.popup.languageAuto }}</option>
        <option value="zh-TW">繁體中文</option>
        <option value="en-US">English</option>
      </select>
    </label>
    <p v-if="status === 'loading'">{{ t.popup.checkingSignIn }}</p>
    <template v-else-if="user">
      <div class="account">
        <img v-if="user.photoURL" :src="user.photoURL" alt="">
        <div><strong>{{ user.displayName || t.popup.genericUser }}</strong><small>{{ user.email }}</small></div>
      </div>
      <p>{{ t.popup.signedInHint }}</p>

      <!-- Server state, so it appears only once read: rendering an unchecked box
           while the value is unknown would misstate the account's setting. -->
      <div v-if="registrationCampaigns !== null" class="setting">
        <label class="setting__row">
          <input
            type="checkbox"
            :checked="registrationCampaigns"
            :disabled="registrationBusy"
            :aria-busy="registrationBusy"
            aria-describedby="registration-hint"
            @change="toggleRegistrationCampaigns"
          />
          <span>{{ t.popup.registrationCampaigns }}</span>
        </label>
        <p id="registration-hint" class="setting__hint">{{ t.popup.registrationCampaignsHint }}</p>
        <p v-if="settingsError" class="error" role="alert">{{ settingsError }}</p>
      </div>

      <button @click="logout">{{ t.popup.signOut }}</button>
    </template>
    <template v-else>
      <p>{{ t.popup.signedOutHint }}</p>
      <button :disabled="status === 'signing-in'" @click="login">{{ status === 'signing-in' ? t.popup.signingIn : t.popup.signIn }}</button>
    </template>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </main>
</template>

<style>
body { margin: 0; width: 300px; font: 14px/1.7 system-ui, sans-serif; color: #18312b; background: #f5f8f4; }
main { padding: 24px; } h1 { margin: 4px 0 12px; } span { font-size: 11px; color: #806019; }
button { width: 100%; padding: 12px; background: #1b5946; border: 0; border-radius: 8px; color: white; cursor: pointer; font: inherit; }
button:disabled { cursor: wait; opacity: .65; }
.account { display: flex; align-items: center; gap: 10px; margin: 14px 0; padding: 10px; border-radius: 10px; background: white; }
.account img { width: 36px; height: 36px; border-radius: 50%; }
.language { display: grid; gap: 4px; margin: 12px 0 4px; font-size: 12px; color: #4c5f58; }
.language select { padding: 7px 8px; border: 1px solid #cfdcd6; border-radius: 7px; background: #fff; font: inherit; color: inherit; }
.setting { margin: 14px 0; padding: 12px; border-radius: 10px; background: #fff; }
.setting__row { display: flex; align-items: flex-start; gap: 8px; font-size: 13px; line-height: 1.5; }
.setting__row input { margin: 3px 0 0; flex: 0 0 auto; accent-color: #1b5946; }
.setting__row input:disabled { cursor: wait; }
.setting__hint { margin: 6px 0 0; color: #65776b; font-size: 11px; line-height: 1.55; }
.account div { display: grid; min-width: 0; }
.account small { overflow: hidden; margin: 0; text-overflow: ellipsis; white-space: nowrap; }
.error { color: #a22c22; font-size: 12px; }
</style>
