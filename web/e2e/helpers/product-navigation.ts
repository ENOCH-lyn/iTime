import { expect, type Locator, type Page } from '@playwright/test'

type ProductDestination = 'AI' | '任务' | '规划' | '日历' | '复盘' | '我的'
type LocatorRoot = Page | Locator

const paths: Record<ProductDestination, string> = {
  AI: '/ai',
  任务: '/tasks',
  规划: '/plans',
  日历: '/calendar',
  复盘: '/reviews',
  我的: '/me',
}

async function firstVisible(locator: Locator) {
  for (const candidate of await locator.all()) {
    if (await candidate.isVisible()) return candidate
  }
  return null
}

async function visibleNavigationDialog(page: Page) {
  return firstVisible(page.getByRole('dialog', { name: '导航', exact: true }))
}

async function waitForNavigationDialog(page: Page) {
  let dialog: Locator | null = null
  await expect.poll(async () => {
    dialog = await visibleNavigationDialog(page)
    return dialog !== null
  }).toBe(true)
  return dialog!
}

async function visibleDestination(root: LocatorRoot, label: ProductDestination) {
  return (
    await firstVisible(root.getByRole('link', { name: label, exact: true }))
    ?? await firstVisible(root.getByRole('menuitem', { name: label, exact: true }))
  )
}

async function waitForDestination(root: LocatorRoot, label: ProductDestination) {
  let destination: Locator | null = null
  await expect.poll(async () => {
    destination = await visibleDestination(root, label)
    return destination !== null
  }).toBe(true)
  return destination!
}

export async function navigateToProduct(
  page: Page,
  label: ProductDestination,
) {
  let root: LocatorRoot = await visibleNavigationDialog(page) ?? page
  let destination = await visibleDestination(root, label)
  if (!destination) {
    const openNavigation = await firstVisible(
      page.getByRole('button', { name: '打开导航', exact: true }),
    )
    if (openNavigation) {
      await openNavigation.click()
      root = await waitForNavigationDialog(page)
      destination = await waitForDestination(root, label)
    }
  }

  expect(destination, `找不到可见的"${label}"产品入口`).not.toBeNull()
  await destination!.click()
  await expect(page).toHaveURL(new RegExp(`${paths[label]}(?:[?#/]|$)`))
}
