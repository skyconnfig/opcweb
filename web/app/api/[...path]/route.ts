import { NextRequest, NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

type RouteContext = { params: Promise<{ path: string[] }> }

function upstreamUrl() {
  return (process.env.API_URL || 'http://127.0.0.1:8689').replace(/\/$/, '')
}

async function forward(request: NextRequest, context: RouteContext) {
  const { path } = await context.params
  const target = new URL(`${upstreamUrl()}/api/${path.join('/')}`)
  request.nextUrl.searchParams.forEach((value, key) => target.searchParams.set(key, value))

  const headers = new Headers(request.headers)
  headers.delete('host')
  headers.delete('content-length')
  headers.delete('connection')
  const token = process.env.API_AUTH_TOKEN
  if (token) headers.set('authorization', `Bearer ${token}`)

  const body = request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.arrayBuffer()
  const response = await fetch(target, { method: request.method, headers, body, cache: 'no-store' })
  const responseHeaders = new Headers()
  for (const name of ['content-type', 'cache-control', 'content-encoding', 'etag', 'last-modified']) {
    const value = response.headers.get(name)
    if (value) responseHeaders.set(name, value)
  }
  return new NextResponse(response.body, { status: response.status, headers: responseHeaders })
}

export const GET = forward
export const POST = forward
export const PUT = forward
export const PATCH = forward
export const DELETE = forward
