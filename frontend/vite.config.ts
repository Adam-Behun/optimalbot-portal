import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  
  // Path aliases for @/* imports
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },

  // Proxy configuration for FastAPI backend
  server: {
    port: 3000,
    proxy: {
      '/patients': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/add-patient': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/add-patients-bulk': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/start-call': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/end-call': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },

  // Production build configuration - OUTPUT TO 'build' FOR DOCKER
  build: {
    outDir: 'build',  // Changed from 'dist' to match your Docker/FastAPI setup
    emptyOutDir: true,
    sourcemap: true,
    // Optimize chunk size
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'data-vendor': ['axios', 'papaparse'],
        },
      },
    },
    // Increase chunk size warning limit for healthcare app
    chunkSizeWarningLimit: 1000,
  },

  // Optimize dependencies
  optimizeDeps: {
    include: ['react', 'react-dom', 'react-router-dom', 'axios', 'papaparse'],
  },
});
