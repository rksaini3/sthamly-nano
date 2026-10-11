const c = (name) => `rgb(var(--${name}) / <alpha-value>)`

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      // Every color is a CSS variable (app/globals.css), so the whole site is re-themed in one place.
      colors: {
        bg: c('bg'),
        term: c('term'),
        surface: c('surface'),
        raised: c('raised'),
        line: c('line'),
        ink: c('ink'),
        muted: c('muted'),
        brand: c('brand'),
        'brand-ink': c('brand-ink'),
        accent: c('accent'),
        good: c('good'),
        warn: c('warn'),
        bad: c('bad'),
        flat: c('flat'),
      },
      fontFamily: {
        display: ['"Bricolage Grotesque"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        sans: ['"IBM Plex Sans"', 'ui-sans-serif', 'system-ui', '"Segoe UI"', 'Roboto', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
