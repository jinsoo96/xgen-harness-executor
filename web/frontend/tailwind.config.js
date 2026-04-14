/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg: {
          primary: '#0c0c0c',
          secondary: '#141414',
          tertiary: '#1c1c1c',
        },
        accent: {
          DEFAULT: '#c8a45c',
          glow: '#d4a876',
        },
        stage: {
          ingress: '#3b82f6',
          loop: '#8b5cf6',
          egress: '#f43f5e',
        },
      },
      fontFamily: {
        serif: ['Playfair Display', 'serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      animation: {
        'pulse-glow': 'pulse-glow 2.5s ease-in-out infinite',
      },
      keyframes: {
        'pulse-glow': {
          '0%, 100%': { opacity: '0.6' },
          '50%': { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
