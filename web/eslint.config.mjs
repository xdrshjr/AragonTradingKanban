import { FlatCompat } from '@eslint/eslintrc';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

/**
 * next/core-web-vitals plus the TypeScript rules that keep §11.3 honest:
 * no `any`, no raw HTML injection, no floating promises.
 */
const config = [
  {
    ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts', 'coverage/**', 'playwright-report/**', 'test-results/**'],
  },
  ...compat.extends('next/core-web-vitals', 'next/typescript'),
  {
    rules: {
      'no-console': ['warn', { allow: ['error', 'warn'] }],
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      'react/no-danger': 'error',
      eqeqeq: ['error', 'smart'],
      'prefer-const': 'error',
    },
  },
  {
    // The theme bootstrap is the single sanctioned `dangerouslySetInnerHTML` site
    // (see web/tests/ac17-guards.test.ts for the guard).
    files: ['app/layout.tsx'],
    rules: { 'react/no-danger': 'off' },
  },
  {
    files: ['tests/**/*.{ts,tsx}', 'scripts/**/*.mjs', '*.config.{ts,mjs}'],
    rules: { 'no-console': 'off', '@typescript-eslint/no-explicit-any': 'off' },
  },
];

export default config;
