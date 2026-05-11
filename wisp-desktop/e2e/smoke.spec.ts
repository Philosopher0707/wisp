import { test, expect } from '@playwright/test'

/**
 * Smoke test: verify the Wisp renderer loads without crashing.
 *
 * This runs against Chromium to assert DOM renders without uncaught errors.
 * For actual Electron E2E, use @playwright/experimental-ct-electron.
 */

const HTML_PATH = 'http://localhost:3000'

test.describe('Wisp Desktop Smoke', () => {
  test('landing page has title Wisp', async ({ page }) => {
    await page.goto(HTML_PATH)
    await expect(page).toHaveTitle(/Wisp/)
  })

  test('renders without white-screen JS errors', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (err) => errors.push(err.message))
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text())
    })

    await page.goto(HTML_PATH)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(500)

    expect(errors).toEqual([])
  })
})
