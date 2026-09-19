import { expect, test } from '@playwright/test'

test('一次上傳所有已選檔案並顯示成功訊息', async ({ page }) => {
  let requestCount = 0

  await page.route('**/api/v1/me/statements', async (route) => {
    requestCount += 1

    const request = route.request()
    const multipartBody = request.postData() ?? ''

    expect(request.method()).toBe('POST')
    expect(request.headers()['content-type']).toContain('multipart/form-data; boundary=')
    expect(multipartBody.match(/name="files"/g)).toHaveLength(2)
    expect(multipartBody).toContain('filename="first.pdf"')
    expect(multipartBody).toContain('filename="second.pdf"')

    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  await page.goto('/upload-statement')
  await page.locator('#statement-file').setInputFiles([
    {
      name: 'first.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('first statement'),
    },
    {
      name: 'second.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('second statement'),
    },
  ])

  await expect(page.getByText('first.pdf、second.pdf')).toBeVisible()
  await page.getByRole('button', { name: '上傳帳單' }).click()

  await expect(page.getByRole('status')).toHaveText('已上傳 2 份帳單。')
  expect(requestCount).toBe(1)
})

test('上傳請求失敗時顯示錯誤訊息', async ({ page }) => {
  await page.route('**/api/v1/me/statements', async (route) => {
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Internal Server Error' }),
    })
  })

  await page.goto('/upload-statement')
  await page.locator('#statement-file').setInputFiles({
    name: 'statement.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('statement'),
  })
  await page.getByRole('button', { name: '上傳帳單' }).click()

  await expect(page.getByRole('alert')).toHaveText('上傳失敗，請稍後再試。')
})
