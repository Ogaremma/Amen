import type { Config } from 'tailwindcss'
import forms from '@tailwindcss/forms'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        background: '#081120',
        surface: '#111B2E',
        primary: '#0A1F44',
        secondary: '#133A7C',
        accent: '#3B82F6',
        success: '#22C55E',
        muted: '#7B8CA3',
      },
      boxShadow: {
        glow: '0 24px 80px rgba(59, 130, 246, 0.12)',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [forms],
} satisfies Config
