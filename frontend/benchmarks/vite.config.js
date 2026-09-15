import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({base:'./',plugins:[react()],build:{outDir:process.env.PERF_DIST||'perf-dist',rollupOptions:{input:'benchmarks/editor.html'}}});
