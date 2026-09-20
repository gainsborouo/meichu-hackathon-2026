import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';
import { getAdapter, inspectAdapter, inspectCheckout, parseAmount } from '../adapters';
import { pageLocale } from '../lib/i18n';
import { isCheckoutRequest } from '../lib/validation';

const fixture = (name: string) => readFileSync(resolve('tests/fixtures', name), 'utf8');

describe('observed checkout adapters', () => {
  beforeEach(() => { document.body.innerHTML = ''; });

  it('extracts a string product and total from the observed momo payment structure', () => {
    document.body.innerHTML = fixture('momo-payment.html');
    expect(inspectCheckout(document, 'https://cart.momoshop.com.tw/view/cart/WEB/newNormal')).toEqual({
      status: 'ready', context: { platform: 'momo', product: '測試耳機、測試保護殼', payable: 2580, pageLocale: null },
    });
    const adapter = getAdapter(document, 'https://cart.momoshop.com.tw/view/cart/WEB/newNormal')!;
    expect(adapter.getRecommendationMountPoint()).toBe(document.querySelector('#paymentType'));
    expect(adapter.getObservationRoots()).not.toContain(document.body);
  });

  it('does not treat momo cart as its payment-selection page', () => {
    document.body.innerHTML = fixture('momo-cart.html');
    expect(inspectCheckout(document, 'https://cart.momoshop.com.tw/view/cart/WEB/newNormal')).toEqual({ status: 'outside' });
  });

  it('rejects disabled Shopee credit card controls from the saved checkout', () => {
    document.body.innerHTML = fixture('shopee-payment.html');
    expect(inspectCheckout(document, 'https://shopee.tw/checkout')).toEqual({
      status: 'no-credit-card', reason: 'credit-unavailable',
    });
  });

  it('extracts Shopee product and total when an observed credit control is enabled', () => {
    document.body.innerHTML = fixture('shopee-payment.html');
    const credit = document.querySelector<HTMLElement>('[aria-label="信用卡/金融卡"]')!;
    credit.classList.remove('product-variation--disabled');
    credit.removeAttribute('aria-disabled');
    expect(inspectCheckout(document, 'https://shopee.tw/checkout')).toEqual({
      status: 'ready', context: { platform: 'shopee', product: '測試耳機、測試保護殼', payable: 2580, pageLocale: null },
    });
  });

  // The shopper's Shopee account language is their own setting, so extraction has
  // to survive it. This fixture is the English page that previously produced
  // 'product-missing': its product span holds two children, its total heading
  // carries a colon and appears on both an h2 and an h3, and its payment labels
  // are English.
  it('extracts Shopee product and total from the English checkout page', () => {
    document.body.innerHTML = fixture('shopee-payment-en.html');
    expect(inspectCheckout(document, 'https://shopee.tw/checkout')).toEqual({
      status: 'ready',
      context: {
        platform: 'shopee',
        product: 'Blanket Dormitory Single Bedding Class A',
        payable: 270,
        pageLocale: null,
      },
    });
  });

  it('still sees no credit card when the English controls are disabled', () => {
    document.body.innerHTML = fixture('shopee-payment-en.html');
    for (const control of document.querySelectorAll('[role="radio"]')) {
      control.classList.add('product-variation--disabled');
      control.setAttribute('aria-disabled', 'true');
    }
    expect(inspectCheckout(document, 'https://shopee.tw/checkout')).toEqual({
      status: 'no-credit-card', reason: 'credit-unavailable',
    });
  });

  it('recommends on the observed PChome cart and only extracts selected products', () => {
    document.body.innerHTML = fixture('pchome-cart.html');
    expect(inspectCheckout(document, 'https://ecssl.pchome.com.tw/fsrwd/cart')).toEqual({
      status: 'ready', context: { platform: 'pchome', product: '測試耳機', payable: 2580, pageLocale: null },
    });
    const adapter = getAdapter(document, 'https://ecssl.pchome.com.tw/fsrwd/cart')!;
    expect(adapter.getRecommendationMountPoint()).toBe(document.querySelector('.c-nestedList--cartBillsTotals'));
    expect(adapter.getObservationRoots()).not.toContain(document.body);
  });

  it('does not recommend on the PChome payment page', () => {
    document.body.innerHTML = fixture('pchome-payment.html');
    expect(inspectCheckout(document, 'https://ecssl.pchome.com.tw/fsrwd/cart/payinfo')).toEqual({ status: 'outside' });
  });

  it.each([
    ['momo-cart.html', 'https://cart.momoshop.com.tw/view/cart/WEB/newNormal'],
    ['shopee-cart.html', 'https://shopee.tw/cart'],
  ])('does not call %s a payment-selection page', (file, url) => {
    document.body.innerHTML = fixture(file);
    expect(inspectCheckout(document, url)).toEqual({ status: 'outside' });
  });

  it('rejects unrelated and lookalike domains', () => {
    document.body.innerHTML = fixture('shopee-payment.html');
    expect(inspectCheckout(document, 'https://shopee.tw.evil.example/checkout')).toEqual({ status: 'outside' });
    expect(inspectCheckout(document, 'https://example.com/checkout')).toEqual({ status: 'outside' });
  });
});

describe('request contracts', () => {
  it('accepts exactly the internal checkout fields', () => {
    const request = {
      requestId: 'test', platform: 'momo', product: '耳機、保護殼', payable: 2580,
      pageLocale: null,
    };
    expect(isCheckoutRequest(request)).toBe(true);
    expect(isCheckoutRequest({ ...request, pageLocale: 'zh-TW' })).toBe(true);
    expect(isCheckoutRequest({ ...request, pageLocale: 'en-US' })).toBe(true);
    // The backend takes the user from the ID token, so a userId here is not
    // part of the contract and must be rejected as an unexpected field.
    expect(isCheckoutRequest({ ...request, userId: 'firebase-user' })).toBe(false);
    expect(isCheckoutRequest({ ...request, payable: '2580' })).toBe(false);
    expect(isCheckoutRequest({ ...request, product: ['耳機'] })).toBe(false);
    expect(isCheckoutRequest({ ...request, payable: NaN })).toBe(false);
    expect(isCheckoutRequest({ ...request, platform: 'books.com' })).toBe(false);
    // A page may declare any language; only the supported two may cross the
    // boundary, so the backend never receives a locale it would reject.
    expect(isCheckoutRequest({ ...request, pageLocale: 'ja-JP' })).toBe(false);
    expect(isCheckoutRequest({ ...request, pageLocale: '' })).toBe(false);
    // Omitting it entirely is not the same as declaring nothing.
    const { pageLocale: _omitted, ...without } = request;
    expect(isCheckoutRequest(without)).toBe(false);
  });

  it('reads the page language from the document, defaulting to null', () => {
    document.body.innerHTML = fixture('shopee-payment-en.html');
    // The fixtures declare no lang, which is the honest "unknown" case.
    expect(pageLocale(document)).toBeNull();

    document.documentElement.setAttribute('lang', 'zh-TW');
    expect(pageLocale(document)).toBe('zh-TW');
    document.documentElement.setAttribute('lang', 'zh-Hant-TW');
    expect(pageLocale(document)).toBe('zh-TW');
    document.documentElement.setAttribute('lang', 'en');
    expect(pageLocale(document)).toBe('en-US');
    // A language we do not support is null, not a wrong guess.
    document.documentElement.setAttribute('lang', 'ja');
    expect(pageLocale(document)).toBeNull();
    document.documentElement.removeAttribute('lang');
  });

  it('sends the page language with the checkout request', () => {
    document.body.innerHTML = fixture('shopee-payment-en.html');
    document.documentElement.setAttribute('lang', 'en-US');
    expect(inspectCheckout(document, 'https://shopee.tw/checkout')).toMatchObject({
      status: 'ready', context: { pageLocale: 'en-US' },
    });
    document.documentElement.removeAttribute('lang');
  });

  it.each(['NT$2,580', '應付金額：NT$2,580', 'TWD 2580.00', '$2580'])('parses %s', value => {
    expect(parseAmount(value)).toBe(2580);
  });

  it.each(['0', '-100', 'NT$1,00', '商品 2580 運費 60', 'NT$2,580 NT$3,000'])('rejects %s', value => {
    expect(parseAmount(value)).toBeNull();
  });

  it('carries the locale it was given, so the panel and the backend agree', () => {
    // inspectAdapter takes the locale rather than resolving it, because only the
    // content script can: the background has no document and its navigator is the
    // service worker's. A popup override therefore has to travel this way too.
    document.body.innerHTML = fixture('shopee-payment-en.html');
    document.documentElement.setAttribute('lang', 'zh-TW');

    // The page says Chinese, but an explicit choice of English must win.
    expect(inspectAdapter(getAdapter(document, 'https://shopee.tw/checkout'), 'en-US'))
      .toMatchObject({ status: 'ready', context: { pageLocale: 'en-US' } });
    expect(inspectAdapter(getAdapter(document, 'https://shopee.tw/checkout'), 'zh-TW'))
      .toMatchObject({ status: 'ready', context: { pageLocale: 'zh-TW' } });

    document.documentElement.removeAttribute('lang');
  });
});
