<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { messages } from '../lib/i18n';
import type { Locale } from '../lib/i18n';
import type { PanelState, ReminderState, WaitSuggestion } from '../lib/types';

// `locale` is owned by lib/mount.ts, not resolved here. The card mixes this
// component's own copy with prose the backend wrote (`reason`,
// `cap_description`), and the backend's language was fixed when the request went
// out -- so switching language has to refetch, not just re-render, or the card
// would show two languages at once. Only the mount layer can refetch.
const props = defineProps<{ state: PanelState; locale: Locale; reminder: ReminderState }>();
const emit = defineEmits<{ retry: []; remind: [wait: WaitSuggestion] }>();

// Whether the user has chosen. Until they do, both options are offered; choosing
// "buy now" simply dismisses the suggestion, which needs no request.
const decided = ref(false);

const wait = computed(() =>
  props.state.status === 'success' ? (props.state.wait ?? null) : null);

// A new recommendation is a new decision, so the previous choice must not carry
// over -- otherwise a dismissed suggestion would stay hidden for a different purchase.
watch(
  () => (props.state.status === 'success' ? props.state.wait?.sale_id : undefined),
  () => { decided.value = false; },
);

const waitDate = computed(() => {
  const value = wait.value?.starts_at;
  if (!value) return '';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat(props.locale, { month: 'long', day: 'numeric' }).format(parsed);
});

const reminderMessage = computed(() => {
  const state = props.reminder;
  if (state.status === 'saving') return t.value.wait.saving;
  if (state.status === 'saved') {
    return state.alreadyNotified ? t.value.wait.savedAlready : t.value.wait.saved;
  }
  if (state.status === 'error') return t.value.wait[
    state.reason === 'not-connected' ? 'notConnected'
      : state.reason === 'signed-out' ? 'signedOut' : 'failed'
  ];
  return '';
});

function buyNow() {
  decided.value = true;
}

function remindMe() {
  if (!wait.value || props.reminder.status === 'saving') return;
  emit('remind', wait.value);
}

const t = computed(() => messages(props.locale));
const currency = (value: number) =>
  new Intl.NumberFormat(props.locale, { style: 'currency', currency: 'TWD' }).format(value);

// The backend returns a card name, not a last-four -- it never sees card numbers.
// Prefers the English name in an English UI so the card does not read as mixed
// script, and falls back when the catalog has no translation.
const cardLabel = computed(() => {
  if (props.state.status !== 'success') return '';
  const { bank_name: bank, name, issuer_en: bankEn, name_en: nameEn } = props.state.result.card;
  const english = props.locale === 'en-US';
  const cardName = (english && nameEn) || name;
  const issuer = english ? (bankEn ?? bank) : bank;
  return issuer ? `${issuer} ${cardName}` : cardName;
});


// Null hides the button entirely. Retrying changes nothing until the user has
// signed in or added a card, and a button next to "not signed in" would read as
// if signing in had already happened. Interactive sign-in must start from the
// toolbar popup anyway: background.ts only accepts `auth:sign-in` from an
// extension page, so a checkout-page button could not trigger it.
const retryLabel = computed(() => {
  if (props.state.status === 'success') return t.value.retry;
  if (props.state.status === 'error' && props.state.reason === 'failed') return t.value.retry;
  return null;
});

</script>

<template>
  <div v-if="state.status !== 'hidden'" class="reward-shell">
    <aside class="reward-panel" aria-label="信用卡推薦">
      <header>
        <div class="brand">
          <svg class="brand-mark" viewBox="0 0 128 128" aria-hidden="true">
            <rect width="128" height="128" rx="28" fill="#1b5946" />
            <path
              d="M26 38a10 10 0 0 1 10-10h56a10 10 0 0 1 10 10v52a10 10 0 0 1-10 10H36a10 10 0 0 1-10-10V38Z"
              fill="#fff"
            />
            <path d="M26 45h76v16H26V45Z" fill="#d9eee5" />
            <path d="M39 78h24" stroke="#1b5946" stroke-width="8" stroke-linecap="round" />
            <circle cx="86" cy="80" r="9" fill="#efbd4c" />
          </svg>
          <strong>{{ t.brandName }}</strong>
        </div>
      </header>
      <div class="panel-body">
        <template v-if="state.status !== 'unavailable'">
          <div class="context"><span>{{ state.context.platform }}</span><strong>{{ currency(state.context.payable) }}</strong></div>
          <p class="product">{{ state.context.product }}</p>
        </template>
        <div aria-live="polite" role="status">
          <template v-if="state.status === 'unavailable'"><p class="message">{{ t.unavailable[state.reason] }}</p></template>
          <template v-else-if="state.status === 'loading'">
            <!-- The backend verifies campaign terms against official pages and can
                 take up to two minutes, so say so rather than implying it stalled. -->
            <p class="headline">{{ t.loadingHeadline }}</p>
            <p class="message">
              {{ state.stage && state.stage !== 'preprocessing'
                ? t.loadingStage[state.stage]
                : t.loadingMessage }}
            </p>
          </template>
          <template v-else-if="state.status === 'success'">
            <div class="credit-card">
              <span>{{ t.successLabel }}</span>
              <strong>{{ cardLabel }}</strong>
              <span>{{ t.rewardEstimate(currency(state.result.estimated_reward_twd), state.result.rate_display) }}</span>
              <span v-if="state.result.cap_description" class="cap">{{ state.result.cap_description }}</span>
            </div>
            <p v-if="state.result.requires_registration" class="message">
              {{ t.registrationNeeded }}
              <!-- The registration page is the bank's own; opened in a new tab so
                   it never navigates away from the checkout the user is mid-way
                   through. rel prevents the opened page reaching back here. -->
              <a
                v-if="state.result.registration_url"
                :href="state.result.registration_url"
                target="_blank"
                rel="noopener noreferrer"
              >{{ t.registrationLink }}</a>
            </p>
            <!-- A standing reward and a limited-time campaign read the same in the
                 card above, so name the difference. -->
            <p v-if="state.result.candidate_type === 'base_benefit'" class="message">
              {{ t.baseBenefitNote }}
            </p>
            <p class="message">{{ state.result.reason }}</p>
            <!-- The backend could not confirm these terms against an official
                 bank page, so say so rather than presenting them as certain. -->
            <p v-if="state.result.verification_status !== 'verified'" class="message caution">
              {{ t.unverifiedNotice }}
            </p>

            <!-- Offered only until the user decides, and only when the backend
                 supplied a usable draft. "Buy now" needs no request: it just
                 dismisses the suggestion. -->
            <div v-if="wait && !decided" class="wait">
              <p class="wait__heading">{{ t.wait.heading }}</p>
              <p class="wait__extra">
                {{ t.wait.extra(currency(wait.estimated_extra_reward_twd), waitDate) }}
              </p>
              <p class="message">{{ wait.reason }}</p>
              <div class="wait__actions">
                <button class="wait__secondary" type="button" @click="buyNow">
                  {{ t.wait.buyNow }}
                </button>
                <button
                  type="button"
                  :disabled="reminder.status === 'saving'"
                  :aria-busy="reminder.status === 'saving'"
                  @click="remindMe"
                >
                  {{ reminder.status === 'saving' ? t.wait.saving : t.wait.remindMe }}
                </button>
              </div>
              <p
                v-if="reminderMessage"
                class="message"
                :class="{ caution: reminder.status === 'error' }"
                role="status"
              >
                {{ reminderMessage }}
              </p>
            </div>
          </template>
          <template v-else>
            <p class="headline">{{ t.failure[state.reason].headline }}</p>
            <p class="message">{{ t.failure[state.reason].message }}</p>
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
