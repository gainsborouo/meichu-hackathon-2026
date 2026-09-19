<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { CircleCheck, CircleX, FileText, FileUp, LoaderCircle, TriangleAlert } from '@lucide/vue'
import MarkdownIt from 'markdown-it'
import { storeToRefs } from 'pinia'
import SiteHeader from '../components/SiteHeader.vue'
import { api } from '../services/api'
import { useAuthStore } from '../stores/authStore'

type UploadState = 'idle' | 'uploading' | 'success' | 'error'
type AnalysisState = 'idle' | 'loading' | 'error'

const markdown = new MarkdownIt({ html: false, linkify: true })

markdown.renderer.rules.link_open = (tokens, index, options, _env, renderer) => {
  tokens[index]!.attrSet('target', '_blank')
  tokens[index]!.attrSet('rel', 'noopener noreferrer')
  return renderer.renderToken(tokens, index, options)
}
markdown.renderer.rules.image = (tokens, index) => markdown.utils.escapeHtml(tokens[index]!.content)

const authStore = useAuthStore()
const { ready: authReady, user: authUser } = storeToRefs(authStore)
const selectedFiles = ref<File[]>([])
const uploadState = ref<UploadState>('idle')
const message = ref('')
const analysisState = ref<AnalysisState>('idle')
const analysisError = ref('')
const analysisMarkdown = ref('')
const analysisDialog = ref<HTMLDialogElement | null>(null)
let analysisController: AbortController | null = null

const analysisHtml = computed(() => markdown.render(analysisMarkdown.value))
const analysisButtonLabel = computed(() => {
  if (!authReady.value) return '確認登入狀態…'
  if (!authUser.value) return '登入後查看'
  return analysisState.value === 'loading' ? '載入中' : '我的帳單分析'
})

onBeforeUnmount(() => analysisController?.abort())

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

async function openAnalysis() {
  if (!authReady.value || !authUser.value || analysisState.value === 'loading') return

  const controller = new AbortController()
  analysisController = controller
  analysisState.value = 'loading'
  analysisError.value = ''
  analysisMarkdown.value = ''

  try {
    const response = await api.get<string>('/me/statements', { signal: controller.signal })

    if (controller.signal.aborted) return
    if (typeof response.data !== 'string') throw new TypeError('Expected a Markdown string')

    analysisMarkdown.value = response.data
    analysisState.value = 'idle'
    analysisDialog.value?.showModal()
  } catch {
    if (controller.signal.aborted) return

    analysisState.value = 'error'
    analysisError.value = '無法取得帳單分析，請稍後再試。'
  } finally {
    if (analysisController === controller) analysisController = null
  }
}

function closeAnalysis() {
  analysisDialog.value?.close()
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

        <div class="analysis-entry">
          <button
            class="upload-submit analysis-trigger"
            type="button"
            :disabled="!authReady || !authUser || analysisState === 'loading'"
            :aria-busy="analysisState === 'loading'"
            @click="openAnalysis"
          >
            <LoaderCircle
              v-if="analysisState === 'loading'"
              class="analysis-trigger__spinner"
              :size="19"
              aria-hidden="true"
            />
            <FileText v-else :size="19" aria-hidden="true" />
            {{ analysisButtonLabel }}
          </button>
          <p
            v-if="analysisError"
            class="upload-message upload-message--error analysis-entry__error"
            role="alert"
          >
            {{ analysisError }}
          </p>
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

    <dialog
      ref="analysisDialog"
      class="analysis-dialog"
      aria-labelledby="analysis-dialog-title"
      @cancel.prevent="closeAnalysis"
      @click.self="closeAnalysis"
    >
      <div class="analysis-dialog__surface">
        <header class="analysis-dialog__header">
          <h2 id="analysis-dialog-title">帳單分析</h2>
          <button type="button" aria-label="關閉帳單分析" autofocus @click="closeAnalysis">
            <CircleX :size="22" aria-hidden="true" />
          </button>
        </header>

        <div class="analysis-dialog__body">
          <!-- Markdown 已停用原始 HTML，並限制圖片與危險連結。 -->
          <div v-if="analysisMarkdown.trim()" class="analysis-markdown" v-html="analysisHtml"></div>
          <p v-else class="analysis-empty">尚無統計數據，請先上傳帳單。</p>
        </div>
      </div>
    </dialog>

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

.analysis-entry {
  display: grid;
  min-width: 0;
  align-content: end;
  justify-items: start;
  gap: var(--space-xs);
}

.analysis-entry__error {
  max-width: 30ch;
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

.analysis-trigger__spinner {
  animation: upload-spin 1s linear infinite;
}

.analysis-dialog {
  width: min(calc(100% - (var(--space-xl) * 2)), 64rem);
  max-width: none;
  max-height: min(85dvh, 52rem);
  border: var(--rule-hairline) solid var(--color-rule-strong);
  border-radius: var(--radius-panel);
  padding: 0;
  overflow: hidden;
  background: var(--color-paper);
  color: var(--color-ink);
  box-shadow: 0 1.5rem 4rem oklch(20% 0.016 260 / 0.24);
}

.analysis-dialog::backdrop {
  background: oklch(20% 0.016 260 / 0.58);
}

.analysis-dialog__surface {
  display: grid;
  max-height: min(85dvh, 52rem);
  grid-template-rows: auto minmax(0, 1fr);
}

.analysis-dialog__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-lg);
  border-bottom: var(--rule-hairline) solid var(--color-rule);
  padding: var(--space-lg) var(--space-xl);
}

.analysis-dialog__header h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: var(--text-lg);
  letter-spacing: -0.02em;
}

.analysis-dialog__header button {
  display: inline-flex;
  width: var(--control-height);
  height: var(--control-height);
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: var(--radius-control);
  outline: var(--rule-focus) solid transparent;
  outline-offset: var(--rule-hairline);
  padding: 0;
  background: transparent;
  color: var(--color-muted);
}

.analysis-dialog__header button:focus-visible {
  outline-color: var(--color-focus);
}

.analysis-dialog__body {
  min-height: 0;
  padding: var(--space-xl);
  overflow: auto;
}

.analysis-empty {
  margin: 0;
  color: var(--color-muted);
  line-height: 1.6;
}

.analysis-markdown {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--color-ink-2);
  line-height: 1.7;
}

.analysis-markdown :deep(:first-child) {
  margin-block-start: 0;
}

.analysis-markdown :deep(:last-child) {
  margin-block-end: 0;
}

.analysis-markdown :deep(h1),
.analysis-markdown :deep(h2),
.analysis-markdown :deep(h3),
.analysis-markdown :deep(h4) {
  margin-block: var(--space-xl) var(--space-sm);
  color: var(--color-ink);
  font-family: var(--font-display);
  line-height: 1.25;
}

.analysis-markdown :deep(h1) {
  font-size: 1.75rem;
}

.analysis-markdown :deep(h2) {
  font-size: var(--text-lg);
}

.analysis-markdown :deep(h3),
.analysis-markdown :deep(h4) {
  font-size: var(--text-md);
}

.analysis-markdown :deep(p),
.analysis-markdown :deep(ul),
.analysis-markdown :deep(ol),
.analysis-markdown :deep(blockquote),
.analysis-markdown :deep(pre),
.analysis-markdown :deep(table) {
  margin-block: 0 var(--space-lg);
}

.analysis-markdown :deep(ul),
.analysis-markdown :deep(ol) {
  padding-inline-start: var(--space-xl);
}

.analysis-markdown :deep(table) {
  width: 100%;
  min-width: 34rem;
  border-collapse: collapse;
  font-size: var(--text-sm);
}

.analysis-markdown :deep(th),
.analysis-markdown :deep(td) {
  border: var(--rule-hairline) solid var(--color-rule);
  padding: var(--space-sm);
  text-align: start;
  vertical-align: top;
}

.analysis-markdown :deep(th) {
  background: var(--color-paper-2);
  color: var(--color-ink);
}

.analysis-markdown :deep(blockquote) {
  border-inline-start: 0.2rem solid var(--color-accent-light);
  padding-inline-start: var(--space-md);
  color: var(--color-muted);
}

.analysis-markdown :deep(pre) {
  border-radius: var(--radius-control);
  padding: var(--space-md);
  overflow: auto;
  background: var(--color-graphite);
  color: var(--color-graphite-ink);
}

.analysis-markdown :deep(code) {
  font-family: var(--font-mono);
}

.analysis-markdown :deep(:not(pre) > code) {
  border-radius: var(--radius-control);
  padding: var(--space-3xs) var(--space-2xs);
  background: var(--color-paper-2);
}

.analysis-markdown :deep(a) {
  color: var(--color-accent);
  text-decoration-thickness: var(--rule-hairline);
  text-underline-offset: var(--space-3xs);
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

  .analysis-dialog__header button:hover {
    background: var(--color-paper-2);
    color: var(--color-ink);
  }
}

@media (max-width: 39.999rem) {
  .analysis-dialog {
    width: calc(100% - (var(--space-md) * 2));
    max-height: calc(100dvh - (var(--space-md) * 2));
  }

  .analysis-dialog__surface {
    max-height: calc(100dvh - (var(--space-md) * 2));
  }

  .analysis-dialog__header,
  .analysis-dialog__body {
    padding: var(--space-md);
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

  .analysis-entry {
    justify-items: end;
  }

  .analysis-trigger {
    justify-self: end;
  }

  .analysis-entry__error {
    text-align: end;
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

  .upload-submit__spinner,
  .analysis-trigger__spinner {
    animation-duration: 1.8s;
  }
}
</style>
