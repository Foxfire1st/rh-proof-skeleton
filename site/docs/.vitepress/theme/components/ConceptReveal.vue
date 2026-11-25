<script setup lang="ts">
import { computed } from 'vue'
import { useData, withBase } from 'vitepress'

const props = defineProps<{
  slug: string
  term: string
  href: string
}>()

const { site } = useData()

const resolvedHref = computed(() => {
  if (!props.href.startsWith('/')) {
    return props.href
  }
  const base = site.value.base
  if (base && base !== '/' && props.href.startsWith(base)) {
    return props.href
  }
  return withBase(props.href)
})
</script>

<template>
  <span class="rh-concept-reveal" :data-concept="slug">
    <span class="rh-concept-reveal__eyebrow" aria-hidden="true">Idea</span>
    <span class="rh-concept-reveal__prose"><slot /></span>
    <span class="rh-concept-reveal__sep" aria-hidden="true">→</span>
    <a :href="resolvedHref" class="rh-concept-reveal__term">{{ term }}</a>
  </span>
</template>
