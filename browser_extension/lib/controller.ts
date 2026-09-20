import { RecommendationError } from './types';
import type {
  CheckoutRequest,
  SearchStage,
  FailureReason,
  Inspection,
  PanelState,
  Recommendation,
  WaitSuggestion,
} from './types';
import { diagnostic } from './diagnostics';

interface Options {
  inspect: () => Inspection;
  request: (request: CheckoutRequest) => Promise<{ best: Recommendation; wait: WaitSuggestion | null }>;
  render: (state: PanelState) => void;
  createId?: () => string;
}

function reasonOf(error: unknown): FailureReason {
  return error instanceof RecommendationError ? error.reason : 'failed';
}

export function createController(options: Options) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let currentKey = '';
  let revision = 0;
  let disposed = false;
  let inspectionKey = '';
  // The request whose progress the panel is currently showing.
  let currentRequestId = '';
  // Retain results and in-flight work across temporary DOM loss in this checkout.
  // No persistent storage; leaving checkout clears the session.
  const requests = new Map<string, Promise<{ best: Recommendation; wait: WaitSuggestion | null }>>();
  const createId = options.createId ?? (() => crypto.randomUUID());
  // pageLocale is part of the identity, not incidental: the backend writes
  // `reason` and `cap_description` in that language, so the same purchase in
  // another language is a different answer and must not be served from cache.
  const fingerprint = (value: Inspection) => value.status === 'ready'
    ? JSON.stringify([
      value.context.platform,
      value.context.product,
      value.context.payable,
      value.context.pageLocale,
    ]) : '';

  async function recommend(expected: number, force: boolean) {
    if (disposed || expected !== revision) return;
    const snapshot = options.inspect();
    if (snapshot.status !== 'ready' || fingerprint(snapshot) !== currentKey) { refresh(); return; }
    const { context } = snapshot;
    const key = currentKey;
    try {
      if (force) requests.delete(key);
      let pending = requests.get(key);
      if (!pending) {
        const requestId = createId();
        currentRequestId = requestId;
        diagnostic('api', 'started');
        // requestId correlation happens in lib/api.ts, which rejects a reply
        // that does not match the request it sent.
        pending = options.request({ requestId, ...context }).then(result => {
          diagnostic('api', 'received');
          return result;
        });
        requests.set(key, pending);
      // Only a successful answer is worth reusing: a failed one must not be
      // replayed after the user signs in or adds a card.
      pending.catch(() => requests.delete(key));
      }
      const result = await pending;
      if (disposed || expected !== revision) return;
      const latest = options.inspect();
      if (latest.status !== 'ready' || fingerprint(latest) !== key) { refresh(); return; }
      options.render({ status: 'success', context, result: result.best, wait: result.wait });
    } catch (error) {
      diagnostic('api', 'failed');
      if (!disposed && expected === revision) {
        const latest = options.inspect();
        if (latest.status !== 'ready' || fingerprint(latest) !== key) refresh();
        else options.render({ status: 'error', context, reason: reasonOf(error) });
      }
    }
  }

  function refresh(force = false) {
    if (disposed) return;
    const snapshot = options.inspect();
    const key = fingerprint(snapshot);
    const nextInspectionKey = snapshot.status === 'ready' ? key
      : snapshot.status === 'outside' ? 'outside' : `${snapshot.status}:${snapshot.reason}`;
    if (!force && nextInspectionKey === inspectionKey) return;
    inspectionKey = nextInspectionKey;
    diagnostic('checkout', snapshot.status === 'ready' ? 'ready' : nextInspectionKey);
    currentKey = key;
    revision += 1;
    clearTimeout(timer);
    if (snapshot.status === 'outside') { requests.clear(); options.render({ status: 'hidden' }); return; }
    if (snapshot.status !== 'ready') { options.render({ status: 'unavailable', reason: snapshot.reason }); return; }
    options.render({ status: 'loading', context: snapshot.context });
    const expected = revision;
    timer = setTimeout(() => { void recommend(expected, force); }, 700);
  }

  return {
    refresh,
    retry: () => refresh(true),
    /**
     * Updates the loading state with the backend's current stage.
     *
     * Ignored unless a request for `requestId` is the one being shown, so a late
     * update from a superseded request cannot relabel the current one.
     */
    reportStage(requestId: string, stage: SearchStage) {
      if (disposed || requestId !== currentRequestId) return;
      const snapshot = options.inspect();
      if (snapshot.status !== 'ready' || fingerprint(snapshot) !== currentKey) return;
      options.render({ status: 'loading', context: snapshot.context, stage });
    },
    dispose: () => { disposed = true; revision += 1; clearTimeout(timer); requests.clear(); options.render({ status: 'hidden' }); },
  };
}
