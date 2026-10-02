import { ref, watch } from 'vue'

export type Theme = 'system' | 'light' | 'dark'
export type FontSize = 'normal' | 'large' | 'larger'
const media = window.matchMedia('(prefers-color-scheme: dark)')
let saved: { theme?: Theme; fontSize?: FontSize } = {}
try { saved = JSON.parse(localStorage.getItem('itime-appearance') ?? '{}') ?? {} } catch { /* Optional local preference. */ }
export const theme = ref<Theme>(['system', 'light', 'dark'].includes(saved.theme ?? '') ? saved.theme! : 'system')
export const fontSize = ref<FontSize>(['normal', 'large', 'larger'].includes(saved.fontSize ?? '') ? saved.fontSize! : 'normal')

function apply() {
  document.documentElement.dataset.theme = theme.value === 'system' ? (media.matches ? 'dark' : 'light') : theme.value
  document.documentElement.dataset.fontSize = fontSize.value
}
watch([theme, fontSize], () => {
  apply()
  try { localStorage.setItem('itime-appearance', JSON.stringify({ theme: theme.value, fontSize: fontSize.value })) } catch { /* Still applies for this visit. */ }
})
media.addEventListener('change', apply)
apply()
