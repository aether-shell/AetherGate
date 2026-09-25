<template>
  <ManagedVersionBadge :version="currentVersion" />
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import ManagedVersionBadge from './ManagedVersionBadge.vue'

const props = defineProps<{ version?: string }>()
const authStore = useAuthStore()
const appStore = useAppStore()
const currentVersion = computed(() => appStore.currentVersion || props.version || '')

onMounted(() => {
  if (authStore.isAdmin) {
    // Cached release/source flags cannot re-enable official updates.
    appStore.fetchVersion(false)
  }
})
</script>
