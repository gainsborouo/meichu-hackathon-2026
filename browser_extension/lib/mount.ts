import { createApp, h, shallowRef } from 'vue';
import { browser } from 'wxt/browser';
import RecommendationPanel from '../components/RecommendationPanel.vue';
import panelCss from '../components/panel.css?inline';
import { inspectAdapter, type CheckoutAdapter } from '../adapters';
import { createReminder, requestRecommendation } from './api';
import { createController } from './controller';
import { diagnostic } from './diagnostics';
import { currentLocale, isLocale, pageLocale, rememberPageLocale, resolveLocale } from './i18n';
import type { Locale } from './i18n';
import type {
  CheckoutRequest,
  PanelState,
  ReminderState,
  SearchStage,
  WaitSuggestion,
} from './types';

export function mountRecommendation(
  resolveAdapter: () => CheckoutAdapter | null,
  onRequest?: (request: CheckoutRequest) => void,
) {
  const host = document.createElement('card-reward-assistant');
  host.style.setProperty('all', 'initial', 'important');
  host.style.setProperty('display', 'block', 'important');
  const shadow = host.attachShadow({ mode: 'open' });
  const style = document.createElement('style');
  style.textContent = panelCss;
  const root = document.createElement('div');
  shadow.append(style, root);
  const state = shallowRef<PanelState>({ status: 'hidden' });
  // The page's own language is the default: someone reading a Chinese checkout
  // wants Chinese advice even if their browser is English. An explicit choice in
  // the popup overrides it.
  // Resolved here, in the content script, where both the page's `lang` and the
  // page's `navigator` are available. This exact value is what travels to the
  // backend (see inspectAdapter), so its prose and this copy cannot disagree.
  const fallbackLocale = pageLocale() ?? resolveLocale();
  const locale = shallowRef<Locale>(fallbackLocale);
  // The popup has no page to follow, so record this one for it. Only a language the
  // page actually declared: our own navigator fallback is already what the popup
  // would use anyway, and storing it would make a guess look like a fact.
  const declared = pageLocale();
  if (declared) void rememberPageLocale(declared);
  let adapter = resolveAdapter();
  let mountedAfter: Element | null = null;
  let observed: Element[] = [];
  let scheduled: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  const controller = createController({
    // `locale.value` rather than the page default: an explicit choice in the popup
    // must reach the backend too, or the reason would come back in the page's
    // language while the panel renders in the chosen one.
    inspect: () => inspectAdapter(adapter, locale.value),
    request: request => { onRequest?.(request); return requestRecommendation(request); },
    render: next => {
      // A new pick is a new decision, so a previous reminder result must not
      // linger beside it.
      if (next.status !== state.value.status || next.status === 'loading') {
        reminder.value = { status: 'idle' };
      }
      state.value = next;
      if (next.status === 'hidden') host.remove();
      else if (mountedAfter?.isConnected && !host.isConnected) mountedAfter.after(host);
    },
  });
  // Owned here, not in the panel: this layer makes the request, so it holds the
  // result. Reset whenever a new recommendation arrives.
  const reminder = shallowRef<ReminderState>({ status: 'idle' });

  async function onRemind(wait: WaitSuggestion) {
    reminder.value = { status: 'saving' };
    reminder.value = await createReminder(wait);
  }

  const app = createApp({ render: () => h(RecommendationPanel, {
    state: state.value,
    locale: locale.value,
    reminder: reminder.value,
    onRetry: controller.retry,
    onRemind,
  }) });
  app.mount(root);

  // A shown card mixes our copy with prose the backend wrote in the language the
  // request asked for, so changing language must refetch rather than re-render --
  // otherwise the card keeps last request's `reason` beside freshly translated
  // labels, which is the mismatch this exists to prevent.
  function applyLocale(next: Locale) {
    if (next === locale.value) return;
    locale.value = next;
    // Only states that carry backend prose need new data; the rest just re-render.
    if (state.value.status === 'success' || state.value.status === 'loading') controller.retry();
  }
  void currentLocale(fallbackLocale).then(applyLocale);

  function onStorageChange(changes: Record<string, { newValue?: unknown }>) {
    if (!('locale' in changes)) return;
    const next = changes.locale?.newValue;
    applyLocale(isLocale(next) ? next : fallbackLocale);
  }
  browser.storage.local.onChanged.addListener(onStorageChange);

  // Progress pushed by the background for the request this page started.
  function onRuntimeMessage(message: unknown) {
    const data = message as { type?: string; requestId?: string; stage?: string } | null;
    if (data?.type !== 'recommend:stage') return;
    if (typeof data.requestId !== 'string' || typeof data.stage !== 'string') return;
    controller.reportStage(data.requestId, data.stage as SearchStage);
  }
  browser.runtime.onMessage.addListener(onRuntimeMessage);

  function synchronize() {
    if (disposed) return;
    adapter = resolveAdapter();
    const nextMount = adapter?.isCheckoutPage() ? adapter.getRecommendationMountPoint() : null;
    if (mountedAfter !== nextMount) {
      mountedAfter = nextMount;
      host.remove();
      if (nextMount) diagnostic('ui', 'mount-point-found');
    }
    if (nextMount && state.value.status !== 'hidden' && !host.isConnected) nextMount.after(host);
    const roots = adapter?.getObservationRoots() ?? [];
    if (roots.length !== observed.length || roots.some((node, i) => node !== observed[i])) {
      observer.disconnect();
      observed = roots;
      for (const node of roots) observer.observe(node, {
        subtree: true, childList: true, characterData: true, attributes: true,
        attributeFilter: ['hidden', 'disabled', 'aria-disabled', 'aria-hidden', 'class', 'style', 'checked'],
      });
    }
    controller.refresh();
  }
  function schedule() {
    if (scheduled !== undefined) return;
    scheduled = setTimeout(() => { scheduled = undefined; synchronize(); }, 150);
  }
  const observer = new MutationObserver(records => {
    // A site's payment block may contain the insertion point; don't observe ourselves.
    if (records.some(record => record.target !== host && !host.contains(record.target)
      && !(record.type === 'childList' && [...record.addedNodes, ...record.removedNodes].every(node => node === host)))) schedule();
  });
  const onChange = (event: Event) => {
    if (event.target instanceof Node && observed.some(node => node.contains(event.target as Node))) schedule();
  };
  document.addEventListener('change', onChange, true);
  document.addEventListener('input', onChange, true);
  // A bounded check rediscovers replaced roots/routes/ancestor visibility. Adapters
  // use a few known IDs/semantic queries, never full-document text/style scans.
  const timer = setInterval(synchronize, 1000);
  synchronize();
  return {
    refresh: synchronize,
    // Forces a fresh request for the current checkout state, bypassing the
    // dedupe that would otherwise treat this as an unchanged page.
    retry: controller.retry,
    dispose() {
      disposed = true;
      browser.storage.local.onChanged.removeListener(onStorageChange);
      browser.runtime.onMessage.removeListener(onRuntimeMessage);
      observer.disconnect();
      clearTimeout(scheduled);
      clearInterval(timer);
      document.removeEventListener('change', onChange, true);
      document.removeEventListener('input', onChange, true);
      controller.dispose();
      app.unmount();
      host.remove();
    },
  };
}
