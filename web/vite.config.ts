import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: run the backend with `JOBHUNT_TOKEN=dev python -m jobhunt --port 8765 --no-browser`
// and this proxies /api to it. Production: the backend serves web/dist itself.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
})
