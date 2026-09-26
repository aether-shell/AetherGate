<template>
  <details v-if="isAdmin" class="relative min-w-0 max-w-full text-xs" data-testid="managed-version">
    <summary class="max-w-full cursor-pointer truncate rounded-lg px-2 py-1 text-gray-500 dark:text-dark-400" :title="version ? `AetherGate v${version}` : 'AetherGate'">
      AetherGate <span v-if="version">v{{ version }}</span>
    </summary>
    <div class="absolute left-0 top-full z-50 mt-2 w-64 rounded-xl border border-gray-200 bg-white p-4 shadow-lg dark:border-dark-700 dark:bg-dark-800">
      <p class="font-medium text-gray-900 dark:text-white">{{ t('version.managedTitle') }}</p>
      <p class="mt-2 text-gray-500 dark:text-dark-400">{{ t('version.managedHint') }}</p>
      <a class="mt-3 inline-block text-primary-600" href="https://github.com/aether-shell/AetherGate/actions" target="_blank" rel="noopener noreferrer">
        {{ t('version.managedActions') }}
      </a>
    </div>
  </details>
  <span v-else class="text-xs text-gray-500 dark:text-dark-400">AetherGate <span v-if="version">v{{ version }}</span></span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'

defineProps<{ version?: string }>()
const { t } = useI18n()
const auth = useAuthStore()
const isAdmin = computed(() => auth.isAdmin)
</script>
