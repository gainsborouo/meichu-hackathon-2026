export function text(element: Element | null): string {
  return (element?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

export function isVisible(element: Element | null): boolean {
  if (!element || element.closest('[hidden], [aria-hidden="true"], [inert]')) return false;
  const win = element.ownerDocument.defaultView;
  if (!win) return false;
  for (let current: Element | null = element; current; current = current.parentElement) {
    const style = win.getComputedStyle(current);
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
  }
  return true;
}

export function isAvailableControl(control: Element): boolean {
  if (control.matches(':disabled') || control.closest('[aria-disabled="true"], [inert]')) return false;
  // Custom radios often hide the native input while keeping its label visible.
  if (control.tagName === 'INPUT') {
    const labels = [...((control as HTMLInputElement).labels ?? [])];
    return labels.length ? labels.some(isVisible) : isVisible(control);
  }
  return isVisible(control);
}

export function parseAmount(raw: string): number | null {
  const input = raw.replace(/^(?:應付(?:總)?金額|總付款金額)\s*[:：]?\s*/, '')
    .replace(/^(?:NT\$|TWD|NTD|新臺幣|新台幣|\$)\s*/i, '').replace(/\s*元$/, '').trim();
  if (!/^(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?$/.test(input)) return null;
  const amount = Number(input.replaceAll(',', ''));
  return Number.isFinite(amount) && amount > 0 ? amount : null;
}

export function productString(names: string[]): string | null {
  if (!names.length || names.some(name => !name || name.length > 300)) return null;
  const product = names.join('、');
  return product.length <= 10_000 ? product : null;
}

export function present(elements: (Element | null | undefined)[]): Element[] {
  return [...new Set(elements.filter((e): e is Element => !!e))];
}
