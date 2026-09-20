<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const props = withDefaults(
  defineProps<{
    id: string
    modelValue: boolean
    tone?: 'default' | 'form'
  }>(),
  { tone: 'default' },
)

const emit = defineEmits<{ 'update:modelValue': [enabled: boolean] }>()
const { t } = useI18n()
</script>

<template>
  <div
    class="web-search-preference"
    :class="{ 'web-search-preference--form': props.tone === 'form' }"
  >
    <label class="web-search-preference__label" :for="props.id">
      {{ t('recommendations.webSearchLabel') }}
    </label>
    <button
      :id="props.id"
      class="web-search-switch"
      type="button"
      role="switch"
      :aria-checked="props.modelValue"
      @click="emit('update:modelValue', !props.modelValue)"
    >
      <span class="web-search-switch__track" aria-hidden="true"></span>
    </button>
  </div>
</template>

<style scoped>
.web-search-preference {
  --preference-ink: var(--color-ink);
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

.web-search-preference--form {
  --preference-ink: var(--color-form-ink);
  --preference-border: var(--color-form-rule-strong);
  --preference-track: var(--color-form-surface-raised);
  --preference-knob: var(--color-form-accent-ink);
  --preference-active: var(--color-form-submit);
  --preference-active-knob: var(--color-form-accent-ink);
  --preference-focus: var(--color-form-accent);
}

.web-search-preference__label {
  font-size: var(--text-sm);
  font-weight: 700;
}

.web-search-preference--form .web-search-preference__label {
  font-weight: 600;
}

.web-search-switch {
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

.web-search-switch__track {
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

.web-search-switch__track::after {
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

.web-search-switch[aria-checked='true'] .web-search-switch__track {
  border-color: var(--preference-active);
  background: var(--preference-active);
}

.web-search-switch[aria-checked='true'] .web-search-switch__track::after {
  background: var(--preference-active-knob);
  transform: translateX(1.3rem);
}

.web-search-switch:focus-visible .web-search-switch__track {
  outline: var(--rule-focus) solid var(--preference-focus);
  outline-offset: var(--rule-hairline);
}

@media (prefers-reduced-motion: reduce) {
  .web-search-switch__track,
  .web-search-switch__track::after {
    transition-duration: var(--dur-reduced);
  }
}
</style>
