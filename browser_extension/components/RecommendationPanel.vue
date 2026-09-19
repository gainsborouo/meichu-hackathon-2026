<script setup lang="ts">
import { computed } from 'vue';
import type { EstimatedReward, FailureReason, PanelState, UnavailableReason } from '../lib/types';

const props = defineProps<{ state: PanelState }>();
defineEmits<{ retry: [] }>();
const currency = (value: number) => new Intl.NumberFormat('zh-TW', { style: 'currency', currency: 'TWD' }).format(value);

// The backend returns a card name, not a last-four -- it never sees card numbers.
const cardLabel = computed(() => {
  if (props.state.status !== 'success') return '';
  const { bank_name: bank, name } = props.state.result.card;
  return bank ? `${bank} ${name}` : name;
});

// A card whose campaign terms carry no machine-readable rate still gets
// recommended, with no estimate -- so show the reason instead of a fake number.
const reward = computed<EstimatedReward | null>(() =>
  props.state.status === 'success' ? props.state.result.estimated_reward : null);

// `action: null` hides the retry button. Retrying changes nothing until the user
// has signed in or added a card, and a button labelled "重新推薦" next to "請先登入"
// reads as if signing in had already happened -- so the panel states what to do
// and offers no action it cannot deliver. Interactive sign-in must be started
// from the toolbar popup: background.ts only accepts `auth:sign-in` from an
// extension page, so a checkout-page button could not trigger it anyway.
const failure: Record<FailureReason, { headline: string; message: string; action: string | null }> = {
  'signed-out': {
    headline: '尚未登入',
    message: '請點擊瀏覽器工具列的「刷哪張」圖示並使用 Google 登入，登入後回到此頁即可取得推薦。',
    action: null,
  },
  'no-cards': {
    headline: '卡包還沒有信用卡',
    message: '已登入，但帳號中沒有可比較的信用卡。請先加入你持有的信用卡，再回到此頁。',
    action: null,
  },
  failed: {
    headline: '暫時無法取得推薦',
    message: '連線或伺服器發生問題，稍後再試一次。',
    action: '重新推薦',
  },
};

// Null hides the button entirely: only a transient failure is worth retrying
// from here, and a success can always be refreshed.
const retryLabel = computed(() => {
  if (props.state.status === 'success') return '重新推薦';
  if (props.state.status === 'error') return failure[props.state.reason].action;
  return null;
});
const unavailable: Record<UnavailableReason, string> = {
  'credit-unavailable': '此頁沒有可用的信用卡付款選項，未發送推薦請求。',
  'product-missing': '此頁尚無法確認商品，未發送推薦請求。',
  'amount-missing': '此頁尚無法確認應付金額，未發送推薦請求。',
};

</script>

<template>
  <div v-if="state.status !== 'hidden'" class="reward-shell">
    <aside class="reward-panel" aria-label="信用卡推薦">
      <header>
        <div class="brand">
          <span class="brand-mark" aria-hidden="true">卡</span>
          <div><strong>刷哪張</strong><span class="subtitle">信用卡回饋助手</span></div>
        </div>
      </header>
      <div class="panel-body">
        <template v-if="state.status !== 'unavailable'">
          <div class="context"><span>{{ state.context.platform }}</span><strong>{{ currency(state.context.payable) }}</strong></div>
          <p class="product">{{ state.context.product }}</p>
        </template>
        <div aria-live="polite" role="status">
          <template v-if="state.status === 'unavailable'"><p class="message">{{ unavailable[state.reason] }}</p></template>
          <template v-else-if="state.status === 'loading'">
            <p class="headline">正在挑選信用卡…</p><p class="message">正在比較你持有的信用卡回饋。</p>
          </template>
          <template v-else-if="state.status === 'success'">
            <div class="credit-card">
              <span>建議使用這張信用卡</span>
              <strong>{{ cardLabel }}</strong>
              <span v-if="reward">預估回饋 {{ currency(reward.amount) }}</span>
              <span v-else>此卡回饋條件無法自動計算</span>
            </div>
            <p v-if="reward?.requires_registration" class="message">這張卡的回饋需要先登錄活動。</p>
            <p class="message">{{ state.result.reason }}</p>
          </template>
          <template v-else>
            <p class="headline">{{ failure[state.reason].headline }}</p>
            <p class="message">{{ failure[state.reason].message }}</p>
          </template>
        </div>
        <button
          v-if="retryLabel"
          class="retry"
          @click="$emit('retry')"
        >
          {{ retryLabel }}
        </button>
      </div>
    </aside>
  </div>
</template>
