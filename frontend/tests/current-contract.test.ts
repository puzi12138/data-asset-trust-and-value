import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App.vue'
import Value from '../src/pages/Value.vue'

vi.mock('vue-router', () => ({ useRoute: () => ({path:'/', meta:{title:'测试',subtitle:'测试'}}) }))
afterEach(() => vi.unstubAllGlobals())

describe('current application contracts', () => {
  it('presents the assessment workspace without internal release labels', () => {
    const wrapper = mount(App, {global:{stubs:{RouterLink:{template:'<a><slot /></a>'},RouterView:true}}})
    expect(wrapper.text()).toContain('可信与任务价值研究')
    expect(wrapper.text()).not.toMatch(/ph[a]se\s*\d/i)
  })
  it('offers current evidence registration without linking to an unavailable fixed case', () => {
    const wrapper = mount(App, {global:{stubs:{RouterLink:{props:['to'],template:'<a :href="to"><slot /></a>'},RouterView:true}}})
    expect(wrapper.find('a[href="/visual-cases"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/visual-review"]').exists()).toBe(false)
  })
  it('keeps simulated delivery separate and unavailable as an active valuation source', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url:string) => ({ok:true,status:200,json:async()=>url==='/api/assets'?{assets:[]}:{tasks:[],economics:[]}})))
    const wrapper = mount(Value)
    await flushPromises()
    const simulated = wrapper.get('[aria-label="价值信用来源"] option[value="simulated"]')
    expect(simulated.text()).toContain('模拟履约')
    expect(simulated.attributes('disabled')).toBeDefined()
    expect((wrapper.get('[aria-label="价值信用来源"]').element as HTMLSelectElement).value).toBe('local_operational')
  })
})
