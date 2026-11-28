// Run with `npm test` from website/ (part of `npm run check`).
// The landing hero's button row: Download, Read the docs, then the coffee button.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const hero = readFileSync(new URL('../.vitepress/theme/components/landing/LandingHero.vue', import.meta.url), 'utf8')
const site = readFileSync(new URL('../data/site.ts', import.meta.url), 'utf8')

const actions = hero.match(/<div class="hero-actions">([\s\S]*?)\n {6}<\/div>/)?.[1] ?? ''
const buttons = [...actions.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a>/g)].map(([, attrs, inner]) => ({
  attrs,
  text: inner.replace(/<svg[\s\S]*?<\/svg>/g, '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim(),
}))

test('the hero row has three buttons, with Support Raticode right after Read the docs', () => {
  assert.deepEqual(buttons.map((button) => button.text), ['{{ primaryLabel }}', 'Read the docs', '☕ Support Raticode'])
})

test('the Support Raticode button opens the Buy Me a Coffee page in a new tab', () => {
  const support = buttons[2]
  assert.ok(support, 'no third hero button')
  assert.match(support.attrs, /:href="supportProjectUrl"/)
  assert.match(site, /supportProjectUrl = 'https:\/\/buymeacoffee\.com\/Raticode'/)
  assert.match(support.attrs, /target="_blank"/)
  assert.match(support.attrs, /rel="noopener noreferrer"/)
})

test('the Support Raticode button uses the same button classes as Read the docs, so it is the same height', () => {
  assert.match(buttons[1].attrs, /class="rc-btn rc-btn-ghost"/)
  assert.match(buttons[2].attrs, /class="rc-btn rc-btn-ghost\b/)
})
