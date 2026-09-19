<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { browser } from 'wxt/browser';
import type { AuthUser } from '../../lib/auth';

const user = ref<AuthUser | null>(null);
const status = ref<'loading' | 'ready' | 'signing-in'>('loading');
const error = ref('');
async function refreshUser() {
  status.value = 'loading';
  const response = await browser.runtime.sendMessage({ type: 'auth:get' });
  user.value = response?.user ?? null;
  status.value = 'ready';
}
async function login() {
  status.value = 'signing-in';
  error.value = '';
  try {
    const response = await browser.runtime.sendMessage({ type: 'auth:sign-in' });
    if (response?.error) throw new Error(response.error);
    user.value = response?.user ?? null;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'Google 登入失敗';
  } finally {
    status.value = 'ready';
  }
}
async function logout() {
  error.value = '';
  const response = await browser.runtime.sendMessage({ type: 'auth:sign-out' });
  if (response?.error) error.value = response.error;
  else user.value = null;
}
onMounted(() => { void refreshUser(); });
</script>

<template>
  <main>
    <h1>刷哪張</h1>
    <p v-if="status === 'loading'">正在確認登入狀態…</p>
    <template v-else-if="user">
      <div class="account">
        <img v-if="user.photoURL" :src="user.photoURL" alt="">
        <div><strong>{{ user.displayName || 'Google 使用者' }}</strong><small>{{ user.email }}</small></div>
      </div>
      <p>已登入。在支援的購物頁結帳時會顯示信用卡推薦。</p>
      <button @click="logout">登出</button>
    </template>
    <template v-else>
      <p>登入後才能在支援的購物頁取得信用卡推薦。</p>
      <button :disabled="status === 'signing-in'" @click="login">{{ status === 'signing-in' ? '正在開啟 Google…' : '使用 Google 登入' }}</button>
    </template>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <small>各平台目前的驗證狀態與限制請見專案 README。</small>
  </main>
</template>

<style>
body { margin: 0; width: 300px; font: 14px/1.7 system-ui, sans-serif; color: #18312b; background: #f5f8f4; }
main { padding: 24px; } h1 { margin: 4px 0 12px; } span { font-size: 11px; color: #806019; }
button { width: 100%; padding: 12px; background: #1b5946; border: 0; border-radius: 8px; color: white; cursor: pointer; font: inherit; }
button:disabled { cursor: wait; opacity: .65; }
.account { display: flex; align-items: center; gap: 10px; margin: 14px 0; padding: 10px; border-radius: 10px; background: white; }
.account img { width: 36px; height: 36px; border-radius: 50%; }
.account div { display: grid; min-width: 0; }
.account small { overflow: hidden; margin: 0; text-overflow: ellipsis; white-space: nowrap; }
.error { color: #a22c22; font-size: 12px; }
small { display: block; color: #65776b; margin-top: 16px; }
</style>
