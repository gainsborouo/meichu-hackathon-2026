import { createApp, h, shallowRef } from 'vue';
import RecommendationPanel from '../components/RecommendationPanel.vue';
import panelCss from '../components/panel.css?inline';
import { inspectAdapter, type CheckoutAdapter } from '../adapters';
import { requestRecommendation } from './api';
import { createController } from './controller';
import { diagnostic } from './diagnostics';
import type { CheckoutRequest, PanelState } from './types';

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
  let adapter = resolveAdapter();
  let mountedAfter: Element | null = null;
  let observed: Element[] = [];
  let scheduled: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  const controller = createController({
    inspect: () => inspectAdapter(adapter),
    request: request => { onRequest?.(request); return requestRecommendation(request); },
    render: next => {
      state.value = next;
      if (next.status === 'hidden') host.remove();
      else if (mountedAfter?.isConnected && !host.isConnected) mountedAfter.after(host);
    },
  });
  const app = createApp({ render: () => h(RecommendationPanel, {
    state: state.value, onRetry: controller.retry,
  }) });
  app.mount(root);

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
