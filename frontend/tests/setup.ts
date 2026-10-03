// Emulates Nuxt auto-imports for component tests (no Nuxt runtime needed).
import * as vue from 'vue'
import { config } from '@vue/test-utils'
import * as format from '~/utils/format'
import * as capabilities from '~/utils/capabilities'
import * as guard from '~/utils/guard'

Object.assign(globalThis, { computed: vue.computed, ref: vue.ref, watch: vue.watch, onMounted: vue.onMounted, onBeforeUnmount: vue.onBeforeUnmount, nextTick: vue.nextTick })
Object.assign(globalThis, format, capabilities, guard)
config.global.mocks = { ...format, ...capabilities, ...guard }
