<script setup lang="ts">
import { computed } from 'vue'
import { withBase } from 'vitepress'
import { screenshotImage, type ScreenshotName } from '../../../data/screenshots'

// A real app screenshot in the docs. Below-the-fold by default, so it loads lazily.
// `phone` shows a portrait phone capture at phone size instead of the full column width.
const props = withDefaults(defineProps<{ name: ScreenshotName; caption?: string; alt?: string; eager?: boolean; phone?: boolean }>(), {
  caption: '',
  alt: '',
  eager: false,
  phone: false,
})

const image = computed(() => screenshotImage(props.name))
const src = computed(() => withBase(image.value.src))
const srcset = computed(() => image.value.sources.map((source) => `${withBase(source.path)} ${source.width}w`).join(', '))
const altText = computed(() => props.alt || image.value.alt)
const sizes = computed(() => (props.phone ? '300px' : `(min-width: 960px) min(720px, ${image.value.width}px), calc(100vw - 48px)`))
</script>

<template>
  <figure class="rc-shot" :class="{ 'rc-shot-phone': phone }">
    <a :href="src" class="rc-shot-frame" :style="{ maxWidth: phone ? undefined : `${image.width}px` }" :aria-label="`Open the full-size screenshot: ${caption || altText}`">
      <img
        :src="src"
        :srcset="srcset"
        :sizes="sizes"
        :width="image.width"
        :height="image.height"
        :alt="altText"
        :loading="eager ? 'eager' : 'lazy'"
        decoding="async"
      />
    </a>
    <figcaption v-if="caption">{{ caption }}</figcaption>
  </figure>
</template>

<style scoped>
.rc-shot {
  margin: 24px 0 28px;
}

.rc-shot-frame {
  display: block;
  overflow: hidden;
  border: 1px solid var(--vp-c-divider);
  border-radius: 10px;
  background: #18181b;
  box-shadow: 0 18px 40px -24px rgba(10, 9, 32, 0.55);
  line-height: 0;
  cursor: zoom-in;
}

.rc-shot-frame:focus-visible {
  outline: 2px solid var(--vp-c-brand-1);
  outline-offset: 3px;
}

.rc-shot img {
  display: block;
  width: 100%;
  height: auto;
}

.rc-shot-phone .rc-shot-frame {
  max-width: 300px;
  border-radius: 22px;
}

.rc-shot-phone figcaption {
  max-width: 300px;
}

.rc-shot figcaption {
  margin-top: 10px;
  font-size: 0.9rem;
  line-height: 1.6;
  color: var(--vp-c-text-2);
}
</style>
