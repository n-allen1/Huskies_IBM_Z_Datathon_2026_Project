/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{html,js,ts,svelte}', './index.html'],
  theme: {
    extend: {
      colors: {
        app: '#0b111b',
        panel: '#131d2b',
        raised: '#1b2939',
        hover: '#223347',
        line: '#2a3a4c',
        'line-strong': '#5a7189',
        fg: '#e8eef5',
        muted: '#9baec2',
        faint: '#8ba0b5',
        accent: '#4da8d8',
        'accent-fg': '#06121c',
        'accent-soft': '#14304a',
        p1: '#f77b7b',
        p2: '#f5a05a',
        p3: '#e6cb5c',
        p4: '#8fb4d6',
        ok: '#4fc08d',
        warn: '#e6b450',
        danger: '#f77b7b',
        'danger-soft': '#3a1a22',
        'warn-soft': '#3a2f14',
        'ok-soft': '#12332a',
        ai: '#b79bf2',
        'ai-soft': '#2a2145'
      },
      fontFamily: {
        sans: [
          'Segoe UI Variable Text',
          'Segoe UI',
          'system-ui',
          '-apple-system',
          'Helvetica Neue',
          'Arial',
          'sans-serif'
        ],
        mono: [
          'ui-monospace',
          'Cascadia Mono',
          'SF Mono',
          'Consolas',
          'Liberation Mono',
          'monospace'
        ]
      },
      opacity: {
        45: '0.45'
      }
    }
  },
  plugins: []
};
