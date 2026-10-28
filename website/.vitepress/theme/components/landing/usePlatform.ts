import { onMounted, ref } from 'vue'
import type { DesktopOs } from '../../../../data/releases'

// Guesses the visitor's desktop OS after hydration. The static HTML never depends on
// it, and every platform stays one click away on the download section.
export function usePlatform() {
  const os = ref<DesktopOs | null>(null)
  const mobile = ref(false)

  onMounted(() => {
    const agent = navigator.userAgent
    if (/Android|iPhone|iPad|iPod/i.test(agent)) mobile.value = true
    else if (/Mac/i.test(agent)) os.value = 'macos'
    else if (/Win/i.test(agent)) os.value = 'windows'
    else if (/Linux|X11|CrOS/i.test(agent)) os.value = 'linux'
  })

  return { os, mobile }
}
