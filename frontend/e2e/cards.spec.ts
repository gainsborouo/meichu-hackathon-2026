import { expect, test } from '@playwright/test'

const catalogCards = [
  {
    id: '11111111-1111-4111-8111-111111111111',
    bank_name: '中國信託銀行',
    name: '中國信託 LINE Pay 信用卡',
    artwork_id: 'ctbc-linepay-ve8710',
    display_name: '中國信託銀行｜中國信託 LINE Pay 信用卡（VE8710）',
    issuer_en: 'CTBC Bank',
    variant: 'VE8710',
    network: 'VISA',
    tier: 'Signature',
    official_image_url: 'https://example.com/ctbc.png',
    image_is_composite: false,
  },
  {
    id: '22222222-2222-4222-8222-222222222222',
    bank_name: '台北富邦銀行',
    name: 'momo 卡',
    artwork_id: 'fubon-momo',
    display_name: '台北富邦銀行｜momo 卡',
    issuer_en: 'Taipei Fubon Bank',
    variant: null,
    network: null,
    tier: null,
    official_image_url: 'https://example.com/momo.png',
    image_is_composite: false,
  },
]
const existingUserCard = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  card: catalogCards[0]!,
  created_at: '2026-09-19T00:00:00Z',
}
const createdUserCard = {
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  card: catalogCards[1]!,
  created_at: '2026-09-19T01:00:00Z',
}

test('讀取卡片資料後可新增與移除卡片', async ({ page }) => {
  let postedCardId = ''
  let deletedUserCardId = ''

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const { pathname } = new URL(request.url())

    if (request.method() === 'GET' && pathname === '/api/v1/cards') {
      await route.fulfill({ status: 200, json: catalogCards })
      return
    }
    if (request.method() === 'GET' && pathname === '/api/v1/me/cards') {
      await route.fulfill({ status: 200, json: [existingUserCard] })
      return
    }
    if (request.method() === 'POST' && pathname === '/api/v1/me/cards') {
      postedCardId = request.postDataJSON().card_id
      await route.fulfill({ status: 201, json: createdUserCard })
      return
    }
    if (request.method() === 'DELETE' && pathname.startsWith('/api/v1/me/cards/')) {
      deletedUserCardId = pathname.split('/').at(-1) ?? ''
      expect(request.postData()).toBeNull()
      await route.fulfill({ status: 204 })
      return
    }

    await route.abort()
  })

  await page.goto('/cards')

  const wallet = page.getByRole('region', { name: '我的卡包' })
  await expect(wallet.getByText(catalogCards[0]!.name)).toBeVisible()
  await expect(page.locator('.catalogue-card')).toHaveCount(2)

  await page
    .getByRole('button', {
      name: `加入${catalogCards[1]!.bank_name}${catalogCards[1]!.name}`,
    })
    .click()
  await expect(wallet.getByText(catalogCards[1]!.name)).toBeVisible()
  expect(postedCardId).toBe(catalogCards[1]!.id)

  await page
    .getByRole('button', {
      name: `移除${catalogCards[1]!.bank_name}${catalogCards[1]!.name}`,
    })
    .click()
  await expect(wallet.getByText(catalogCards[1]!.name)).toHaveCount(0)
  expect(deletedUserCardId).toBe(createdUserCard.id)
})

test('初始讀取失敗時顯示錯誤，不顯示空卡包', async ({ page }) => {
  await page.route('**/api/v1/**', async (route) => {
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Internal Server Error' }),
    })
  })

  await page.goto('/cards')

  await expect(page.getByRole('alert')).toContainText('無法讀取卡片資料')
  await expect(page.getByText('尚未加入信用卡')).toHaveCount(0)
})

test('新增失敗時顯示錯誤並維持原本卡包', async ({ page }) => {
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const { pathname } = new URL(request.url())

    if (request.method() === 'GET' && pathname === '/api/v1/cards') {
      await route.fulfill({ status: 200, json: catalogCards })
      return
    }
    if (request.method() === 'GET' && pathname === '/api/v1/me/cards') {
      await route.fulfill({ status: 200, json: [] })
      return
    }
    if (request.method() === 'POST' && pathname === '/api/v1/me/cards') {
      await route.fulfill({ status: 500, json: { detail: 'Internal Server Error' } })
      return
    }

    await route.abort()
  })

  await page.goto('/cards')
  await page
    .getByRole('button', {
      name: `加入${catalogCards[0]!.bank_name}${catalogCards[0]!.name}`,
    })
    .click()

  await expect(page.getByRole('alert')).toContainText('持卡資料未變更')
  await expect(page.locator('.wallet-card')).toHaveCount(0)
})
