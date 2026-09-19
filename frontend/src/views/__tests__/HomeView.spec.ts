import { describe, expect, it } from 'vitest'

import { mount } from '@vue/test-utils'
import HomeView from '../HomeView.vue'

describe('HomeView', () => {
  it('renders the project title', () => {
    const wrapper = mount(HomeView)

    expect(wrapper.get('h1').text()).toBe('Meichu Hackathon 2026')
  })
})
