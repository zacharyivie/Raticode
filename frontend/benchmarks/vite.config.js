import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {monacoPatches} from '../dev/monaco-patches.js';
export default defineConfig({base:'./',plugins:[react(),monacoPatches()],build:{outDir:process.env.PERF_DIST||'perf-dist',rollupOptions:{input:'benchmarks/editor.html'}}});
