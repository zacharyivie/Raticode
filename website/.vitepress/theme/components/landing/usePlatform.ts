import { onMounted, ref } from 'vue'
import type { DesktopOs } from '../../../../data/releases'

// Guesses the visitor's desktop OS, or an Android phone, after hydration. The static HTML never depends on
// it, and every platform stays one click away on the download section.
export function usePlatform() {
  const os = ref<DesktopOs | null>(null)
  const mobile = ref(false)
  const android = ref(false)

  onMounted(() => {
    const agent = navigator.userAgent
    android.value = /Android/i.test(agent)
    if (/Android|iPhone|iPad|iPod/i.test(agent)) mobile.value = true
    else if (/Mac/i.test(agent)) os.value = 'macos'
    else if (/Win/i.test(agent)) os.value = 'windows'
    else if (/Linux|X11|CrOS/i.test(agent)) os.value = 'linux'
  })

  return { os, mobile, android }
}
