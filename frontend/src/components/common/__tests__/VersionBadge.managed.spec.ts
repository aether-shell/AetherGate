import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import VersionBadge from '../VersionBadge.vue'

const mocks = vi.hoisted(() => ({
  fetchVersion: vi.fn(),
  update: vi.fn(),
  rollback: vi.fn(),
  versions: vi.fn()
}))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ isAdmin: true }) }))
vi.mock('@/stores/app', () => ({ useAppStore: () => ({
  currentVersion: '0.2.8-aethergate.1', latestVersion: '99.0.0', hasUpdate: true,
  buildType: 'release', releaseInfo: { html_url: 'https://github.com/Wei-Shaw/sub2api' },
  fetchVersion: mocks.fetchVersion
}) }))
vi.mock('vue-i18n', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-i18n')>(),
  useI18n: () => ({ t: (key: string) => key })
}))
vi.mock('@/api/admin/system', () => ({
  performUpdate: mocks.update, rollback: mocks.rollback, getRollbackVersions: mocks.versions,
  restartService: vi.fn()
}))

describe('AetherGate managed version', () => {
  it('旧官方更新缓存不能显示更新、回退按钮或官方命令', async () => {
    const wrapper = mount(VersionBadge)
    await wrapper.get('button').trigger('click')
    expect(document.body.textContent).toContain('AetherGate')
    expect(document.body.textContent).toContain('0.2.8-aethergate.1')
    expect(document.body.querySelector('a')?.getAttribute('href')).toBe('https://github.com/aether-shell/AetherGate/actions')
    expect(document.body.innerHTML).not.toContain('weishaw/sub2api')
    expect(document.body.innerHTML).not.toContain('install.sh')
    expect(mocks.update).not.toHaveBeenCalled()
    expect(mocks.rollback).not.toHaveBeenCalled()
    expect(mocks.versions).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
