import type { NextConfig } from 'next'
import { PHASE_DEVELOPMENT_SERVER } from 'next/constants'

const nextConfig = (phase: string): NextConfig => ({
  reactStrictMode: true,
  output: 'standalone',
  // Keep the dev compiler cache separate from the production build cache.
  // Running npm run build must not invalidate an active local console.
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? '.next-dev' : '.next',
  // The desktop app opens the local console through 127.0.0.1 while Next's
  // dev server is usually started on localhost. Keep HMR/static asset
  // requests on both explicit local origins.
  allowedDevOrigins: ['127.0.0.1', 'localhost'],
})
export default nextConfig
