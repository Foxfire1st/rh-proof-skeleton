<script setup lang="ts">
import { onMounted, ref } from 'vue'

type ZoomLevel = 'first-principles' | 'undergrad' | 'lean'

interface ZoomTabDef {
  id: ZoomLevel
  panelId: string
  slotName: 'level1' | 'level2' | 'level3'
  label: string
}

const TABS: readonly ZoomTabDef[] = [
  {
    id: 'first-principles',
    panelId: 'level-1',
    slotName: 'level1',
    label: 'Level 1 · First-Principles',
  },
  {
    id: 'undergrad',
    panelId: 'level-2',
    slotName: 'level2',
    label: 'Level 2 · Undergrad Bridge',
  },
  {
    id: 'lean',
    panelId: 'level-3',
    slotName: 'level3',
    label: 'Level 3 · Formal Lean Contract',
  },
] as const

const props = defineProps<{
  pageId: string
}>()

const hydrated = ref(false)
const activeLevel = ref<ZoomLevel>('first-principles')
const tabRefs = ref<Array<HTMLButtonElement | null>>([])

function parseLevelFromUrl(): ZoomLevel | null {
  if (typeof window === 'undefined') {
    return null
  }
  const params = new URLSearchParams(window.location.search)
  const queryLevel = params.get('level')
  if (queryLevel === 'first-principles' || queryLevel === 'abitur') {
    return 'first-principles'
  }
  if (queryLevel === 'undergrad' || queryLevel === 'lean') {
    return queryLevel
  }
  const hash = window.location.hash.toLowerCase()
  if (
    hash === '#level-first-principles' ||
    hash === '#level-abitur' ||
    hash === '#level-1'
  ) {
    return 'first-principles'
  }
  if (hash === '#level-undergrad' || hash === '#level-2') {
    return 'undergrad'
  }
  if (hash === '#level-lean' || hash === '#level-3') {
    return 'lean'
  }
  return null
}

function syncUrl(level: ZoomLevel) {
  if (typeof window === 'undefined') {
    return
  }
  const url = new URL(window.location.href)
  url.searchParams.set('level', level)
  if (url.hash.toLowerCase().startsWith('#level-')) {
    url.hash = ''
  }
  window.history.replaceState(window.history.state, '', url.toString())
}

function setTabRef(el: unknown, index: number) {
  tabRefs.value[index] = el instanceof HTMLButtonElement ? el : null
}

function selectTab(level: ZoomLevel, moveFocus = false) {
  activeLevel.value = level
  syncUrl(level)
  if (moveFocus) {
    const idx = TABS.findIndex((tab) => tab.id === level)
    if (idx >= 0) {
      tabRefs.value[idx]?.focus()
    }
  }
}

function onTabKeydown(event: KeyboardEvent, currentIndex: number) {
  let nextIndex: number | null = null
  if (event.key === 'ArrowRight') {
    nextIndex = (currentIndex + 1) % TABS.length
  } else if (event.key === 'ArrowLeft') {
    nextIndex = (currentIndex - 1 + TABS.length) % TABS.length
  } else if (event.key === 'Home') {
    nextIndex = 0
  } else if (event.key === 'End') {
    nextIndex = TABS.length - 1
  }
  if (nextIndex !== null) {
    event.preventDefault()
    selectTab(TABS[nextIndex].id, true)
  }
}

onMounted(() => {
  const initial = parseLevelFromUrl()
  if (initial) {
    activeLevel.value = initial
  }
  hydrated.value = true
})
</script>

<template>
  <div class="rh-zoom-tabs" :data-page-id="props.pageId">
    <nav
      v-if="!hydrated"
      class="rh-zoom-static-nav"
      aria-label="Jump to explanation level"
    >
      <span class="rh-zoom-static-nav__label">Explanation levels:</span>
      <a
        v-for="tab in TABS"
        :key="tab.id"
        :href="`#${tab.panelId}`"
        class="rh-zoom-static-nav__link"
      >
        {{ tab.label }}
      </a>
    </nav>

    <div
      v-else
      class="rh-zoom-tablist"
      role="tablist"
      aria-label="Explanation depth level"
    >
      <button
        v-for="(tab, index) in TABS"
        :key="tab.id"
        :id="`${props.pageId}-tab-${tab.id}`"
        :ref="(el) => setTabRef(el, index)"
        type="button"
        role="tab"
        class="rh-zoom-tab"
        :class="{ 'rh-zoom-tab--active': activeLevel === tab.id }"
        :aria-selected="activeLevel === tab.id ? 'true' : 'false'"
        :aria-controls="tab.panelId"
        :tabindex="activeLevel === tab.id ? 0 : -1"
        @click="selectTab(tab.id)"
        @keydown="onTabKeydown($event, index)"
      >
        {{ tab.label }}
      </button>
    </div>

    <section
      v-for="tab in TABS"
      :key="tab.id"
      :id="tab.panelId"
      class="rh-zoom-panel"
      :class="{ 'rh-zoom-panel--hidden': hydrated && activeLevel !== tab.id }"
      :role="hydrated ? 'tabpanel' : undefined"
      :aria-labelledby="
        hydrated
          ? `${props.pageId}-tab-${tab.id}`
          : `${props.pageId}-heading-${tab.id}`
      "
      :tabindex="hydrated && activeLevel === tab.id ? 0 : undefined"
    >
      <div
        :id="`${props.pageId}-heading-${tab.id}`"
        class="rh-zoom-panel__heading"
        :class="{ 'rh-zoom-panel__heading--sr': hydrated }"
      >
        {{ tab.label }}
      </div>
      <slot :name="tab.slotName" />
    </section>
  </div>
</template>
