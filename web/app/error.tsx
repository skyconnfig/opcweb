'use client'

import { useEffect } from 'react'
import { RefreshCw, TriangleAlert } from 'lucide-react'

export default function Error({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Keep the failure observable in the browser without exposing internals
    // to the operator-facing recovery screen.
    console.error('AI Lead Radar 页面渲染失败')
  }, [])

  return <main className="product-shell"><section className="page page-error" role="alert"><TriangleAlert size={24} /><h1>页面暂时无法显示</h1><p>当前视图发生了未预期的渲染错误。已有数据不会因此丢失，请重新加载当前页面。</p><button className="button button-accent" onClick={() => reset()}><RefreshCw size={14} />重新加载页面</button></section></main>
}
