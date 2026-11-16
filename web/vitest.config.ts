import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./', import.meta.url)),
    },
  },
  // The shell exports NODE_ENV=production, which makes React resolve its
  // production build and breaks act(). Force the test build for unit tests.
  define: { 'process.env.NODE_ENV': JSON.stringify('test') },
  test: {
    env: { NODE_ENV: 'test' },
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    exclude: ['tests/e2e/**'],
    css: false,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      thresholds: { lines: 70, functions: 70, branches: 70, statements: 70 },
      include: ['lib/**/*.ts', 'components/report/**/*.tsx', 'components/report/blocks/**/*.tsx'],
      exclude: ['**/*.d.ts', 'tests/**'],
    },
  },
});
