/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        medical: {
          50: '#F0F7FF',
          100: '#E0EFFF',
          500: '#1890FF',
          600: '#096DD9',
          700: '#0050B3',
        }
      }
    },
  },
  plugins: [],
}
