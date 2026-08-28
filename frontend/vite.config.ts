import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // Installable-app + offline app shell. Emits `manifest.webmanifest` and a
    // Workbox-generated `sw.js` into `dist/` at build time; `main.tsx` registers it.
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'Jarvis',
        short_name: 'Jarvis',
        description: 'Personal AI assistant',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        background_color: '#05070d',
        theme_color: '#05070d',
        icons: [
          // Derived from public/favicon.png (1254x1254) -- see public/pwa-*.png.
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
        ],
      },
      workbox: {
        // App shell only: the hashed JS/CSS bundles and index.html. The plugin adds
        // manifest.webmanifest and the two icons it references on top of this; the
        // 556 KB favicon.png is deliberately left out of the precache.
        globPatterns: ['**/*.{js,css,html}'],
        navigateFallback: 'index.html',
        // API calls must always hit the network -- never be answered with the
        // cached index.html. In dev that's the proxy below; in prod it's
        // VITE_API_BASE_URL (cross-origin, so out of the SW's precache scope anyway).
        navigateFallbackDenylist: [/^\/api\//],
        cleanupOutdatedCaches: true,
      },
      // The SW is a production artifact; leaving it off in `npm run dev` keeps the
      // Vite proxy and HMR free of a caching layer.
      devOptions: { enabled: false },
    }),
  ],
  server: {
    host: '127.0.0.1',
    proxy: {
      // Forward API calls to the FastAPI backend during development.
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
