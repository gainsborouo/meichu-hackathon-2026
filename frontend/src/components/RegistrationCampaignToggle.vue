<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useI18n } from 'vue-i18n'

import { api } from '../services/api'
import { useAuthStore } from '../stores/authStore'

interface UserRead {
  registration_campaigns_enabled: boolean
}

withDefaults(defineProps<{ tone?: 'default' | 'form' }>(), { tone: 'default' })

const emit = defineEmits<{
  'busy-change': [busy: boolean]
  'update-start': []
  updated: [enabled: boolean]
  'update-failed': []
}>()

const authStore = useAuthStore()
const { ready: authReady, user: authUser } = storeToRefs(authStore)
const { t } = useI18n()
const enabled = ref(false)
const loaded = ref(false)
const saving = ref(false)
const errorKey = ref('')

const disabled = computed(() => !authUser.value || !loaded.value || saving.value)
const error = computed(() => (errorKey.value ? t(errorKey.value) : ''))

async function load() {
  loaded.value = false
  errorKey.value = ''

  if (!authReady.value || !authUser.value) return

  try {
    const { data } = await api.get<UserRead>('/me')
    enabled.value = data.registration_campaigns_enabled
    loaded.value = true
  } catch {
    errorKey.value = 'recommendations.registrationPreferenceLoadFailed'
  }
}

async function update(nextValue: boolean) {
  if (disabled.value) return

  const previousValue = enabled.value
  let savedValue: boolean | null = null

  enabled.value = nextValue
  saving.value = true
  errorKey.value = ''
  emit('busy-change', true)
  emit('update-start')

  try {
    const { data } = await api.patch<UserRead>('/me', {
      registration_campaigns_enabled: nextValue,
    })
    savedValue = data.registration_campaigns_enabled
    enabled.value = savedValue
  } catch {
    enabled.value = previousValue
    errorKey.value = 'recommendations.registrationPreferenceUpdateFailed'
  }

  saving.value = false
  emit('busy-change', false)

  if (savedValue === null) emit('update-failed')
  else emit('updated', savedValue)
}

watch([authReady, authUser], () => void load(), { immediate: true })
</script>

<template>
  <div
    class="registration-preference"
    :class="{ 'registration-preference--form': tone === 'form' }"
  >
    <label class="registration-preference__label" for="registration-campaigns-toggle">
      {{ t('recommendations.registrationPreferenceLabel') }}
    </label>
    <button
      id="registration-campaigns-toggle"
      class="registration-switch"
      type="button"
      role="switch"
      :aria-checked="enabled"
      :disabled="disabled"
      :aria-busy="!loaded || saving"
      :aria-describedby="error ? 'registration-preference-message' : undefined"
      @click="update(!enabled)"
    >
      <span class="registration-switch__track" aria-hidden="true"></span>
    </button>
    <p
      v-if="error"
      id="registration-preference-message"
      class="registration-preference__message"
      :class="{ 'registration-preference__message--error': error }"
      :role="error ? 'alert' : undefined"
      data-testid="registration-preference-error"
    >
      {{ error }}
    </p>
  </div>
</template>

<style scoped>
.registration-preference {
  --preference-ink: var(--color-ink);
  --preference-muted: var(--color-muted);
  --preference-error: var(--color-error);
  --preference-border: var(--color-rule-strong);
  --preference-track: var(--color-paper);
  --preference-knob: var(--color-ink-2);
  --preference-active: var(--color-action);
  --preference-active-knob: var(--color-action-ink);
  --preference-focus: var(--color-focus);

  display: grid;
  min-width: 0;
  gap: var(--space-xs);
  color: var(--preference-ink);
}

.registration-preference--form {
  --preference-ink: var(--color-form-ink);
  --preference-muted: var(--color-form-muted);
  --preference-error: var(--color-form-error);
  --preference-border: var(--color-form-rule-strong);
  --preference-track: var(--color-form-surface-raised);
  --preference-knob: var(--color-form-accent-ink);
  --preference-active: var(--color-form-submit);
  --preference-active-knob: var(--color-form-accent-ink);
  --preference-focus: var(--color-form-accent);
}

.registration-preference__label {
  font-size: var(--text-sm);
  font-weight: 700;
}

.registration-preference--form .registration-preference__label {
  font-weight: 600;
}

.registration-switch {
  position: relative;
  display: inline-flex;
  width: 3rem;
  min-height: var(--control-height);
  padding: 0;
  border: 0;
  align-items: center;
  background: transparent;
  cursor: pointer;
}

.registration-switch__track {
  position: relative;
  width: 3rem;
  height: 1.75rem;
  border: var(--rule-hairline) solid var(--preference-border);
  border-radius: var(--radius-pill);
  background: var(--preference-track);
  transition:
    border-color var(--dur-short) var(--ease-out),
    background-color var(--dur-short) var(--ease-out);
}

.registration-switch__track::after {
  position: absolute;
  top: 0.2rem;
  left: 0.2rem;
  width: 1.25rem;
  height: 1.25rem;
  border-radius: 50%;
  background: var(--preference-knob);
  content: '';
  transition: transform var(--dur-short) var(--ease-out);
}

.registration-switch[aria-checked='true'] .registration-switch__track {
  border-color: var(--preference-active);
  background: var(--preference-active);
}

.registration-switch[aria-checked='true'] .registration-switch__track::after {
  background: var(--preference-active-knob);
  transform: translateX(1.3rem);
}

.registration-switch:focus-visible .registration-switch__track {
  outline: var(--rule-focus) solid var(--preference-focus);
  outline-offset: var(--rule-hairline);
}

.registration-switch:disabled {
  cursor: not-allowed;
}

.registration-switch:disabled .registration-switch__track {
  cursor: not-allowed;
  opacity: 0.55;
}

.registration-preference__message {
  min-height: 1.5em;
  color: var(--preference-muted);
  font-size: var(--text-xs);
}

.registration-preference__message--error {
  color: var(--preference-error);
}

@media (prefers-reduced-motion: reduce) {
  .registration-switch__track,
  .registration-switch__track::after {
    transition-duration: var(--dur-reduced);
  }
}
</style>
