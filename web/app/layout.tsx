import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = { title: 'AI 截流雷达', description: '行业获客信号工作台' }

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>
}
