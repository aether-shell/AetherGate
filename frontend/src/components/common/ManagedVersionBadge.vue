<template>
  <div class="min-w-0 max-w-full text-xs" data-testid="managed-version">
    <button
      v-if="isAdmin"
      type="button"
      class="rounded py-1 text-gray-500 hover:text-primary-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500 dark:text-dark-400"
      :title="t('version.managedTitle')"
      aria-haspopup="dialog"
      @click="showDetails = true"
    >
      {{ shortVersion }}
    </button>
    <span v-else class="text-gray-500 dark:text-dark-400" :title="version">{{ shortVersion }}</span>
    <BaseDialog
      v-if="isAdmin"
      :show="showDetails"
      :title="t('version.managedTitle')"
      width="narrow"
      close-on-click-outside
      @close="showDetails = false"
    >
      <div class="min-w-0 whitespace-normal text-sm">
        <p class="text-gray-500 dark:text-dark-400">{{ t('version.currentVersion') }}</p>
        <div class="mt-2 flex items-start gap-3">
          <code class="min-w-0 flex-1 break-all text-gray-900 dark:text-white">AetherGate <span v-if="version">v{{ version }}</span></code>
          <button v-if="version" type="button" class="shrink-0 text-primary-600" @click="copyToClipboard(version)">
            {{ t(copied ? 'common.copied' : 'common.copy') }}
          </button>
        </div>
        <p class="mt-4 break-words leading-relaxed text-gray-500 dark:text-dark-400">{{ t('version.managedHint') }}</p>
        <a class="mt-4 inline-block text-primary-600" href="https://github.com/aether-shell/AetherGate/actions" target="_blank" rel="noopener noreferrer">
          {{ t('version.managedActions') }}
        </a>
      </div>
    </BaseDialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'
import { useClipboard } from '@/composables/useClipboard'
import BaseDialog from './BaseDialog.vue'

const props = defineProps<{ version?: string }>()
const { t } = useI18n()
const auth = useAuthStore()
const { copied, copyToClipboard } = useClipboard()
const isAdmin = computed(() => auth.isAdmin)
const showDetails = ref(false)
const shortVersion = computed(() => {
  const release = props.version?.match(/^(?:v)?(\d+\.\d+\.\d+)/)?.[1]
  return release ? `v${release}` : 'AetherGate'
})
</script>
