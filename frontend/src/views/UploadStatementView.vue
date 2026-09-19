<script setup lang="ts">
import { ref } from 'vue'
import { CircleCheck, FileText, FileUp, LoaderCircle, TriangleAlert } from '@lucide/vue'
import SiteHeader from '../components/SiteHeader.vue'
import { api } from '../services/api'

type UploadState = 'idle' | 'uploading' | 'success' | 'error'

const selectedFiles = ref<File[]>([])
const uploadState = ref<UploadState>('idle')
const message = ref('')

function handleFileSelection(event: Event) {
  const input = event.target as HTMLInputElement

  selectedFiles.value = Array.from(input.files ?? [])
  uploadState.value = 'idle'
  message.value = ''
}

async function uploadStatement() {
  if (selectedFiles.value.length === 0) {
    uploadState.value = 'error'
    message.value = '請先選擇帳單檔案。'
    return
  }

  uploadState.value = 'uploading'
  message.value = ''

  try {
    const formData = new FormData()

    selectedFiles.value.forEach((file) => formData.append('files', file))
    await api.post('/me/statements', formData)

    uploadState.value = 'success'
    message.value = `已上傳 ${selectedFiles.value.length} 份帳單。`
  } catch {
    uploadState.value = 'error'
    message.value = '上傳失敗，請稍後再試。'
  }
}
</script>

<template>
  <div class="upload-page">
    <SiteHeader current="upload-statement" />

    <main class="upload-workspace">
      <header class="upload-intro">
        <div>
          <h1>上傳帳單</h1>
          <p>選擇電子帳單檔案，送出後交由系統解析。</p>
        </div>
      </header>

      <form class="upload-panel" :data-state="uploadState" @submit.prevent="uploadStatement">
        <header class="upload-panel__header">
          <div>
            <h2>電子帳單檔案</h2>
            <p>可一次選擇多個檔案。</p>
          </div>
          <FileUp :size="22" :stroke-width="1.8" aria-hidden="true" />
        </header>

        <div class="upload-panel__body">
          <label
            class="upload-dropzone"
            :class="{ 'upload-dropzone--selected': selectedFiles.length > 0 }"
            for="statement-file"
          >
            <FileText :size="30" :stroke-width="1.6" aria-hidden="true" />
            <span class="upload-dropzone__copy">
              <strong>
                {{
                  selectedFiles.length ? `已選擇 ${selectedFiles.length} 個檔案` : '選擇電子帳單'
                }}
              </strong>
              <span>
                {{
                  selectedFiles.length
                    ? selectedFiles.map((file) => file.name).join('、')
                    : '將檔案拖曳至此，或點選瀏覽檔案'
                }}
              </span>
            </span>
            <input
              id="statement-file"
              name="files"
              type="file"
              multiple
              @change="handleFileSelection"
            />
          </label>

          <div class="upload-actions">
            <button
              class="upload-submit"
              type="submit"
              :data-state="uploadState"
              :disabled="selectedFiles.length === 0 || uploadState === 'uploading'"
              :aria-busy="uploadState === 'uploading'"
            >
              <LoaderCircle
                v-if="uploadState === 'uploading'"
                class="upload-submit__spinner"
                :size="19"
                aria-hidden="true"
              />
              <FileUp v-else :size="19" aria-hidden="true" />
              {{ uploadState === 'uploading' ? '上傳中' : '上傳帳單' }}
            </button>

            <p
              v-if="message"
              class="upload-message"
              :class="`upload-message--${uploadState}`"
              :role="uploadState === 'error' ? 'alert' : 'status'"
              aria-live="polite"
            >
              <CircleCheck v-if="uploadState === 'success'" :size="18" aria-hidden="true" />
              <TriangleAlert v-else :size="18" aria-hidden="true" />
              <span>{{ message }}</span>
            </p>
          </div>
        </div>
      </form>
    </main>

    <footer class="page-footer">
      <div class="page-footer__meta">
        <span>信用卡推薦</span>
        <span>© 2026 Meichu Hackathon @ Google</span>
      </div>
    </footer>
  </div>
</template>

<style scoped>
/* Hallmark · pre-emit critique: P4 H4 E4 S5 R5 V4
 * Hallmark · genre: modern-minimal · macrostructure: Component Playground · theme: Cobalt
 * tone: 實用、柔和 · anchor hue: cobalt 256 · nav: N9 · footer: Ft5
 * contrast: pass (40–41) · slop: pass (42–45) · honest: pass (46)
 * chrome: pass (47) · tokens: pass (48) · responsive: pass (49)
 * icons: pass (30) · mobile: pass (34, 49, 50–57)
 * states: default · hover · focus · active · disabled · loading · error · success
 */
.upload-page {
  min-height: 100dvh;
  background: var(--color-paper);
  color: var(--color-ink);
}

.upload-workspace,
.page-footer {
  width: min(100% - (var(--space-lg) * 2), var(--layout-max));
  margin-inline: auto;
}

.upload-workspace {
  display: grid;
  gap: var(--space-2xl);
  padding-block: var(--space-xl) var(--space-3xl);
}

.upload-intro {
  display: grid;
  gap: var(--space-lg);
  border-bottom: var(--rule-hairline) solid var(--color-rule);
  padding-block-end: var(--space-xl);
}

.upload-intro h1 {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  font-family: var(--font-display);
  font-size: clamp(2rem, 4vw, 3.25rem);
  font-style: normal;
  font-weight: 700;
  letter-spacing: -0.035em;
  line-height: 1.05;
}

.upload-intro > div > p {
  max-width: 54ch;
  margin: 0;
  margin-block-start: var(--space-sm);
  color: var(--color-ink-2);
  line-height: 1.6;
}

.upload-panel {
  display: grid;
  min-width: 0;
  gap: var(--space-md);
}

.upload-panel__header {
  display: flex;
  min-width: 0;
  align-items: end;
  justify-content: space-between;
  gap: var(--space-md);
  border-bottom: var(--rule-hairline) solid var(--color-rule);
  padding-block-end: var(--space-md);
}

.upload-panel__header h2,
.upload-panel__header p,
.upload-message {
  margin: 0;
}

.upload-panel__header h2 {
  font-family: var(--font-display);
  font-size: var(--text-lg);
  font-style: normal;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.upload-panel__header p {
  margin-top: var(--space-2xs);
  color: var(--color-muted);
  font-size: var(--text-sm);
}

.upload-panel__header > svg {
  flex: 0 0 auto;
  color: var(--color-accent);
}

.upload-panel__body {
  display: grid;
  min-width: 0;
  gap: var(--space-lg);
}

.upload-dropzone {
  position: relative;
  display: flex;
  min-height: 10rem;
  min-width: 0;
  align-items: center;
  gap: var(--space-lg);
  border: var(--rule-hairline) solid var(--color-rule-strong);
  border-radius: var(--radius-panel);
  outline: var(--rule-focus) solid transparent;
  outline-offset: var(--rule-hairline);
  padding: var(--space-lg);
  background: var(--color-paper-2);
  color: var(--color-ink);
  box-shadow: var(--shadow-panel);
  transition:
    background-color var(--dur-short) var(--ease-out),
    transform var(--dur-micro) var(--ease-out);
}

.upload-dropzone > svg {
  flex: 0 0 auto;
  color: var(--color-accent);
}

.upload-dropzone__copy {
  display: grid;
  min-width: 0;
  gap: var(--space-2xs);
}

.upload-dropzone__copy strong,
.upload-dropzone__copy span {
  max-width: 100%;
  overflow-wrap: anywhere;
}

.upload-dropzone__copy strong {
  font-weight: 600;
  line-height: 1.5;
}

.upload-dropzone__copy span {
  color: var(--color-muted);
  font-size: var(--text-sm);
  line-height: 1.5;
}

.upload-dropzone input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  cursor: pointer;
  opacity: 0;
}

.upload-dropzone:has(input:focus-visible) {
  outline-color: var(--color-focus);
  background: var(--color-paper);
}

.upload-dropzone:has(input:active) {
  transform: translateY(var(--rule-hairline));
}

.upload-dropzone--selected {
  border-color: var(--color-accent);
  background: var(--color-paper);
}

.upload-actions {
  display: flex;
  min-width: 0;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-md) var(--space-lg);
}

.upload-message {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: var(--space-xs);
  color: var(--color-muted);
  font-size: var(--text-sm);
  line-height: 1.5;
}

.upload-message--success {
  color: var(--color-success);
}

.upload-message--error {
  color: var(--color-error);
}

.upload-submit {
  display: inline-flex;
  min-height: var(--control-height);
  justify-self: start;
  align-items: center;
  justify-content: center;
  gap: var(--space-xs);
  border: var(--rule-hairline) solid var(--color-accent);
  border-radius: var(--radius-control);
  outline: var(--rule-focus) solid transparent;
  outline-offset: var(--rule-focus);
  padding-inline: var(--space-lg);
  background: var(--color-accent);
  color: var(--color-accent-ink);
  font-size: var(--text-base);
  font-weight: 600;
  white-space: nowrap;
  transition:
    background-color var(--dur-short) var(--ease-out),
    transform var(--dur-micro) var(--ease-out);
}

.upload-submit:focus-visible {
  outline-color: var(--color-focus);
}

.upload-submit:active {
  transform: translateY(var(--rule-hairline));
}

.upload-submit:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.upload-submit[data-state='error'] {
  border-color: var(--color-error);
}

.upload-submit[data-state='success'] {
  border-color: var(--color-success);
}

.upload-submit__spinner {
  animation: upload-spin 1s linear infinite;
}

.page-footer {
  display: grid;
  gap: var(--space-xl);
  padding-block: var(--space-2xl) var(--space-lg);
}

.page-footer__meta {
  display: flex;
  align-items: baseline;
  flex-direction: column;
  gap: var(--space-xs);
  border-top: var(--rule-hairline) solid var(--color-rule);
  padding-block-start: var(--space-sm);
  color: var(--color-muted);
  font-size: var(--text-xs);
}

@media (hover: hover) and (pointer: fine) {
  .upload-dropzone:hover {
    background: var(--color-paper);
  }

  .upload-submit:hover:not(:disabled) {
    background: var(--color-accent-hover);
  }
}

@media (min-width: 40rem) {
  .upload-workspace,
  .page-footer {
    width: min(100% - (var(--space-xl) * 2), var(--layout-max));
  }

  .page-footer__meta {
    flex-direction: row;
    justify-content: space-between;
  }
}

@media (min-width: 60rem) {
  .upload-intro {
    grid-template-columns: minmax(0, 1.4fr) minmax(0, 0.6fr);
  }
}

@keyframes upload-spin {
  to {
    transform: rotate(1turn);
  }
}

@media (prefers-reduced-motion: reduce) {
  .upload-dropzone,
  .upload-submit {
    transition-duration: var(--dur-reduced);
  }

  .upload-submit:active {
    transform: none;
  }

  .upload-submit__spinner {
    animation-duration: 1.8s;
  }
}
</style>
