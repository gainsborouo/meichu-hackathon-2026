import { expect, test } from '@playwright/test'

test('switches to English and keeps the language across reloads and navigation', async ({
  page,
}) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem('credit-card-recommendation.locale')) {
      localStorage.setItem('credit-card-recommendation.locale', 'zh-TW')
    }
  })
  await page.goto('/')

  await expect(page.getByRole('heading', { level: 1 })).toContainText('這筆消費')
  await page.getByLabel('語言').selectOption('en-US')

  await expect(page.getByRole('heading', { level: 1 })).toContainText('Which card should you use')
  await expect(page).toHaveTitle('Credit Card Recommender')
  await expect(page.locator('html')).toHaveAttribute('lang', 'en-US')

  await page.reload()
  await expect(page.getByLabel('Language')).toHaveValue('en-US')

  await page.getByRole('link', { name: 'Upload Statements' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Upload Statements')
})
