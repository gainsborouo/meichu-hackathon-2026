import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';
import { getAdapter, inspectCheckout, parseAmount } from '../adapters';
import { isCheckoutRequest } from '../lib/validation';

const fixture = (name: string) => readFileSync(resolve('tests/fixtures', name), 'utf8');

describe('observed checkout adapters', () => {
  beforeEach(() => { document.body.innerHTML = ''; });

  it('extracts a string product and total from the observed momo payment structure', () => {
    document.body.innerHTML = fixture('momo-payment.html');
    expect(inspectCheckout(document, 'https://cart.momoshop.com.tw/view/cart/WEB/newNormal')).toEqual({
      status: 'ready', context: { platform: 'momo', product: '測試耳機、測試保護殼', payable: 2580 },
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
      status: 'ready', context: { platform: 'shopee', product: '測試耳機、測試保護殼', payable: 2580 },
    });
  });

  it('recommends on the observed PChome cart and only extracts selected products', () => {
    document.body.innerHTML = fixture('pchome-cart.html');
    expect(inspectCheckout(document, 'https://ecssl.pchome.com.tw/fsrwd/cart')).toEqual({
      status: 'ready', context: { platform: 'pchome', product: '測試耳機', payable: 2580 },
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
  it('accepts exactly the four internal checkout fields', () => {
    const request = { requestId: 'test', platform: 'momo', product: '耳機、保護殼', payable: 2580 };
    expect(isCheckoutRequest(request)).toBe(true);
    // The backend takes the user from the ID token, so a userId here is not
    // part of the contract and must be rejected as an unexpected field.
    expect(isCheckoutRequest({ ...request, userId: 'firebase-user' })).toBe(false);
    expect(isCheckoutRequest({ ...request, payable: '2580' })).toBe(false);
    expect(isCheckoutRequest({ ...request, product: ['耳機'] })).toBe(false);
    expect(isCheckoutRequest({ ...request, payable: NaN })).toBe(false);
    expect(isCheckoutRequest({ ...request, platform: 'books.com' })).toBe(false);
  });

  it.each(['NT$2,580', '應付金額：NT$2,580', 'TWD 2580.00', '$2580'])('parses %s', value => {
    expect(parseAmount(value)).toBe(2580);
  });

  it.each(['0', '-100', 'NT$1,00', '商品 2580 運費 60', 'NT$2,580 NT$3,000'])('rejects %s', value => {
    expect(parseAmount(value)).toBeNull();
  });
});
