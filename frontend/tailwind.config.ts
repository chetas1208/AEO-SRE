import type { Config } from 'tailwindcss'

export default <Partial<Config>>{
  darkMode: 'class',
  content: [
    './components/**/*.{vue,js,ts}',
    './layouts/**/*.vue',
    './pages/**/*.vue',
    './app.vue',
    './plugins/**/*.{js,ts}'
  ],
  theme: {
    extend: {
      colors: {
        'bg-0': '#060911',
        'bg-1': '#0a0e1a',
        'bg-2': '#0f1526',
        'bg-3': '#151d34',
        'surface': 'rgba(15, 21, 38, 0.72)',
        'surface-card': '#0d1322',
        'surface-elevated': '#141c32',
        'border-custom': 'rgba(45, 58, 88, 0.55)',
        'border-strong': 'rgba(72, 92, 138, 0.7)',
        'primary': '#6366f1',
        'good': '#10b981',
        'warn': '#f59e0b',
        'bad': '#f43f5e',
        'blue-custom': '#38bdf8',
        'policy': '#a855f7'
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Inter', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace']
      }
    }
  }
}
