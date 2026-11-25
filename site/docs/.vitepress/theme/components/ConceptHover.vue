<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import { useData, withBase } from 'vitepress'

const props = defineProps<{
  slug: string
  term: string
  display: string
  glimpse: string
  href: string
  visualSrc?: string
  visualAlt?: string
}>()

const { site } = useData()
const uid = useId()
const popoverId = computed(() => `rh-hover-${props.slug}-${uid}`)

const wrapperRef = ref<HTMLElement | null>(null)
const triggerRef = ref<HTMLButtonElement | null>(null)
const cardRef = ref<HTMLElement | null>(null)

const isHovered = ref(false)
const isFocused = ref(false)
const isPinned = ref(false)
const suppressFocusOpen = ref(false)
const alignRight = ref(false)
const shiftPx = ref(0)

let hoverCloseTimer: ReturnType<typeof setTimeout> | null = null

const isOpen = computed(() => isPinned.value || isHovered.value || isFocused.value)

function resolveSitePath(raw: string): string {
  if (!raw.startsWith('/')) {
    return raw
  }
  const base = site.value.base
  if (base && base !== '/' && raw.startsWith(base)) {
    return raw
  }
  return withBase(raw)
}

const resolvedHref = computed(() => resolveSitePath(props.href))
const resolvedVisualSrc = computed(() =>
  props.visualSrc ? resolveSitePath(props.visualSrc) : undefined,
)

const cardStyle = computed(() =>
  shiftPx.value !== 0 ? { '--rh-card-shift': `${shiftPx.value}px` } : undefined,
)

function clearHoverTimer() {
  if (hoverCloseTimer !== null) {
    clearTimeout(hoverCloseTimer)
    hoverCloseTimer = null
  }
}

function updateDesktopPosition() {
  if (typeof window === 'undefined' || window.innerWidth <= 640) {
    alignRight.value = false
    shiftPx.value = 0
    return
  }
  const wrapper = wrapperRef.value
  if (wrapper) {
    const rect = wrapper.getBoundingClientRect()
    alignRight.value = rect.left > window.innerWidth / 2
  }
  shiftPx.value = 0
  void nextTick(() => {
    const card = cardRef.value
    if (!card || window.innerWidth <= 640) {
      return
    }
    const cardRect = card.getBoundingClientRect()
    const gutter = 16
    if (cardRect.right > window.innerWidth - gutter) {
      shiftPx.value = -(cardRect.right - (window.innerWidth - gutter))
    } else if (cardRect.left < gutter) {
      shiftPx.value = gutter - cardRect.left
    }
  })
}

function onMouseEnter() {
  clearHoverTimer()
  suppressFocusOpen.value = false
  isHovered.value = true
}

function onMouseLeave() {
  clearHoverTimer()
  hoverCloseTimer = setTimeout(() => {
    isHovered.value = false
  }, 120)
}

function onFocusIn() {
  clearHoverTimer()
  if (suppressFocusOpen.value) {
    return
  }
  isFocused.value = true
}

function onFocusOut(event: FocusEvent) {
  const next = event.relatedTarget as Node | null
  if (!next || !wrapperRef.value?.contains(next)) {
    isFocused.value = false
    isPinned.value = false
    suppressFocusOpen.value = false
  }
}

function togglePin() {
  clearHoverTimer()
  suppressFocusOpen.value = false
  if (isPinned.value) {
    isPinned.value = false
    isHovered.value = false
    isFocused.value = false
  } else {
    isPinned.value = true
  }
}

function closeAll() {
  clearHoverTimer()
  const focusWasInsideCard = Boolean(
    typeof document !== 'undefined' &&
      cardRef.value &&
      document.activeElement &&
      cardRef.value.contains(document.activeElement),
  )
  suppressFocusOpen.value = true
  isPinned.value = false
  isHovered.value = false
  isFocused.value = false
  if (focusWasInsideCard) {
    triggerRef.value?.focus()
  }
}

function closeAndReturnFocus() {
  clearHoverTimer()
  suppressFocusOpen.value = true
  isPinned.value = false
  isHovered.value = false
  isFocused.value = false
  triggerRef.value?.focus()
}

function onDocumentClick(event: MouseEvent) {
  if (!isOpen.value) {
    return
  }
  const target = event.target as Node | null
  if (target && wrapperRef.value && !wrapperRef.value.contains(target)) {
    isPinned.value = false
    isHovered.value = false
    isFocused.value = false
    suppressFocusOpen.value = false
  }
}

watch(isOpen, (open) => {
  if (open) {
    updateDesktopPosition()
  }
})

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
})

onUnmounted(() => {
  clearHoverTimer()
  document.removeEventListener('click', onDocumentClick)
})
</script>

<template>
  <span
    ref="wrapperRef"
    class="rh-concept-hover"
    @mouseenter="onMouseEnter"
    @mouseleave="onMouseLeave"
    @focusin="onFocusIn"
    @focusout="onFocusOut"
    @keydown.esc.stop="closeAll"
  >
    <a :href="resolvedHref" class="rh-concept-hover__link">{{ display }}</a>
    <button
      ref="triggerRef"
      type="button"
      class="rh-concept-hover__trigger"
      :aria-label="`Show recall card for ${term}`"
      :aria-expanded="isOpen ? 'true' : 'false'"
      :aria-controls="popoverId"
      @click.stop="togglePin"
    >
      ?
    </button>
    <span
      v-if="isOpen"
      ref="cardRef"
      :id="popoverId"
      role="region"
      :aria-label="`${term} recall card`"
      class="rh-concept-hover__card"
      :class="{ 'rh-concept-hover__card--right': alignRight }"
      :style="cardStyle"
    >
      <span class="rh-concept-hover__header">
        <strong class="rh-concept-hover__title">{{ term }}</strong>
        <button
          type="button"
          class="rh-concept-hover__close"
          :aria-label="`Close ${term} recall card`"
          @click.stop="closeAndReturnFocus"
        >
          ×
        </button>
      </span>
      <span class="rh-concept-hover__glimpse">{{ glimpse }}</span>
      <img
        v-if="resolvedVisualSrc"
        :src="resolvedVisualSrc"
        :alt="visualAlt || `Diagram for ${term}`"
        class="rh-concept-hover__img"
        loading="lazy"
      />
      <a :href="resolvedHref" class="rh-concept-hover__more">
        Open full Concept Page →
      </a>
    </span>
  </span>
</template>
