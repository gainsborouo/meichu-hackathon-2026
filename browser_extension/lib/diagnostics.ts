// Status codes only. Never pass DOM nodes, requests, products, URLs or values.
export function diagnostic(event: string, status: string): void {
  if (import.meta.env.DEV && import.meta.env.MODE !== 'test') console.debug('[刷哪張]', event, status);
}
