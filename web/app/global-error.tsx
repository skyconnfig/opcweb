'use client'

import { RefreshCw, TriangleAlert } from 'lucide-react'

const styles = {
  body: { margin: 0, minHeight: '100vh', background: '#f4f4f1', color: '#242523', fontFamily: '"Avenir Next", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif' },
  main: { minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24 },
  panel: { width: 'min(520px, 100%)', padding: 32, border: '1px solid #e2e3de', borderRadius: 14, background: '#fbfbf9', textAlign: 'center' as const, boxShadow: '0 6px 20px rgba(35, 36, 28, .06)' },
  icon: { color: '#b44d35' },
  title: { margin: '14px 0 8px', fontSize: 22 },
  copy: { margin: 0, color: '#858780', fontSize: 13, lineHeight: 1.6 },
  button: { marginTop: 22, display: 'inline-flex', alignItems: 'center', gap: 7, border: 0, borderRadius: 8, padding: '9px 13px', background: '#b44d35', color: '#fff', cursor: 'pointer', font: 'inherit', fontSize: 12 },
}

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <html lang="zh-CN"><body style={styles.body}><main style={styles.main} role="alert"><section style={styles.panel}><TriangleAlert size={28} style={styles.icon} /><h1 style={styles.title}>应用暂时无法加载</h1><p style={styles.copy}>应用外壳发生了未预期的错误。请重新加载，或检查本地 Web 服务是否仍在运行。</p><button style={styles.button} onClick={() => reset()}><RefreshCw size={14} />重新加载应用</button></section></main></body></html>
}
