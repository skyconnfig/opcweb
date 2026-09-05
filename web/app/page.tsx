'use client'

import { useEffect, useRef, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Activity, ArrowDownRight, ArrowUpRight, BarChart3, Bot, Check, ChevronDown, ChevronRight, CircleHelp, Clock3, Database, FileText, Gauge, LayoutDashboard, ListChecks, LoaderCircle, Menu, MessageCircle, Moon, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Pause, Play, Plus, Radar, RefreshCw, Search, Settings, SlidersHorizontal, Sparkles, Sun, Target, UserRound, Video, Wifi, X, Zap } from 'lucide-react'

const API = ''
const SCHEDULE_INTERVALS = [10, 15, 20, 25, 30] as const
type RecordShape = Record<string, any>
type ViewKey = 'overview' | 'smart' | 'keywords' | 'videos' | 'comments' | 'leads' | 'replies' | 'knowledge' | 'persona' | 'agents' | 'tasks' | 'analytics' | 'providers' | 'douyin' | 'settings'

const navigation: Array<{ key: ViewKey; label: string; icon: LucideIcon; section?: string }> = [
  { key: 'overview', label: '总览', icon: LayoutDashboard },
  { key: 'smart', label: '智能截流', icon: Sparkles },
  { key: 'keywords', label: '关键词雷达', icon: Radar },
  { key: 'videos', label: '热门视频', icon: Video },
  { key: 'comments', label: '评论池', icon: MessageCircle },
  { key: 'leads', label: '潜客池', icon: Target },
  { key: 'replies', label: 'AI 回复', icon: MessageCircle },
  { key: 'knowledge', label: '知识库', icon: FileText },
  { key: 'persona', label: '人设配置', icon: UserRound },
  { key: 'agents', label: '智能体', icon: Bot, section: '系统' },
  { key: 'tasks', label: '任务中心', icon: ListChecks },
  { key: 'analytics', label: '数据分析', icon: BarChart3 },
  { key: 'providers', label: '数据源', icon: Database, section: '配置' },
  { key: 'douyin', label: '抖音账号', icon: UserRound },
  { key: 'settings', label: '系统设置', icon: Settings },
]

async function request(path: string, options?: RequestInit) {
  const headers = new Headers(options?.headers)
  headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API}${path}`, { ...options, headers })
  if (!response.ok) { const body = await response.json().catch(() => ({})); const detail = Array.isArray(body.detail) ? body.detail.map((item: RecordShape) => `${Array.isArray(item.loc) ? item.loc.join('.') : '参数'}：${item.msg}`).join('；') : typeof body.detail === 'string' ? body.detail : ''; throw new Error([body.message || body.code || `请求失败 ${response.status}`, detail].filter(Boolean).join('：')) }
  return response.json()
}

async function requestWithRetry(path: string, options?: RequestInit, attempts = 3) {
  let lastError: unknown
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try { return await request(path, options) } catch (error) {
      lastError = error
      if (attempt < attempts - 1) await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)))
    }
  }
  throw lastError instanceof Error ? lastError : new Error('请求失败')
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : '请求失败，请稍后重试'
}

function commentCoverageLabel(value?: string) {
  return ({ complete: '完整', partial: '部分', unknown: '待确认' } as Record<string, string>)[value || ''] || value || '待确认'
}

function commentCoverageTone(value?: string) {
  return value === 'complete' ? 'green' : value === 'partial' ? 'amber' : 'neutral'
}

function replyStatusLabel(value?: string) {
  return ({ DRAFT: '草稿', WAITING_REVIEW: '待审核', APPROVED: '已批准', SENDING: '发送中', SENT: '已发送', SENT_UNVERIFIED: '已发送待验证', VERIFIED: '已验证', FAILED: '发送失败', SKIPPED: '已跳过' } as Record<string, string>)[value || ''] || value || '未知'
}

function stepStatusLabel(value?: string) {
  return ({ queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', paused: '已暂停' } as Record<string, string>)[value || ''] || value || '未知'
}

function normalizeSchedule(value: RecordShape) {
  const interval = Number(value?.interval_minutes)
  return { ...value, enabled: Boolean(value?.enabled), full: Boolean(value?.full), interval_minutes: SCHEDULE_INTERVALS.includes(interval as (typeof SCHEDULE_INTERVALS)[number]) ? interval : 30 }
}


export default function Page() {
  const [view, setView] = useState<ViewKey>('overview')
  const [project, setProject] = useState<RecordShape>({})
  const [projects, setProjects] = useState<RecordShape[]>([])
  const [collapsed, setCollapsed] = useState(false)
  const [dark, setDark] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [projectsLoading, setProjectsLoading] = useState(true)
  const [projectsError, setProjectsError] = useState('')

  const loadProjects = async () => {
    setProjectsLoading(true)
    try {
      const items = await request('/api/projects')
      setProjects(items)
      const savedProjectId = Number(window.localStorage.getItem('radar:project'))
      const selected = items.find((item: RecordShape) => item.id === savedProjectId) || items[0]
      if (selected) setProject(selected)
      setProjectsError('')
    } catch (error) {
      setProjectsError(errorText(error))
    } finally {
      setProjectsLoading(false)
    }
  }

  useEffect(() => {
    const savedView = window.location.hash.replace('#', '') as ViewKey
    if (navigation.some((item) => item.key === savedView)) setView(savedView)
    void loadProjects()
  }, [])
  useEffect(() => {
    const savedTheme = window.localStorage.getItem('radar:theme')
    if (savedTheme === 'dark') setDark(true)
  }, [])
  useEffect(() => {
    if (project?.id) window.localStorage.setItem('radar:project', String(project.id))
  }, [project?.id])
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    window.localStorage.setItem('radar:theme', dark ? 'dark' : 'light')
  }, [dark])
  function navigate(next: ViewKey) { setView(next); window.history.replaceState(null, '', `#${next}`); setMobileNav(false) }
  return <div className="product-shell"><Sidebar view={view} navigate={navigate} collapsed={collapsed} setCollapsed={setCollapsed} mobileNav={mobileNav} setMobileNav={setMobileNav} project={project} projects={projects} setProject={setProject} /><main className="workspace"><Topbar view={view} dark={dark} setDark={setDark} setMobileNav={setMobileNav} navigate={navigate} project={project} /><div className="workspace-scroll">{projectsLoading ? <LoadingPage text="正在读取本地工作区…" /> : projectsError ? <ErrorPage message={projectsError} onRetry={() => void loadProjects()} /> : <ViewRouter view={view} project={project} navigate={navigate} onProjectCreated={(created: RecordShape) => { setProject(created); setProjects((current) => [created, ...current.filter((item) => item.id !== created.id)]) }} />}</div></main></div>
}

function Sidebar({ view, navigate, collapsed, setCollapsed, mobileNav, setMobileNav, project, projects, setProject }: RecordShape) {
  return <><div className={`mobile-overlay ${mobileNav ? 'show' : ''}`} onClick={() => setMobileNav(false)} /><aside className={`sidebar ${collapsed ? 'collapsed' : ''} ${mobileNav ? 'mobile-open' : ''}`} aria-label="主导航"><div className="sidebar-top"><div className="wordmark"><span className="wordmark-mark"><Radar size={16} /></span><span className="wordmark-text">AI 截流雷达<small>LEAD RADAR</small></span></div><button className="rail-button close-mobile" onClick={() => setMobileNav(false)} aria-label="关闭导航"><X size={17} /></button><button className="rail-button collapse-button" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? '展开导航' : '收起导航'}>{collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}</button></div><div className="workspace-switcher"><div className="switcher-label">工作区</div><button className="switcher-button" type="button"><span className="project-seal">{(project?.name || '未')[0]}</span><span className="switcher-name">{project?.name || '未选择项目'}<small>本地工作区</small></span><ChevronDown size={14} /></button>{projects.length > 1 && <select className="project-select-hidden" aria-label="切换项目" value={project?.id ?? ''} onChange={(event) => setProject(projects.find((item: RecordShape) => item.id === Number(event.target.value)))}>{projects.map((item: RecordShape) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}</div><nav className="side-nav">{navigation.map((item) => <div key={item.key}>{item.section && <div className="nav-section">{item.section}</div>}<button className={`nav-link ${view === item.key ? 'active' : ''}`} onClick={() => navigate(item.key)}>{<item.icon size={17} strokeWidth={1.8} />}<span>{item.label}</span></button></div>)}</nav><div className="sidebar-bottom"><div className="provider-state"><span className="status-dot" /><span>状态以真实连接为准</span><MoreHorizontal size={15} /></div><div className="account-row"><div className="account-avatar">L</div><div><b>本地工作区</b><small>Windows · 本地运行</small></div><CircleHelp size={15} /></div></div></aside></>
}

function Topbar({ view, dark, setDark, setMobileNav, navigate }: RecordShape) {
  const label = navigation.find((item) => item.key === view)?.label || '总览'
  return <header className="topbar"><div className="topbar-left"><button className="mobile-menu" onClick={() => setMobileNav(true)} aria-label="打开导航"><Menu size={18} /></button><span className="top-context">工作区</span><ChevronRight size={14} className="top-chevron" /><span className="top-current">{label}</span></div><div className="topbar-right"><div className="runtime-chip"><span className="status-dot" />本地 API</div><button className="top-icon-button" onClick={() => navigate('comments')} aria-label="搜索评论" title="搜索评论"><Search size={17} /></button><button className="top-icon-button" onClick={() => setDark(!dark)} aria-label="切换主题">{dark ? <Sun size={17} /> : <Moon size={17} />}</button><div className="top-profile">L</div></div></header>
}

function ViewRouter({ view, project, navigate, onProjectCreated }: RecordShape) {
  if (!project?.id && !['smart', 'providers', 'douyin', 'settings'].includes(view)) return <EmptyWorkspaceView navigate={navigate} />
  if (view === 'smart') return <SmartViewLive project={project} navigate={navigate} onProjectCreated={onProjectCreated} />
  if (view === 'keywords') return <KeywordsViewLive project={project} />
  if (view === 'videos') return <VideosViewLive project={project} />
  if (view === 'comments') return <CommentsView project={project} />
  if (view === 'leads') return <LeadsView project={project} navigate={navigate} />
  if (view === 'replies') return <RepliesView project={project} />
  if (view === 'knowledge') return <KnowledgeView project={project} />
  if (view === 'persona') return <PersonaView project={project} />
  if (view === 'agents') return <AgentsViewLive project={project} />
  if (view === 'tasks') return <TasksView project={project} />
  if (view === 'analytics') return <AnalyticsView project={project} />
  if (view === 'providers') return <ProvidersRegistryView />
  if (view === 'douyin') return <DouyinConnectionView />
  if (view === 'settings') return <SettingsViewLive project={project} />
  return <DashboardLive project={project} navigate={navigate} />
}

function EmptyWorkspaceView({ navigate }: RecordShape) { return <div className="page"><PageHeader eyebrow="WORKSPACE" title="还没有项目" description="创建第一个获客项目，配置文本模型后即可连接真实抖音数据。" actions={<Button variant="accent" icon={Plus} onClick={() => navigate('smart')}>创建项目</Button>} /><section className="panel empty-workspace"><Radar size={28} /><h2>从一个真实业务开始</h2><p>系统不会预置示例项目、视频、评论或潜客。</p></section></div> }

function LoadingPage({ text = '加载中…' }: { text?: string }) { return <div className="page"><section className="panel page-loading"><LoaderCircle size={20} className="loading-spin" /><span>{text}</span></section></div> }
function ErrorPage({ message, onRetry }: { message: string; onRetry: () => void }) { return <div className="page"><section className="panel page-error"><X size={20} /><h2>无法读取工作区</h2><p>{message}</p><Button variant="accent" icon={RefreshCw} onClick={onRetry}>重试</Button></section></div> }

function SmartViewLive({ project, navigate, onProjectCreated }: RecordShape) {
  const [form, setForm] = useState({ name: '我的行业雷达', industry: project.industry || '', location: project.location || '', service: '', price_range: '', target_customer: '', description: '' })
  const [provider, setProvider] = useState('加载中…')
  const [stage, setStage] = useState('')
  const [result, setResult] = useState<RecordShape>()
  const [error, setError] = useState('')
  const [createdProjectId, setCreatedProjectId] = useState<number | null>(null)
  useEffect(() => { request('/api/settings').then((settings) => setProvider(settings.content_provider || '未配置')).catch(() => setProvider('状态未知')) }, [])
  useEffect(() => {
    setForm((current) => ({ ...current, name: project.name || '我的行业雷达', industry: project.industry || '', location: project.location || '', service: project.service || '', price_range: project.price_range || '', target_customer: project.target_customer || '', description: project.description || '' }))
    setCreatedProjectId(null)
    setStage('')
    setResult(undefined)
    setError('')
  }, [project.id])
  const set = (key: string, value: string) => setForm((current) => ({ ...current, [key]: value }))
  async function activate() {
    setError('')
    if (!form.name.trim() || !form.industry.trim()) { setError('请先填写项目名称和行业'); return }
    setResult(undefined); setStage('creating')
    try {
      let projectId = createdProjectId
      if (!projectId) {
        const created = await request('/api/projects', { method: 'POST', body: JSON.stringify({ ...form, name: form.name.trim(), industry: form.industry.trim() }) })
        projectId = Number(created.id)
        setCreatedProjectId(projectId)
        onProjectCreated?.(created)
      }
      setStage('analyzing')
      const analysis = await request(`/api/projects/${projectId}/smart-mode`, { method: 'POST' })
      setResult(analysis)
      setStage('scanning')
      await request(`/api/projects/${projectId}/scan`, { method: 'POST' })
      setStage('done')
      navigate('tasks')
    } catch (err) { setError(errorText(err)); setStage('failed') }
  }
  const progress = stage === 'done' ? 5 : stage === 'scanning' ? 3 : stage === 'analyzing' ? 1 : 0
  const workflow = ['理解行业与客户语言', '生成高意图关键词', '发现并排序机会视频', '分析公开评论信号', '归档潜客并生成建议']
  return <div className="page"><PageHeader eyebrow="INDUSTRY INTELLIGENCE" title="智能截流" description="描述你的业务，系统会生成搜索策略并开始监听公开需求。" actions={<div className="provider-chip"><span className="status-dot" />{provider}</div>} />{error && <div className="error-banner"><X size={15} /><span>{error}</span><button onClick={() => setError('')} aria-label="关闭错误">关闭</button></div>}<div className="smart-grid"><section className="panel form-panel"><PanelHeader label="PROJECT BRIEF" title="业务画像" action={<span className="required-note">* 必填信息</span>} /><div className="form-grid"><Field label="项目名称" required value={form.name} onChange={(value: string) => set('name', value)} placeholder="我的行业雷达" /><Field label="行业" required value={form.industry} onChange={(value: string) => set('industry', value)} placeholder="装修、教育、财税" /><Field label="地区" value={form.location} onChange={(value: string) => set('location', value)} placeholder="例如：长沙" /><Field label="业务 / 产品服务" wide value={form.service} onChange={(value: string) => set('service', value)} placeholder="你具体提供什么服务？" /><Field label="客单价" value={form.price_range} onChange={(value: string) => set('price_range', value)} placeholder="例如：5万-30万" /><Field label="目标客户" wide value={form.target_customer} onChange={(value: string) => set('target_customer', value)} placeholder="谁最可能购买？" /><Field label="补充介绍" wide area value={form.description} onChange={(value: string) => set('description', value)} placeholder="优势、客户痛点、服务限制…" /></div><div className="form-actions"><Button variant="accent" icon={Sparkles} onClick={activate} disabled={Boolean(stage) && stage !== 'failed'}>{stage === 'done' ? '雷达已开启' : stage === 'failed' ? '重试扫描' : stage ? '处理中…' : '分析并开启智能模式'}</Button><span className="form-footnote"><Check size={13} />默认人工审核；自动回复需在设置中显式开启</span></div></section><section className="panel workflow-panel"><PanelHeader label="AUTOMATION PLAN" title="系统将自动完成" /><div className="workflow-list">{workflow.map((label, index) => { const done = index < progress; const current = Boolean(stage) && !done && index === progress; return <div className={`workflow-row ${done ? 'complete' : ''}`} key={label}><span>{done ? <Check size={13} /> : current ? <span className="spinner" /> : String(index + 1).padStart(2, '0')}</span><b>{label}</b>{current && <small className="workflow-current">处理中</small>}</div> })}</div>{result ? <div className="analysis-summary"><div className="summary-number">{result.keyword_count ?? '—'}</div><div><b>个行业关键词已生成</b><span>高机会词将优先进入扫描队列</span></div></div> : <div className="workflow-note"><BrainIcon /><b>准备好后，系统会持续工作</b><span>扫描完成后，你可以在任务中心查看进度和失败重试。</span></div>}</section></div></div>
}

function PageHeader({ eyebrow, title, description, actions }: RecordShape) { return <div className="page-header"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1>{description && <p>{description}</p>}</div>{actions && <div className="header-actions">{actions}</div>}</div> }
function Button({ children, variant = 'secondary', icon: Icon, onClick, disabled, type = 'button', title }: RecordShape) { return <button type={type} className={`button button-${variant}`} onClick={onClick} disabled={disabled} aria-busy={Boolean(disabled)} title={title}>{Icon && <Icon size={15} strokeWidth={1.8} />}{children}</button> }
function StatusPill({ children, tone = 'neutral' }: RecordShape) { return <span className={`status-pill ${tone}`}>{children}</span> }
function SectionLabel({ children }: RecordShape) { return <div className="section-label">{children}</div> }
function Skeleton({ width = '100%' }: { width?: string }) { return <span className="skeleton" style={{ width }} /> }

function DashboardLive({ project, navigate }: RecordShape) {
  const [data, setData] = useState<RecordShape>({ stats: {}, events: [], checklist: { done: 0, total: 6 } })
  const [leadSignals, setLeadSignals] = useState<RecordShape | null>(null)
  const [error, setError] = useState('')
  const [live, setLive] = useState<RecordShape[]>([])
  const [douyin, setDouyin] = useState<RecordShape>({ browser: 'stopped', login: 'NOT_STARTED' })
  useEffect(() => {
    let stopped = false
    const load = async () => {
      try {
        const [dashboard, leads] = await Promise.all([
          request(`/api/dashboard?project_id=${project.id}`),
          request(`/api/leads?project_id=${project.id}`),
        ])
        if (!stopped) {
          setData(dashboard)
          setLeadSignals(deriveLeadSignalRates(leads))
          setError('')
        }
      } catch (err) {
        if (!stopped) setError(errorText(err))
      }
    }
    void load()
    return () => { stopped = true }
  }, [project.id])
  useEffect(() => { request('/api/douyin/status').then(setDouyin).catch(() => {}) }, [])
  useEffect(() => {
    let source: EventSource | undefined
    let retryTimer: number | undefined
    const cursorKey = `radar:last-event-id:${project.id}`
    const savedCursor = Number(window.sessionStorage.getItem(cursorKey) || '0')
    let lastEventId = Number.isSafeInteger(savedCursor) && savedCursor > 0 ? savedCursor : 0
    let stopped = false
    const connect = () => {
      if (stopped) return
      source = new EventSource(`/api/events/stream?project_id=${project.id}&last_event_id=${lastEventId}`)
      source.onmessage = (event) => {
        const nextEventId = Number(event.lastEventId)
        if (Number.isSafeInteger(nextEventId) && nextEventId > lastEventId) {
          lastEventId = nextEventId
          window.sessionStorage.setItem(cursorKey, String(lastEventId))
        }
        try { setLive((current) => [...current.slice(-7), JSON.parse(event.data)]) } catch {}
      }
      source.onerror = () => {
        source?.close()
        if (!stopped) retryTimer = window.setTimeout(connect, 1500)
      }
    }
    connect()
    return () => { stopped = true; source?.close(); if (retryTimer) window.clearTimeout(retryTimer) }
  }, [project.id])
  const stats = data.stats || {}
  const events = [...(data.events || []), ...live].slice(-8)
  const topKeywords = data.top_keywords || []
  const projectName = `${project.location || '未设置地区'} · ${project.industry || '未设置行业'}`
  const done = Number(data.checklist?.done || 0)
  const connectionLabel = douyin.browser === 'running' ? (douyin.login === 'LOGGED_IN' ? '抖音已登录' : `抖音：${douyin.login || '待确认'}`) : '抖音浏览器未启动'
  return <div className="page"><PageHeader eyebrow="OPERATIONS / LIVE" title="总览" description="把公开内容里的购买信号，变成今天可以行动的客户队列。" actions={<><Button icon={RefreshCw} onClick={() => window.location.reload()}>刷新数据</Button><Button variant="accent" icon={Sparkles} onClick={() => navigate('smart')}>开启智能截流</Button></>} />{error && <div className="error-banner"><X size={15} />{error}</div>}<div className="context-bar"><div className="context-main"><span className="context-mark"><Radar size={17} /></span><div><b>{projectName} 行业雷达</b><span>当前使用 {data.mode || '文本数据源'} · 所有判断只基于文字与结构化字段</span></div></div><div className="context-meta"><span><span className="status-dot" />{connectionLabel}</span><span className="context-divider" /><span>数据实时更新</span></div></div><div className="metric-grid"><Metric label="扫描关键词" value={stats.keywords ?? <Skeleton width="36px" />} helper="当前项目" trend="" icon={Radar} /><Metric label="发现视频" value={stats.videos ?? <Skeleton width="36px" />} helper="当前项目" trend="" icon={Video} /><Metric label="公开评论" value={stats.comments ?? <Skeleton width="50px" />} helper="公开文本" trend="" icon={MessageCircle} /><Metric label="新增潜客" value={stats.leads ?? <Skeleton width="36px" />} helper={`${stats.s_leads || 0} 个 S 级机会`} trend="" icon={Target} accent /></div><div className="dashboard-grid"><section className="panel feed-panel"><PanelHeader label="LIVE SIGNAL FEED" title="实时截流雷达" action={<StatusPill tone="live"><span className="status-dot" />真实事件流</StatusPill>} /><div className="feed-list">{events.length ? events.map((event: RecordShape, index: number) => <div className="feed-item" key={`${event.id || 'live'}-${index}`}><time>{formatTime(event.created_at)}</time><span className={`feed-marker ${event.event_type?.includes('lead') ? 'hot' : ''}`} /><div className="feed-copy"><b>{event.message}</b>{event.payload?.keyword && <span>关键词 · {event.payload.keyword}</span>}</div>{event.event_type === 'task.completed' && <Check size={15} className="feed-check" />}</div>) : <EmptyState icon={Activity} text="启动一次智能扫描，实时事件会出现在这里。" action={{ label: '开启扫描', onClick: () => navigate('smart') }} />}</div><div className="panel-footer-link" onClick={() => navigate('tasks')}>查看任务事件 <ArrowUpRight size={14} /></div></section><section className="panel signal-panel"><PanelHeader label="SIGNAL QUALITY" title="机会信号" action={<button className="icon-button" aria-label="机会信号说明" title="基于潜客字段计算"><MoreHorizontal size={16} /></button>} /><div className="signal-total"><div><b>{stats.s_leads || 0}</b><span>S 级潜客</span></div><div className="signal-ring"><span>—</span><small>基于真实数据</small></div></div><div className="signal-bars"><SignalBar label="明确询价" value={leadSignals?.inquiry_rate} color="red" /><SignalBar label="预算表达" value={leadSignals?.budget_rate} color="dark" /><SignalBar label="本地需求" value={leadSignals?.location_rate} color="gray" /></div><div className="insight-line"><Zap size={14} /><span>信号趋势：<b>{leadSignals ? '基于潜客字段实时计算' : '正在读取潜客字段'}</b></span></div></section></div><div className="lower-grid"><section className="panel table-panel compact-table"><PanelHeader label="TOP OPPORTUNITIES" title="最值得扫描的词" action={<button className="text-button" onClick={() => navigate('keywords')}>查看全部 <ArrowUpRight size={13} /></button>} /><table><thead><tr><th>关键词</th><th>机会评分</th><th>潜客</th><th>最近扫描</th></tr></thead><tbody>{topKeywords.length ? topKeywords.slice(0, 4).map((item: RecordShape) => <tr key={item.id || item.keyword}><td><b>{item.keyword}</b><small>{item.category} · AI 推荐</small></td><td><span className="score-value">{Math.round(item.opportunity_score || 0)}</span></td><td><b className="accent-text">{item.lead_count || 0}</b></td><td className="muted">{formatDateTime(item.last_scanned_at)}</td></tr>) : <tr><td colSpan={4}><EmptyState icon={Radar} text="完成一次扫描后，机会词会出现在这里。" /></td></tr>}</tbody></table></section><section className="panel checklist-panel"><PanelHeader label="TODAY" title="工作进度" action={<StatusPill tone="neutral">{done} / 6</StatusPill>} /><div className="checklist"><CheckRow label="理解行业与目标客户" done={Boolean(project.intelligence && Object.keys(project.intelligence).length)} /><CheckRow label="生成关键词雷达" done={Number(stats.keywords) > 0} /><CheckRow label="扫描公开内容" done={Number(stats.videos) > 0} /><CheckRow label="判断购买意向" done={Number(stats.leads) > 0} /><CheckRow label="人工跟进高价值潜客" /><CheckRow label="回填成交结果" /></div></section></div></div>
}
function Metric({ label, value, helper, trend, icon: Icon, accent }: RecordShape) { return <div className="metric"><div className="metric-label"><span>{label}</span><Icon size={15} /></div><div className="metric-value">{value}</div><div className="metric-foot"><span>{helper}</span><b className={accent ? 'positive' : ''}><ArrowUpRight size={12} />{trend}</b></div></div> }
function PanelHeader({ label, title, action }: RecordShape) { return <div className="panel-header"><div><SectionLabel>{label}</SectionLabel><h2>{title}</h2></div>{action}</div> }
function deriveLeadSignalRates(leads: RecordShape[]) {
  const rows = Array.isArray(leads) ? leads : []
  const hasValue = (value: unknown) => {
    const text = String(value || '').trim()
    return Boolean(text) && !/^(未知|待确认|未识别|无)$/.test(text)
  }
  const inquiryCount = rows.filter((lead) => Array.isArray(lead.buying_signals) && lead.buying_signals.some((signal: unknown) => /询价|报价|价格|多少钱|预算/.test(String(signal)))).length
  const budgetCount = rows.filter((lead) => hasValue(lead.budget)).length
  const locationCount = rows.filter((lead) => hasValue(lead.location)).length
  const percentage = (count: number) => rows.length ? Math.round(count / rows.length * 100) : 0
  return { inquiry_rate: percentage(inquiryCount), budget_rate: percentage(budgetCount), location_rate: percentage(locationCount) }
}
function SignalBar({ label, value, color }: RecordShape) {
  const numeric = value == null ? null : Math.max(0, Math.min(100, Number(value)))
  return <div className="signal-bar"><div><span>{label}</span><b>{numeric == null ? '—' : `${numeric}%`}</b></div><i><em className={color} style={{ width: `${numeric ?? 0}%` }} /></i></div>
}
function CheckRow({ label, done }: RecordShape) { return <div className={`check-row ${done ? 'done' : ''}`}><span className="check-box">{done && <Check size={12} />}</span><span>{label}</span>{done ? <small>完成</small> : <small className="next-label">待处理</small>}</div> }
function EmptyState({ icon: Icon, text, action }: RecordShape) { return <div className="empty-state"><Icon size={22} /><span>{text}</span>{action && <button onClick={action.onClick}>{action.label} <ArrowUpRight size={13} /></button>}</div> }
function formatTime(value?: string) { return value ? new Date(value).toLocaleTimeString('zh-CN', { hour12: false }) : '--:--' }
function formatDateTime(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '未安排' }
function taskStatusLabel(status?: string) { return ({ completed: '已完成', queued: '排队中', running: '运行中', paused: '已暂停', failed: '失败' } as Record<string, string>)[status || ''] || status || '未知' }
function taskStatusTone(status?: string) { return status === 'completed' ? 'green' : status === 'failed' ? 'red' : 'amber' }
function downloadJson(filename: string, value: unknown) { const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' }); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url) }

function BrainIcon() { return <span className="brain-icon"><Bot size={22} /></span> }
function Field({ label, value, onChange, placeholder, wide, area, type = 'text', list, required = false, disabled = false }: RecordShape) { return <label className={`field ${wide ? 'wide' : ''}`}><span>{label}{required && <em className="field-required">*</em>}</span>{area ? <textarea value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} rows={3} required={required} disabled={disabled} /> : <input type={type} list={list} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} required={required} disabled={disabled} />}</label> }

function TableSkeleton({ rows }: { rows: number }) { return <div className="table-skeleton">{Array.from({ length: rows }, (_, index) => <div key={index}><Skeleton width="190px" /><Skeleton width="60px" /><Skeleton width="90px" /><Skeleton width="35px" /><Skeleton width="38px" /></div>)}</div> }


function LeadDrawer({ lead, close, project, onUpdated }: RecordShape) {
  const [advice, setAdvice] = useState<RecordShape>()
  const [loading, setLoading] = useState(false)
  const [adviceError, setAdviceError] = useState('')
  const [status, setStatus] = useState(lead.status || 'NEW')
  const [statusBusy, setStatusBusy] = useState(false)
  const [statusMessage, setStatusMessage] = useState('')
  const statuses: Record<string, string> = { NEW: '新发现', FOLLOW_UP: '待跟进', CONTACTED: '已联系', QUALIFIED: '有效客户', WON: '已成交', LOST: '未成交', IGNORED: '忽略' }
  useEffect(() => { setStatus(lead.status || 'NEW') }, [lead.id, lead.status])
  async function generate() { if (!lead.id || typeof lead.id !== 'number') return; setLoading(true); setAdviceError(''); try { setAdvice(await request(`/api/leads/${lead.id}/persona`, { method: 'POST' })) } catch (error) { setAdviceError(errorText(error)) } finally { setLoading(false) } }
  async function changeStatus(next: string) { setStatusBusy(true); setStatusMessage('保存中…'); try { const updated = await request(`/api/leads/${lead.id}`, { method: 'PATCH', body: JSON.stringify({ status: next }) }); setStatus(updated.status); onUpdated?.(updated); setStatusMessage('状态已更新') } catch (error: any) { setStatus(lead.status || 'NEW'); setStatusMessage(error.message) } finally { setStatusBusy(false) } }
  return <div className="drawer-backdrop" onClick={close}><aside className="lead-drawer" onClick={(event) => event.stopPropagation()}><div className="drawer-head"><span className="eyebrow">LEAD PROFILE</span><button className="icon-button" onClick={close}><X size={17} /></button></div><div className="drawer-identity"><span className="drawer-avatar">{(lead.nickname || '客')[0]}</span><div><h2>{lead.nickname || '未提供昵称'}</h2><span>抖音用户 · {lead.platform_user_id || '未提供用户标识'}</span></div><div className="drawer-score"><b>{lead.lead_score == null ? '—' : Math.round(lead.lead_score)}</b><small>{lead.lead_level ? `${lead.lead_level} 级` : '未分级'}</small></div></div><div className="drawer-status"><label htmlFor="lead-status">CRM 状态</label><select id="lead-status" className="select-control" value={status} onChange={(event) => void changeStatus(event.target.value)} disabled={statusBusy}>{Object.entries(statuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>{statusMessage && <span>{statusMessage}</span>}</div><div className="drawer-facts"><div><span>需求</span><b>{lead.need || '未识别'}</b></div><div><span>地区</span><b>{lead.location || project.location || '未识别'}</b></div><div><span>预算</span><b>{lead.budget || '未识别'}</b></div><div><span>阶段</span><b>{lead.purchase_stage || '未识别'}</b></div></div><div className="drawer-section"><SectionLabel>AI SUMMARY</SectionLabel><p>{lead.summary || '暂无 AI 摘要。'}</p></div><div className="drawer-section"><SectionLabel>BUYING SIGNALS</SectionLabel><div className="signal-tags">{lead.buying_signals?.length ? lead.buying_signals.map((signal: string) => <span key={signal}><Check size={12} />{signal}</span>) : <span>暂无已记录信号</span>}</div></div><div className="drawer-section"><SectionLabel>COMMENT CONTEXT</SectionLabel>{lead.comments?.length ? <div className="history-list">{lead.comments.map((comment: RecordShape) => <div key={comment.id}><time>{formatDateTime(comment.created_at_platform || comment.created_at)}</time><p>{comment.content}</p></div>)}</div> : <p>暂无可展示的历史评论上下文。</p>}</div><div className="drawer-section"><SectionLabel>SOURCE VIDEOS</SectionLabel>{lead.videos?.length ? <div className="source-list">{lead.videos.map((video: RecordShape) => <a key={video.id} href={video.url || '#'} target="_blank" rel="noreferrer">{video.title || '无标题视频'} <ArrowUpRight size={12} /></a>)}</div> : <p>暂无来源视频记录。</p>}</div><div className="drawer-advice"><div className="drawer-advice-head"><div><SectionLabel>PERSONA AGENT</SectionLabel><b>人工跟进建议</b></div><Bot size={19} /></div>{advice ? <><blockquote>{advice.recommended_reply}</blockquote><p className="next-question"><b>下一问</b>{advice.follow_up_question}</p></> : <><p>生成一条专业、克制的回答建议，不会自动发送。</p><Button variant="accent" icon={Sparkles} onClick={generate} disabled={loading}>{loading ? '生成中…' : '生成跟进建议'}</Button></>}</div></aside></div>
}

function AgentsViewLive({ project }: RecordShape) {
  const [runs, setRuns] = useState<RecordShape[]>([])
  const [error, setError] = useState('')
  const agents = [{ name: 'IndustryAgent', label: '行业理解', desc: '仅使用行业文字和结构化字段。', icon: BrainIcon }, { name: 'KeywordAgent', label: '关键词发现', desc: '生成 100–300 个文本关键词。', icon: Search }, { name: 'LeadJudgeAgent', label: '潜客判断', desc: '规则预筛后调用文本模型判断意图。', icon: Target }, { name: 'PersonaAgent', label: '人设跟进', desc: '输出仅供人工审核的文本建议。', icon: MessageCircle }]
  useEffect(() => { request(`/api/agent-runs?project_id=${project.id}&limit=40`).then(setRuns).catch((err: any) => setError(err.message)) }, [project.id])
  return <div className="page"><PageHeader eyebrow="AGENT SYSTEM" title="智能体" description="所有 Agent 只处理文字、结构化字段和公开视频元数据。" />{error && <div className="error-banner"><X size={15} />{error}</div>}<div className="agent-grid">{agents.map(({ name, label, desc, icon: Icon }) => <section className="panel agent-card" key={name}><div className="agent-card-top"><span className="agent-glyph"><Icon size={19} /></span><StatusPill tone="neutral">实际运行状态</StatusPill></div><h2>{label}</h2><span className="agent-code">{name} · v1</span><p>{desc}</p><div className="agent-card-foot"><span><Activity size={13} />纯文本模型 · 可观测</span><ChevronRight size={15} /></div></section>)}</div><section className="panel registry-panel"><div><SectionLabel>PROMPT REGISTRY</SectionLabel><h2>每次判断都可追踪</h2><p>记录 prompt version、输入哈希、耗时、token 和结构化输出。</p></div><div className="registry-numbers"><span><b>{runs.length}</b>最近运行</span><span><b>{runs.filter((item) => item.success).length}</b>成功</span></div></section><section className="panel data-panel agent-runs-panel"><div className="data-toolbar"><div><SectionLabel>RUN HISTORY</SectionLabel><h2>文本模型调用记录</h2></div><span className="muted">不保存图片或视频帧</span></div>{runs.length ? <table><thead><tr><th>Agent</th><th>模型</th><th>Prompt</th><th>Tokens</th><th>耗时</th><th>结果</th><th>时间</th></tr></thead><tbody>{runs.map((run: RecordShape) => <tr key={run.id}><td><b>{run.agent}</b></td><td>{run.model || '—'}</td><td>{run.prompt_version || '—'}</td><td>{run.token_usage || 0}</td><td>{run.latency_ms || 0} ms</td><td><StatusPill tone={run.success ? 'green' : 'red'}>{run.success ? '成功' : '失败'}</StatusPill></td><td>{formatDateTime(run.created_at)}</td></tr>)}</tbody></table> : <EmptyState icon={Bot} text="暂无 Agent 运行记录；完成一次智能分析后会显示。" />}</section></div>
}

function CommentsView({ project }: RecordShape) {
  const [items, setItems] = useState<RecordShape[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [coverage, setCoverage] = useState('all')
  const [filter, setFilter] = useState('all')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<RecordShape>()
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')

  const reload = async () => {
    setLoading(true)
    try { setItems(await request(`/api/comments?project_id=${project.id}&limit=500`)); setError('') } catch (err) { setError(errorText(err)) } finally { setLoading(false) }
  }
  useEffect(() => { void reload() }, [project.id])
  useEffect(() => { setSelectedId(null); setDetail(undefined); setError('') }, [project.id])
  const openDetail = async (id: number) => {
    setSelectedId(id); setDetail(undefined); setDetailLoading(true); setDetailError('')
    try { setDetail(await request(`/api/comments/${id}`)) } catch (err) { setDetailError(errorText(err)) } finally { setDetailLoading(false) }
  }
  const refreshDetail = async (id: number) => { try { setDetail(await request(`/api/comments/${id}`)); setDetailError('') } catch (err) { setDetailError(errorText(err)) } }
  const analyze = async (id: number) => { setBusy(id); setError(''); try { await request(`/api/comments/${id}/analyze`, { method: 'POST' }); await reload(); if (selectedId === id) await refreshDetail(id) } catch (err) { setError(errorText(err)) } finally { setBusy(null) } }
  const generate = async (id: number) => { setBusy(id); setError(''); try { await request(`/api/comments/${id}/generate-reply`, { method: 'POST' }); await reload(); if (selectedId === id) await refreshDetail(id) } catch (err) { setError(errorText(err)) } finally { setBusy(null) } }
  const send = async (id: number, text: string) => {
    const replyText = text.trim()
    if (!replyText) { setDetailError('回复文本不能为空'); return }
    if (!window.confirm('确认在真实抖音页面发送这条回复？')) return
    setBusy(id); setDetailError('')
    try { await request(`/api/comments/${id}/reply`, { method: 'POST', body: JSON.stringify({ reply_text: replyText, confirm: true }) }); await reload(); await refreshDetail(id) } catch (err) { setDetailError(errorText(err)) } finally { setBusy(null) }
  }
  const rows = items.filter((item) => {
    const haystack = `${item.nickname || ''} ${item.content || ''} ${item.platform_user_id || ''} ${item.video_id || ''}`.toLowerCase()
    const replyStatus = String(item.reply_status || '')
    const matchesFilter = filter === 'all' || (filter === 'lead' && item.lead_id) || (filter === 's' && item.lead_level === 'S') || (filter === 'a' && item.lead_level === 'A') || (filter === 'waiting' && replyStatus === 'WAITING_REVIEW') || (filter === 'sent' && ['SENT', 'VERIFIED', 'SENT_UNVERIFIED'].includes(replyStatus))
    return (!query.trim() || haystack.includes(query.trim().toLowerCase())) && (coverage === 'all' || item.coverage_status === coverage) && matchesFilter
  })
  return <div className="page"><PageHeader eyebrow="PUBLIC COMMENTS" title="评论池" description="只展示真实同步的公开评论；先查看上下文，再执行 AI 分析或人工确认发送。" actions={<Button icon={RefreshCw} onClick={() => void reload()} disabled={loading}>{loading ? '加载中…' : '刷新'}</Button>} />{error && <div className="error-banner"><X size={15} /><span>{error}</span><button onClick={() => setError('')} aria-label="关闭错误">关闭</button></div>}<div className="comments-toolbar"><label className="table-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索评论、用户或视频 ID" aria-label="搜索评论" /></label><div className="filter-tabs" role="tablist" aria-label="评论状态筛选">{[['all', '全部'], ['lead', '潜客'], ['s', 'S 级'], ['a', 'A 级'], ['waiting', '待审核'], ['sent', '已回复']].map(([key, label]) => <button key={key} className={filter === key ? 'selected' : ''} onClick={() => setFilter(key)}>{label}</button>)}</div><div className="filter-tabs" role="tablist" aria-label="评论覆盖范围筛选">{[['all', '全部覆盖'], ['complete', '完整'], ['partial', '部分'], ['unknown', '待确认']].map(([key, label]) => <button key={key} className={coverage === key ? 'selected' : ''} onClick={() => setCoverage(key)}>{label}</button>)}</div><span className="toolbar-meta">{rows.length} / {items.length} 条评论</span></div><section className="panel data-panel comments-data">{loading ? <TableSkeleton rows={5} /> : rows.length ? <div className="table-scroll"><table><thead><tr><th>用户</th><th>评论</th><th>来源视频</th><th>AI 意图</th><th>潜客分数</th><th>回复状态</th><th>时间</th><th>操作</th></tr></thead><tbody>{rows.map((item: RecordShape) => <tr key={item.id}><td><button className="table-link" onClick={() => void openDetail(item.id)}>{item.nickname || '未知用户'}<small>{item.platform_user_id || '未提供用户标识'}</small></button></td><td className="comment-content"><button className="table-link" onClick={() => void openDetail(item.id)}>{item.content || '无文本内容'}<small><StatusPill tone={commentCoverageTone(item.coverage_status)}>{commentCoverageLabel(item.coverage_status)}</StatusPill></small></button></td><td><span>{item.video_title || `视频 #${item.video_id}`}</span>{item.video_url && <a className="drawer-source-link" href={item.video_url} target="_blank" rel="noreferrer" aria-label="打开来源视频"><ArrowUpRight size={12} /></a>}</td><td>{item.intent_level ? <StatusPill tone={item.lead_level === 'S' ? 'accent' : 'neutral'}>{item.intent_level}</StatusPill> : <span className="muted">未分析</span>}</td><td>{item.lead_score != null ? <b className="lead-score">{Math.round(item.lead_score)}<small>{item.lead_level || '—'}</small></b> : <span className="muted">—</span>}</td><td><StatusPill tone={item.reply_status === 'VERIFIED' ? 'green' : item.reply_status === 'WAITING_REVIEW' ? 'amber' : item.reply_status === 'FAILED' ? 'red' : 'neutral'}>{replyStatusLabel(item.reply_status || '未生成')}</StatusPill></td><td className="muted">{formatDateTime(item.created_at_platform)}</td><td><div className="toolbar-actions"><Button onClick={() => void openDetail(item.id)}>详情</Button><Button onClick={() => void analyze(item.id)} disabled={busy === item.id}>{busy === item.id ? '分析中…' : '分析'}</Button><Button variant="accent" onClick={() => void generate(item.id)} disabled={busy === item.id}>生成回复</Button></div></td></tr>)}</tbody></table></div> : <EmptyState icon={MessageCircle} text={error ? '无法加载真实评论。' : items.length ? '当前筛选没有匹配评论。' : '暂无真实评论，请先连接抖音并同步评论。'} action={error ? { label: '重试', onClick: () => void reload() } : undefined} />}</section>{selectedId && <CommentDrawer detail={detail} loading={detailLoading} error={detailError} close={() => { setSelectedId(null); setDetail(undefined) }} busy={busy === selectedId} onRetry={() => void refreshDetail(selectedId)} onAnalyze={() => void analyze(selectedId)} onGenerate={() => void generate(selectedId)} onSend={(text: string) => void send(selectedId, text)} />}</div>
}

function CommentDrawer({ detail, loading, error, close, onRetry, onAnalyze, onGenerate, onSend, busy }: RecordShape) {
  const [replyText, setReplyText] = useState('')
  const comment = detail?.comment || {}
  const video = detail?.video || {}
  const lead = detail?.lead || {}
  const replies = detail?.replies || []
  useEffect(() => { if (detail?.comment?.id) setReplyText(replies[0]?.reply_text || '') }, [detail?.comment?.id, replies])
  return <div className="drawer-backdrop" onClick={close}><aside className="comment-drawer lead-drawer" onClick={(event) => event.stopPropagation()} aria-label="评论详情"><div className="drawer-head"><span className="eyebrow">COMMENT DETAIL</span><button className="icon-button" onClick={close} aria-label="关闭评论详情"><X size={17} /></button></div>{loading ? <div className="drawer-loading"><LoaderCircle size={20} className="loading-spin" /><span>正在读取评论上下文…</span></div> : error && !detail ? <div className="drawer-error"><X size={18} /><p>{error}</p><Button variant="accent" icon={RefreshCw} onClick={onRetry}>重试</Button></div> : <><div className="comment-identity"><span className="drawer-avatar">{(comment.nickname || '客')[0]}</span><div><h2>{comment.nickname || '未提供昵称'}</h2><span>{comment.platform_user_id || '未提供用户标识'} · {formatDateTime(comment.created_at_platform)}</span></div></div>{error && <div className="error-inline"><X size={14} />{error}</div>}<div className="drawer-comment-quote"><SectionLabel>PUBLIC TEXT</SectionLabel><p>{comment.content || '无文本内容'}</p>{comment.is_reply && <StatusPill tone="neutral">二级回复</StatusPill>}</div>{lead.id ? <div className="drawer-section"><SectionLabel>AI LEAD JUDGEMENT</SectionLabel><div className="drawer-facts"><div><span>潜客评分</span><b>{Math.round(lead.lead_score || 0)} · {lead.lead_level || '—'} 级</b></div><div><span>意向等级</span><b>{lead.intent_level || '—'}</b></div><div><span>需求</span><b>{lead.need || '—'}</b></div><div><span>预算</span><b>{lead.budget || '—'}</b></div><div><span>时间要求</span><b>{lead.time_requirement || '—'}</b></div><div><span>购买阶段</span><b>{lead.purchase_stage || '—'}</b></div></div><p>{lead.reason || lead.summary || '暂无 AI 判断原因'}</p></div> : <div className="drawer-section"><SectionLabel>AI LEAD JUDGEMENT</SectionLabel><p>尚未形成潜客判断，点击“重新分析”开始。</p></div>}<div className="drawer-facts"><div><span>覆盖范围</span><b><StatusPill tone={commentCoverageTone(comment.coverage_status)}>{commentCoverageLabel(comment.coverage_status)}</StatusPill></b></div><div><span>评论赞</span><b>{Number(comment.like_count || 0).toLocaleString()}</b></div><div><span>来源视频</span><b>#{comment.video_id}</b></div><div><span>评论 ID 来源</span><b>{comment.id_source || '—'}</b></div></div><div className="drawer-section"><SectionLabel>THREAD CONTEXT</SectionLabel>{detail.history_text?.length ? <div className="history-list">{detail.history_text.map((text: string, index: number) => <div key={`${index}-${text}`}><time>{index === 0 ? '上下文' : `历史 ${index}`}</time><p>{text}</p></div>)}</div> : <p>暂无同用户或同线程的其他评论。</p>}</div><div className="drawer-section"><SectionLabel>SOURCE VIDEO</SectionLabel><p>{video.title || '无标题视频'}</p><small className="drawer-meta-line">{video.creator || '未知作者'} · 关键词 {video.keyword || '未关联'}</small>{video.url && <a className="drawer-source-link" href={video.url} target="_blank" rel="noreferrer">打开真实视频页面 <ArrowUpRight size={12} /></a>}</div><div className="drawer-section"><SectionLabel>AI / REPLY STATUS</SectionLabel>{replies.length ? <div className="reply-history">{replies.map((reply: RecordShape) => <div key={reply.id}><div><StatusPill tone={reply.status === 'VERIFIED' ? 'green' : reply.status === 'FAILED' ? 'red' : reply.status === 'WAITING_REVIEW' ? 'amber' : 'neutral'}>{replyStatusLabel(reply.status)}</StatusPill><time>{formatDateTime(reply.created_at)}</time></div><p>{reply.reply_text || reply.error_message || '无回复文本'}</p></div>)}</div> : <p>暂无回复记录，可先让文本模型生成草稿。</p>}{!replies.length || ['FAILED', 'SKIPPED'].includes(replies[0]?.status) ? <div className="drawer-actions"><Button onClick={onAnalyze} disabled={busy}>重新分析</Button><Button variant="accent" onClick={onGenerate} disabled={busy}>{busy ? '生成中…' : '生成回复草稿'}</Button></div> : null}</div>{replies[0]?.reply_text && !['VERIFIED', 'SENT', 'SENT_UNVERIFIED', 'SKIPPED'].includes(replies[0]?.status) && <div className="drawer-send"><SectionLabel>HUMAN REVIEW</SectionLabel><textarea className="reply-editor" value={replyText} onChange={(event) => setReplyText(event.target.value)} rows={3} aria-label="编辑待发送回复" placeholder="确认前可编辑回复文本" /><div className="drawer-actions"><Button variant="accent" onClick={() => onSend(replyText)} disabled={busy}>{busy ? '发送中…' : '确认并发送'}</Button></div><small>发送会操作真实抖音页面，必须经过确认；不会使用视觉模型。</small></div>}</>}</aside></div>
}

function RepliesView({ project }: RecordShape) {
  const [items, setItems] = useState<RecordShape[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  const [notice, setNotice] = useState('')
  const reload = async () => { setLoading(true); try { setItems(await request(`/api/replies?project_id=${project.id}`)); setError('') } catch (err: any) { setError(err.message) } finally { setLoading(false) } }
  useEffect(() => { void reload() }, [project.id])
  const beginEdit = (item: RecordShape) => { setEditingId(item.id); setDrafts((current) => ({ ...current, [item.id]: item.reply_text || '' })); setNotice('') }
  const send = async (item: RecordShape) => {
    const replyText = String(drafts[item.id] ?? item.reply_text ?? '').trim()
    if (!replyText) { setError('回复文本不能为空'); return }
    if (!window.confirm('确认批准并在真实抖音页面发送这条回复？')) return
    setBusy(item.id); setError(''); setNotice('')
    try {
      const currentStatus = String(item.status || 'DRAFT')
      if (currentStatus === 'FAILED') await request(`/api/replies/${item.id}`, { method: 'PATCH', body: JSON.stringify({ action: 'retry' }) })
      if (currentStatus !== 'APPROVED') await request(`/api/replies/${item.id}`, { method: 'PATCH', body: JSON.stringify({ action: 'approve', reply_text: replyText }) })
      const result = await request(`/api/comments/${item.comment_id}/reply`, { method: 'POST', body: JSON.stringify({ reply_text: replyText, confirm: true }) })
      setEditingId(null)
      setNotice(result.reply?.status === 'VERIFIED' ? '回复已发送并完成页面验证' : '回复已发送，但尚未完成页面验证')
      await reload()
    } catch (err: any) { setError(err.message); await reload() } finally { setBusy(null) }
  }
  const skip = async (item: RecordShape) => {
    if (!window.confirm('确认跳过这条回复？')) return
    setBusy(item.id); setError(''); setNotice('')
    try { await request(`/api/replies/${item.id}`, { method: 'PATCH', body: JSON.stringify({ action: 'skip' }) }); setNotice('已跳过这条回复'); await reload() } catch (err: any) { setError(err.message) } finally { setBusy(null) }
  }
  const verify = async (item: RecordShape) => {
    if (!window.confirm('重新读取真实抖音页面，核验这条回复是否已经出现？不会再次发送。')) return
    setBusy(item.id); setError(''); setNotice('')
    try {
      const result = await request(`/api/replies/${item.id}/verify`, { method: 'POST' })
      setNotice(result.reply?.status === 'VERIFIED' ? '已从真实页面核验回复' : '页面暂未观察到精确回复文本')
      await reload()
    } catch (err: any) { setError(err.message); await reload() } finally { setBusy(null) }
  }
  const statusLabel: Record<string, string> = { DRAFT: '草稿', WAITING_REVIEW: '待审核', APPROVED: '已批准', SENDING: '发送中', SENT: '已发送', SENT_UNVERIFIED: '已发送待验证', VERIFIED: '已验证', FAILED: '发送失败', SKIPPED: '已跳过' }
  const canSend = (status: string) => ['DRAFT', 'WAITING_REVIEW', 'APPROVED', 'FAILED'].includes(status)
  return <div className="page"><PageHeader eyebrow="REPLY QUEUE" title="AI 回复" description="回复默认进入人工审核队列；批准发送前可编辑文本，发送必须经过明确确认。" actions={<Button icon={RefreshCw} onClick={reload} disabled={loading}>{loading ? '加载中…' : '刷新队列'}</Button>} />{error && <div className="error-banner"><X size={15} />{error}</div>}{notice && <div className="success-banner"><Check size={15} />{notice}</div>}<section className="panel data-panel">{loading ? <TableSkeleton rows={4} /> : items.length ? <table><thead><tr><th>评论</th><th>回复文本</th><th>来源</th><th>状态</th><th>时间</th><th>操作</th></tr></thead><tbody>{items.map((item: RecordShape) => { const status = String(item.status || 'DRAFT'); const isEditing = editingId === item.id; return <tr key={item.id}><td>#{item.comment_id}</td><td>{isEditing ? <textarea className="reply-editor" value={drafts[item.id] ?? ''} onChange={(event) => setDrafts((current) => ({ ...current, [item.id]: event.target.value }))} rows={3} aria-label={`编辑评论 ${item.comment_id} 的回复`} /> : <span>{item.reply_text || item.error_message || '无回复文本'}</span>}{status === 'FAILED' && <small className="reply-error">{item.error_code || 'REPLY_FAILED'} · {item.error_message || '真实回复失败'}</small>}</td><td>{item.reply_source || 'AI'}</td><td><StatusPill tone={status === 'VERIFIED' ? 'green' : status === 'FAILED' ? 'red' : status === 'WAITING_REVIEW' || status === 'SENT_UNVERIFIED' ? 'amber' : 'neutral'}>{statusLabel[status] || status}</StatusPill></td><td>{formatDateTime(item.created_at)}</td><td><div className="toolbar-actions">{canSend(status) && (isEditing ? <><Button onClick={() => setEditingId(null)}>取消</Button><Button variant="accent" onClick={() => void send(item)} disabled={busy === item.id}>{busy === item.id ? '发送中…' : '批准并发送'}</Button></> : <><Button onClick={() => beginEdit(item)} disabled={busy === item.id}>编辑</Button><Button variant="accent" onClick={() => void send(item)} disabled={busy === item.id}>{status === 'FAILED' ? '重试发送' : '批准并发送'}</Button><Button onClick={() => void skip(item)} disabled={busy === item.id}>跳过</Button></>)}{!canSend(status) && status === 'FAILED' && <Button variant="accent" onClick={() => void send(item)} disabled={busy === item.id}>重试发送</Button>}{['SENT_UNVERIFIED', 'SENT'].includes(status) && <Button onClick={() => void verify(item)} disabled={busy === item.id}>{busy === item.id ? '核验中…' : '重新核验'}</Button>}</div></td></tr> })}</tbody></table> : <EmptyState icon={MessageCircle} text={error ? '无法加载真实回复队列。' : '暂无回复记录。'} action={error ? { label: '重试', onClick: reload } : undefined} />}</section></div>
}

function LegacyKnowledgeView({ project }: RecordShape) {
  const [items, setItems] = useState<RecordShape[]>([])
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const reload = async () => { try { setItems(await request(`/api/projects/${project.id}/knowledge`)); setError('') } catch (err: any) { setError(err.message) } }
  useEffect(() => { void reload() }, [project.id])
  const add = async () => { if (!title.trim() || !content.trim()) { setError('标题和事实内容不能为空'); return } try { await request(`/api/projects/${project.id}/knowledge`, { method: 'POST', body: JSON.stringify({ title: title.trim(), content: content.trim(), tags: [], enabled: true }) }); setTitle(''); setContent(''); await reload() } catch (err: any) { setError(err.message) } }
  const edit = (item: RecordShape) => { setEditingId(item.id); setTitle(item.title || ''); setContent(item.content || '') }
  const save = async () => { if (!editingId || !title.trim() || !content.trim()) { setError('标题和事实内容不能为空'); return } try { const current = items.find((item) => item.id === editingId); await request(`/api/knowledge/${editingId}`, { method: 'PUT', body: JSON.stringify({ title: title.trim(), content: content.trim(), tags: current?.tags || [], enabled: current?.enabled !== false }) }); setEditingId(null); setTitle(''); setContent(''); await reload() } catch (err: any) { setError(err.message) } }
  const toggle = async (item: RecordShape) => { try { const updated = await request(`/api/knowledge/${item.id}`, { method: 'PUT', body: JSON.stringify({ title: item.title, content: item.content, tags: item.tags || [], enabled: item.enabled === false }) }); setItems((current) => current.map((row) => row.id === item.id ? updated : row)) } catch (err: any) { setError(err.message) } }
  const remove = async (id: number) => { if (!window.confirm('确认删除这条知识？')) return; try { await request(`/api/knowledge/${id}`, { method: 'DELETE' }); await reload() } catch (err: any) { setError(err.message) } }
  return <div className="page"><PageHeader eyebrow="TEXT KNOWLEDGE" title="知识库" description="提供可核验的产品、价格、服务范围和禁用话术，回复 Agent 只引用文本事实。" />{error && <div className="error-banner"><X size={15} />{error}</div>}<section className="panel form-panel"><PanelHeader label={editingId ? 'EDIT FACT' : 'ADD FACT'} title={editingId ? '编辑事实' : '添加事实'} action={editingId ? <Button onClick={() => { setEditingId(null); setTitle(''); setContent('') }}>取消编辑</Button> : undefined} /><div className="form-grid"><Field label="条目标题" value={title} onChange={setTitle} placeholder="例如：服务范围" /><Field label="事实内容" area value={content} onChange={setContent} placeholder="填写可核验的业务事实，不要写无法兑现的承诺。" /></div><Button variant="accent" icon={editingId ? Check : Plus} onClick={editingId ? save : add}>{editingId ? '保存修改' : '添加知识'}</Button></section><section className="panel data-panel">{items.length ? items.map((item: RecordShape) => <div className="task-item knowledge-item" key={item.id}><div className="task-copy"><b>{item.title}</b><small>{item.content}</small></div><StatusPill tone={item.enabled === false ? 'neutral' : 'green'}>{item.enabled === false ? '已停用' : '已启用'}</StatusPill><Button onClick={() => toggle(item)}>{item.enabled === false ? '启用' : '停用'}</Button><Button onClick={() => edit(item)}>编辑</Button><Button onClick={() => remove(item.id)}>删除</Button></div>) : <EmptyState icon={FileText} text="暂无知识条目。回复 Agent 在知识不足时会强制人工处理。" />}</section></div>
}

function KnowledgeView({ project }: RecordShape) {
  const [items, setItems] = useState<RecordShape[]>([])
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const reload = async () => { setLoading(true); try { setItems(await request(`/api/projects/${project.id}/knowledge`)); setError('') } catch (err) { setError(errorText(err)) } finally { setLoading(false) } }
  useEffect(() => { void reload() }, [project.id])
  const resetEditor = () => { setEditingId(null); setTitle(''); setContent('') }
  const save = async () => {
    if (!title.trim() || !content.trim()) { setError('标题和事实内容不能为空'); return }
    setBusy(true); setError(''); setNotice('')
    try { const current = editingId ? items.find((item) => item.id === editingId) : undefined; await request(editingId ? `/api/knowledge/${editingId}` : `/api/projects/${project.id}/knowledge`, { method: editingId ? 'PUT' : 'POST', body: JSON.stringify({ title: title.trim(), content: content.trim(), tags: current?.tags || [], enabled: current?.enabled !== false }) }); resetEditor(); setNotice(editingId ? '知识条目已更新' : '知识条目已添加'); await reload() } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  const edit = (item: RecordShape) => { setEditingId(item.id); setTitle(item.title || ''); setContent(item.content || ''); setNotice('') }
  const toggle = async (item: RecordShape) => { setBusy(true); try { const updated = await request(`/api/knowledge/${item.id}`, { method: 'PUT', body: JSON.stringify({ title: item.title, content: item.content, tags: item.tags || [], enabled: item.enabled === false }) }); setItems((current) => current.map((row) => row.id === item.id ? updated : row)); setNotice(updated.enabled ? '知识条目已启用' : '知识条目已停用') } catch (err) { setError(errorText(err)) } finally { setBusy(false) } }
  const remove = async (id: number) => { if (!window.confirm('确认删除这条知识？')) return; setBusy(true); try { await request(`/api/knowledge/${id}`, { method: 'DELETE' }); if (editingId === id) resetEditor(); setNotice('知识条目已删除'); await reload() } catch (err) { setError(errorText(err)) } finally { setBusy(false) } }
  return <div className="page"><PageHeader eyebrow="TEXT KNOWLEDGE" title="知识库" description="提供可核验的产品、价格、服务范围和禁用话术，回复 Agent 只引用文本事实。" actions={<Button icon={RefreshCw} onClick={() => void reload()} disabled={loading}>{loading ? '加载中…' : '刷新'}</Button>} />{error && <div className="error-banner"><X size={15} /><span>{error}</span><button onClick={() => setError('')} aria-label="关闭错误">关闭</button></div>}{notice && <div className="success-banner"><Check size={15} /><span>{notice}</span><button onClick={() => setNotice('')} aria-label="关闭提示">关闭</button></div>}<section className="panel form-panel"><PanelHeader label={editingId ? 'EDIT FACT' : 'ADD FACT'} title={editingId ? '编辑事实' : '添加事实'} action={editingId ? <Button onClick={resetEditor}>取消编辑</Button> : undefined} /><div className="form-grid"><Field label="条目标题" required value={title} onChange={setTitle} placeholder="例如：服务范围" disabled={busy} /><Field label="事实内容" required area value={content} onChange={setContent} placeholder="填写可核验的业务事实，不要写无法兑现的承诺。" disabled={busy} /></div><Button variant="accent" icon={editingId ? Check : Plus} onClick={() => void save()} disabled={busy}>{busy ? '保存中…' : editingId ? '保存修改' : '添加知识'}</Button></section><section className="panel data-panel">{loading ? <TableSkeleton rows={3} /> : items.length ? items.map((item: RecordShape) => <div className="task-item knowledge-item" key={item.id}><div className="task-copy"><b>{item.title}</b><small>{item.content}</small></div><StatusPill tone={item.enabled === false ? 'neutral' : 'green'}>{item.enabled === false ? '已停用' : '已启用'}</StatusPill><Button onClick={() => void toggle(item)} disabled={busy}>{item.enabled === false ? '启用' : '停用'}</Button><Button onClick={() => edit(item)} disabled={busy}>编辑</Button><Button onClick={() => void remove(item.id)} disabled={busy}>删除</Button></div>) : <EmptyState icon={FileText} text={error ? '无法加载真实知识条目。' : '暂无知识条目。回复 Agent 在知识不足时会强制人工处理。'} action={error ? { label: '重试', onClick: () => void reload() } : undefined} />}</section></div>
}

function LegacyPersonaView({ project }: RecordShape) {
  const [form, setForm] = useState<RecordShape>({ name: '行业顾问', identity: '本地行业顾问', experience: '', location: '', tone: '专业但不推销', strengths: '', forbidden_words: '', sample_reply: '' })
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const set = (key: string, value: string) => setForm((current) => ({ ...current, [key]: value }))
  useEffect(() => { request(`/api/projects/${project.id}/personas`).then((value) => setForm((current) => ({ ...current, ...value }))).catch((err: any) => setStatus(err.message)) }, [project.id])
  const save = async () => { setBusy(true); setStatus(''); try { const saved = await request(`/api/projects/${project.id}/personas`, { method: 'POST', body: JSON.stringify(form) }); setForm((current) => ({ ...current, ...saved })); setStatus('人设配置已保存，后续建议只会使用文本字段') } catch (err: any) { setStatus(err.message) } finally { setBusy(false) } }
  return <div className="page"><PageHeader eyebrow="TEXT PERSONA" title="人设配置" description="定义回复 Agent 的身份、语气和边界；只用于生成供人工审核的文本建议。" actions={<Button variant="accent" icon={Check} onClick={save} disabled={busy}>{busy ? '保存中…' : '保存人设'}</Button>} />{status && <div className="settings-status page-notice"><span className="status-dot" />{status}</div>}<section className="panel text-model-settings"><div className="settings-heading"><div><SectionLabel>PERSONA AGENT · TEXT ONLY</SectionLabel><h2>跟进人设</h2><p>不要填写密码、联系方式或无法兑现的承诺；系统不会读取图片或视频画面。</p></div><StatusPill tone="green">人工审核</StatusPill></div><div className="settings-form-grid"><Field label="名称" value={form.name || ''} onChange={(value: string) => set('name', value)} placeholder="行业顾问" /><Field label="身份" value={form.identity || ''} onChange={(value: string) => set('identity', value)} placeholder="本地装修顾问" /><Field label="经验" value={form.experience || ''} onChange={(value: string) => set('experience', value)} placeholder="从业年限与擅长领域" /><Field label="地区" value={form.location || ''} onChange={(value: string) => set('location', value)} placeholder="长沙" /><Field label="语气" value={form.tone || ''} onChange={(value: string) => set('tone', value)} placeholder="专业但不推销" /><Field label="优势" value={form.strengths || ''} onChange={(value: string) => set('strengths', value)} placeholder="擅长解决什么问题" /><Field label="禁用词 / 禁止承诺" area value={form.forbidden_words || ''} onChange={(value: string) => set('forbidden_words', value)} placeholder="例如：绝对、全网最低" /><Field label="示例回复" area value={form.sample_reply || ''} onChange={(value: string) => set('sample_reply', value)} placeholder="一条符合人设的示例文本" /></div></section></div>
}

function PersonaView({ project }: RecordShape) {
  const [form, setForm] = useState<RecordShape>({ name: '行业顾问', identity: '本地行业顾问', experience: '', location: '', tone: '专业但不推销', strengths: '', forbidden_words: '', sample_reply: '' })
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const set = (key: string, value: string) => setForm((current) => ({ ...current, [key]: value }))
  const reload = async () => { setLoading(true); try { const value = await request(`/api/projects/${project.id}/personas`); setForm((current) => ({ ...current, ...value })); setError('') } catch (err) { setError(errorText(err)) } finally { setLoading(false) } }
  useEffect(() => { void reload() }, [project.id])
  const save = async () => { if (!String(form.name || '').trim()) { setError('人设名称不能为空'); return } setBusy(true); setError(''); setNotice(''); try { const saved = await request(`/api/projects/${project.id}/personas`, { method: 'POST', body: JSON.stringify({ ...form, name: String(form.name).trim() }) }); setForm((current) => ({ ...current, ...saved })); setNotice('人设配置已保存，后续建议只会使用文本字段') } catch (err) { setError(errorText(err)) } finally { setBusy(false) } }
  return <div className="page"><PageHeader eyebrow="TEXT PERSONA" title="人设配置" description="定义回复 Agent 的身份、语气和边界；只用于生成供人工审核的文本建议。" actions={<><Button icon={RefreshCw} onClick={() => void reload()} disabled={loading || busy}>{loading ? '加载中…' : '刷新'}</Button><Button variant="accent" icon={Check} onClick={() => void save()} disabled={loading || busy}>{busy ? '保存中…' : '保存人设'}</Button></>} />{error && <div className="error-banner"><X size={15} /><span>{error}</span><button onClick={() => setError('')} aria-label="关闭错误">关闭</button></div>}{notice && <div className="success-banner"><Check size={15} /><span>{notice}</span><button onClick={() => setNotice('')} aria-label="关闭提示">关闭</button></div>}<section className="panel text-model-settings"><div className="settings-heading"><div><SectionLabel>PERSONA AGENT · TEXT ONLY</SectionLabel><h2>跟进人设</h2><p>不要填写密码、联系方式或无法兑现的承诺；系统不会读取图片或视频画面。</p></div><StatusPill tone="green">人工审核</StatusPill></div>{loading ? <div className="form-loading"><LoaderCircle size={18} className="loading-spin" />正在读取当前人设…</div> : <div className="settings-form-grid"><Field label="名称" required value={form.name || ''} onChange={(value: string) => set('name', value)} placeholder="行业顾问" disabled={busy} /><Field label="身份" value={form.identity || ''} onChange={(value: string) => set('identity', value)} placeholder="本地装修顾问" disabled={busy} /><Field label="经验" value={form.experience || ''} onChange={(value: string) => set('experience', value)} placeholder="从业年限与擅长领域" disabled={busy} /><Field label="地区" value={form.location || ''} onChange={(value: string) => set('location', value)} placeholder="长沙" disabled={busy} /><Field label="语气" value={form.tone || ''} onChange={(value: string) => set('tone', value)} placeholder="专业但不推销" disabled={busy} /><Field label="优势" value={form.strengths || ''} onChange={(value: string) => set('strengths', value)} placeholder="擅长解决什么问题" disabled={busy} /><Field label="禁用词 / 禁止承诺" area value={form.forbidden_words || ''} onChange={(value: string) => set('forbidden_words', value)} placeholder="例如：绝对、全网最低" disabled={busy} /><Field label="示例回复" area value={form.sample_reply || ''} onChange={(value: string) => set('sample_reply', value)} placeholder="一条符合人设的示例文本" disabled={busy} /></div>}</section></div>
}

function TasksView({ project }: RecordShape) {
  const [tasks, setTasks] = useState<RecordShape[]>([])
  const [schedule, setSchedule] = useState<RecordShape>({ enabled: false, interval_minutes: 30, full: false })
  const [scheduleLoaded, setScheduleLoaded] = useState(false)
  const [scheduleDirty, setScheduleDirty] = useState(false)
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null)
  const [taskDetail, setTaskDetail] = useState<RecordShape>()
  const [detailError, setDetailError] = useState('')
  const [error, setError] = useState('')
  const [scheduleStatus, setScheduleStatus] = useState('')
  const [scheduleStatusError, setScheduleStatusError] = useState(false)
  const [busy, setBusy] = useState(false)
  const [scheduleBusy, setScheduleBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [taskNotice, setTaskNotice] = useState('')
  const scheduleDirtyRef = useRef(false)
  const scheduleRevisionRef = useRef(0)
  const updateSchedule = (patch: RecordShape) => { scheduleDirtyRef.current = true; scheduleRevisionRef.current += 1; setScheduleDirty(true); setSchedule((current) => ({ ...current, ...patch })); setScheduleStatus(''); setScheduleStatusError(false) }
  const refresh = async (showLoading = true) => {
    if (showLoading) setLoading(true)
    const scheduleRevision = scheduleRevisionRef.current
    try {
      const [taskRows, scheduleRow] = await Promise.all([request(`/api/tasks?project_id=${project.id}`), request(`/api/projects/${project.id}/schedule`)])
      setTasks(taskRows)
      if (!scheduleDirtyRef.current && scheduleRevision === scheduleRevisionRef.current) setSchedule(normalizeSchedule(scheduleRow))
      setScheduleLoaded(true)
      setError('')
    } catch (err) { setError(errorText(err)) } finally { if (showLoading) setLoading(false) }
  }
  useEffect(() => {
    scheduleDirtyRef.current = false
    scheduleRevisionRef.current += 1
    setScheduleDirty(false)
    setScheduleLoaded(false)
    setSchedule({ enabled: false, interval_minutes: 30, full: false })
    setScheduleStatus('')
    setScheduleStatusError(false)
    let stopped = false
    const poll = async (showLoading = false) => { if (stopped) return; await refresh(showLoading) }
    void poll(true)
    const timer = window.setInterval(() => { void poll() }, 5000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [project.id])
  useEffect(() => { setSelectedTaskId(null); setTaskDetail(undefined); setDetailError('') }, [project.id])
  useEffect(() => {
    if (!selectedTaskId) { setTaskDetail(undefined); return }
    let stopped = false
    const load = async () => { try { const detail = await request(`/api/tasks/${selectedTaskId}`); if (!stopped) { setTaskDetail(detail); setDetailError('') } } catch (err: any) { if (!stopped) setDetailError(err.message) } }
    void load()
    const timer = window.setInterval(() => { void load() }, 5000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [selectedTaskId])
  const start = async () => { setBusy(true); setTaskNotice(''); try { const result = await request(`/api/projects/${project.id}/scan`, { method: 'POST' }); setTaskNotice(result.task_id ? `扫描已排队，任务 #${result.task_id} 将在任务列表中更新` : '扫描已排队'); await refresh(false) } catch (err) { setError(errorText(err)) } finally { setBusy(false) } }
  const taskAction = async (task: RecordShape, action: 'pause' | 'resume' | 'retry') => { setBusy(true); setTaskNotice(''); try { await request(`/api/tasks/${task.id}/${action}`, { method: 'POST' }); setTaskNotice(action === 'retry' ? '任务已重新排队，将从 checkpoint 继续' : action === 'pause' ? '任务已暂停' : '任务已恢复'); await refresh(false) } catch (err) { setError(errorText(err)) } finally { setBusy(false) } }
  const saveSchedule = async () => {
    const interval = Number(schedule.interval_minutes)
    if (!SCHEDULE_INTERVALS.includes(interval as (typeof SCHEDULE_INTERVALS)[number])) { setScheduleStatusError(true); setScheduleStatus('采集频率只能选择 10、15、20、25 或 30 分钟'); return }
    setScheduleBusy(true); setScheduleStatusError(false); setScheduleStatus(''); scheduleRevisionRef.current += 1
    try {
      const saved = await request(`/api/projects/${project.id}/schedule`, { method: 'PUT', body: JSON.stringify({ enabled: Boolean(schedule.enabled), interval_minutes: interval, full: Boolean(schedule.full) }) })
      const normalized = normalizeSchedule(saved)
      setSchedule(normalized); scheduleDirtyRef.current = false; setScheduleDirty(false); setScheduleStatusError(false); setScheduleStatus(normalized.enabled ? '自动采集计划已启用' : '自动采集计划已关闭')
    } catch (err) { setScheduleStatusError(true); setScheduleStatus(errorText(err)) } finally { setScheduleBusy(false) }
  }
  const scheduleControlsDisabled = !scheduleLoaded || scheduleBusy
  const scheduleStateLabel = scheduleDirty ? '有未保存变更' : schedule.enabled ? '已启用' : '已关闭'
  const scheduleStateTone = scheduleDirty ? 'amber' : schedule.enabled ? 'green' : 'neutral'
  return (
    <div className="page">
      <PageHeader eyebrow="ORCHESTRATION" title="任务中心" description="每次扫描都能暂停、恢复、重试，并从 checkpoint 继续。" actions={<Button variant="accent" icon={Plus} onClick={start} disabled={busy}>{busy ? '提交中…' : '新建扫描'}</Button>} />
      {error && <div className="error-banner" role="alert"><X size={15} /><span>{error}</span><button onClick={() => setError('')} aria-label="关闭错误">关闭</button></div>}{taskNotice && <div className="success-banner" role="status"><Check size={15} /><span>{taskNotice}</span><button onClick={() => setTaskNotice('')} aria-label="关闭提示">关闭</button></div>}
      <div className="task-grid">
        <section className="panel data-panel task-panel">
          <div className="data-toolbar"><div><SectionLabel>SCAN TASKS</SectionLabel><h2>扫描任务</h2></div><StatusPill tone="green">{tasks.length} 个任务 · 每 5 秒更新</StatusPill></div>
          {loading ? <TableSkeleton rows={4} /> : tasks.length ? tasks.map((task: RecordShape) => (
            <div className={`task-item ${selectedTaskId === task.id ? 'selected-task' : ''}`} key={task.id} role="button" tabIndex={0} onClick={() => setSelectedTaskId(task.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelectedTaskId(task.id) } }}>
              <span className={`task-state ${task.status === 'completed' ? '' : 'pending'}`}>{task.status === 'completed' ? <Check size={14} /> : <Clock3 size={14} />}</span>
              <div className="task-copy"><b>{task.name}</b><small title={task.error || undefined}>10 步 · 当前：{task.current_step || '等待启动'}{task.error ? ` · ${task.error}` : ''}</small></div>
              <StatusPill tone={taskStatusTone(task.status)}>{taskStatusLabel(task.status)}</StatusPill>
              {task.status === 'running' && <button className="icon-button" title="暂停" aria-label={`暂停任务 ${task.id}`} disabled={busy} onClick={(event) => { event.stopPropagation(); void taskAction(task, 'pause') }}><Pause size={16} /></button>}
              {task.status === 'paused' && <button className="icon-button" title="恢复" aria-label={`恢复任务 ${task.id}`} disabled={busy} onClick={(event) => { event.stopPropagation(); void taskAction(task, 'resume') }}><Play size={16} /></button>}
              {task.status === 'failed' && <button className="icon-button" title="重试" aria-label={`重试任务 ${task.id}`} disabled={busy} onClick={(event) => { event.stopPropagation(); void taskAction(task, 'retry') }}><RefreshCw size={16} /></button>}
            </div>
          )) : <EmptyState icon={ListChecks} text="还没有扫描任务，先创建一次扫描。" action={{ label: '创建扫描', onClick: start }} />}
        </section>
        <section className="panel checkpoint-panel">
          <div className="checkpoint-heading"><div><SectionLabel>COMMENT COLLECTION</SectionLabel><h2>自动采集</h2></div><StatusPill tone={scheduleStateTone}>{scheduleStateLabel}</StatusPill></div>
          <p>按项目定期扫描关键词并同步公开评论，频率限制为 10～30 分钟。</p>
          <div className="settings-policy-toggles">
            <label className="schedule-toggle"><input type="checkbox" checked={Boolean(schedule.enabled)} disabled={scheduleControlsDisabled} onChange={(event) => updateSchedule({ enabled: event.target.checked })} />启用自动采集</label>
            <label className="schedule-toggle"><input type="checkbox" checked={Boolean(schedule.full)} disabled={scheduleControlsDisabled} onChange={(event) => updateSchedule({ full: event.target.checked })} />扫描全部启用关键词</label>
          </div>
          <div className="settings-form-grid"><label className="field"><span>采集频率</span><select aria-label="采集频率" value={Number(schedule.interval_minutes || 30)} disabled={scheduleControlsDisabled} onChange={(event) => updateSchedule({ interval_minutes: Number(event.target.value) })}><option value={10}>每 10 分钟</option><option value={15}>每 15 分钟</option><option value={20}>每 20 分钟</option><option value={25}>每 25 分钟</option><option value={30}>每 30 分钟</option></select></label></div>
          <div className="settings-actions"><Button variant="accent" onClick={() => void saveSchedule()} disabled={scheduleControlsDisabled}>{scheduleBusy ? '保存中…' : '保存采集计划'}</Button>{scheduleStatus && <span className={`settings-status ${scheduleStatusError ? 'schedule-status-error' : ''}`} role="status" aria-live="polite"><span className="status-dot" />{scheduleStatus}</span>}</div>
          <div className="schedule-meta"><span>下次采集：{schedule.enabled ? (schedule.next_run_at ? formatDateTime(schedule.next_run_at) : '等待安排') : '已关闭'}</span><span>上次采集：{schedule.last_run_at ? formatDateTime(schedule.last_run_at) : '尚未运行'}</span><span>只处理公开文本与结构化字段</span></div>
          <div className="checkpoint-list"><div><span>执行方式</span><b>持久化 Worker</b></div><div><span>事件来源</span><b>数据库 + SSE</b></div><div><span>恢复策略</span><b>关键词级 checkpoint</b></div></div>
          <div className="checkpoint-note"><Check size={14} />任务状态已持久化</div>
        </section>
      </div>
      {selectedTaskId && <section className="panel data-panel task-detail">
        <div className="data-toolbar"><div><SectionLabel>TASK DETAIL</SectionLabel><h2>执行详情 #{selectedTaskId}</h2></div><Button onClick={() => setSelectedTaskId(null)}>收起</Button></div>
        {detailError && <div className="error-inline"><span>{detailError}</span><Button onClick={() => { setDetailError(''); setTaskDetail(undefined); void request(`/api/tasks/${selectedTaskId}`).then(setTaskDetail).catch((err) => setDetailError(errorText(err))) }}>重试</Button></div>}
        {taskDetail ? <div className="task-detail-body">
          <div className="task-detail-summary"><span>状态 <b>{taskDetail.task?.status || '—'}</b></span><span>当前步骤 <b>{taskDetail.task?.current_step || '—'}</b></span><span>错误 <b>{taskDetail.task?.error || '无'}</b></span></div>
          {taskDetail.report?.metrics && <div className="task-report-metrics"><span>视频 <b>{taskDetail.report.metrics.videos || 0}</b></span><span>评论 <b>{taskDetail.report.metrics.comments || 0}</b></span><span>候选判断 <b>{taskDetail.report.metrics.comments_judged || 0}</b></span><span>潜客 <b>{taskDetail.report.metrics.leads || 0}</b></span><span>预筛比例 <b>{Math.round(Number(taskDetail.report.metrics.prefilter_ratio || 0) * 100)}%</b></span></div>}
          <div className="task-step-list">{(taskDetail.steps || []).map((step: RecordShape) => <span key={step.id} className={step.status}>{step.name} · {stepStatusLabel(step.status)}</span>)}</div>
          {taskDetail.events?.length ? <div className="task-events"><SectionLabel>EVENT LOG</SectionLabel>{taskDetail.events.map((event: RecordShape) => <div className="task-event" key={event.id}><time>{formatDateTime(event.created_at)}</time><span>{event.message}</span></div>)}</div> : <div className="task-events-empty">暂无任务事件</div>}
        </div> : <div className="empty-state"><LoaderCircle size={18} className="loading-spin" /><span>正在读取任务详情…</span></div>}
      </section>}
    </div>
  )
}

function AnalyticsView({ project }: RecordShape) {
  const [data, setData] = useState<RecordShape>({ levels: {}, health: {} })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => {
    setLoading(true)
    request(`/api/analytics?project_id=${project.id}`).then((value) => { setData(value); setError('') }).catch((err) => setError(errorText(err))).finally(() => setLoading(false))
  }, [project.id])
  const levels = data.levels || {}
  const health = data.health || {}
  const levelOrder = ['S', 'A', 'B', 'C']
  const total = levelOrder.reduce((sum, level) => sum + Number(levels[level] || 0), 0)
  const palette = ['var(--accent)', '#c7896f', '#d7d8d2', '#bfc3bb']
  let offset = 0
  const donutSegments = levelOrder.map((level, index) => {
    const percentage = total ? Number(levels[level] || 0) / total * 100 : 0
    const segment = `${palette[index]} ${offset}% ${offset + percentage}%`
    offset += percentage
    return segment
  })
  const donutStyle = { background: total ? `conic-gradient(${donutSegments.join(', ')})` : 'var(--line)' }
  const overall = Number(health.overall || 0)
  const coverageStatus = health.comment_coverage_status || 'unknown'
  return <div className="page"><PageHeader eyebrow="SIGNAL ANALYTICS" title="数据分析" description="从真实扫描、评论覆盖和文本判断结果看雷达效率。" actions={<Button icon={ArrowDownRight} onClick={() => downloadJson(`analytics-${project.id}.json`, data)}>导出报告</Button>} />{error && <div className="error-banner"><X size={15} />{error}</div>}{loading ? <section className="panel page-loading"><LoaderCircle size={20} className="loading-spin" /><span>正在读取真实分析数据…</span></section> : <><div className="analytics-grid"><section className="panel quality-panel"><PanelHeader label="LEAD QUALITY" title="潜客等级分布" action={<span className="muted">当前项目</span>} /><div className="quality-content"><div className="donut" style={donutStyle}><div><b>{total}</b><span>潜客总数</span></div></div><div className="legend-list">{levelOrder.map((level, index) => <div key={level}><i className={`legend-dot dot-${index}`} /><span>{level} 级</span><b>{Number(levels[level] || 0)}</b></div>)}</div></div></section><section className="panel health-panel"><PanelHeader label="RADAR HEALTH" title="系统健康度" action={<StatusPill tone={overall >= 80 ? 'green' : 'neutral'}>{overall ? `${overall}/100` : '暂无数据'}</StatusPill>} /><div className="health-number">{overall || '—'}{overall > 0 && <small>/100</small>}</div><div className="health-bars"><HealthBar label="关键词覆盖" value={Number(health.keyword_coverage || 0)} /><HealthBar label={`评论覆盖 · ${commentCoverageLabel(coverageStatus)}`} value={Number(health.comment_coverage || 0)} /><HealthBar label="判断成功率" value={Number(health.judgement_success_rate || 0)} /></div></section></div><section className="panel analytics-note"><Gauge size={18} /><div><b>下一步建议</b><span>{data.next_step || '等待真实扫描数据。'}</span></div><ArrowUpRight size={16} /></section></> }</div>
}
function HealthBar({ label, value }: RecordShape) { return <div><span>{label}</span><i><b style={{ width: `${value}%` }} /></i><strong>{value}%</strong></div> }

function LegacyLeadsView({ project }: RecordShape) {
  const [items, setItems] = useState<RecordShape[]>([])
  const [selected, setSelected] = useState<RecordShape>()
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('all')
  const [loading, setLoading] = useState(true)
  const reload = async () => { setLoading(true); try { setItems(await request(`/api/leads?project_id=${project.id}`)); setError('') } catch (err: any) { setError(err.message) } finally { setLoading(false) } }
  useEffect(() => { void reload() }, [project.id])
  const openLead = async (lead: RecordShape) => { try { setSelected(await request(`/api/leads/${lead.id}`)) } catch (err: any) { setError(err.message); setSelected(lead) } }
  const updateSelected = (updated: RecordShape) => { setSelected((current) => ({ ...current, ...updated })); setItems((current) => current.map((item) => item.id === updated.id ? { ...item, ...updated } : item)) }
  const tabs = [{ key: 'all', label: '全部' }, { key: 'S', label: 'S 级' }, { key: 'A', label: 'A 级' }, { key: 'NEW', label: '待跟进' }]
  const counts: Record<string, number> = { all: items.length, S: items.filter((item) => item.lead_level === 'S').length, A: items.filter((item) => item.lead_level === 'A').length, NEW: items.filter((item) => item.status === 'NEW').length }
  const rows = items.filter((item) => filter === 'all' || (filter === 'NEW' ? item.status === 'NEW' : item.lead_level === filter))
  const statusLabels: Record<string, string> = { NEW: '待跟进', FOLLOW_UP: '跟进中', CONTACTED: '已联系', QUALIFIED: '有效客户', WON: '已成交', LOST: '未成交', IGNORED: '已忽略' }
  return <div className="page"><PageHeader eyebrow="CUSTOMER SIGNALS" title="潜客池" description="把真实购买信号排成一条可以跟进的清晰队列。" actions={<><Button icon={RefreshCw} onClick={reload} disabled={loading}>{loading ? '加载中…' : '刷新'}</Button><Button icon={ArrowDownRight} onClick={() => downloadJson(`leads-${project.id}.json`, items)}>导出列表</Button></>} />{error && <div className="error-banner"><X size={15} />{error}</div>}<div className="lead-tabs">{tabs.map((tab) => <button key={tab.key} className={filter === tab.key ? 'active' : ''} onClick={() => setFilter(tab.key)}>{tab.label} <b>{counts[tab.key]}</b></button>)}<div className="table-search"><Search size={14} />按评分排序</div></div><section className="panel data-panel lead-data">{loading ? <TableSkeleton rows={5} /> : rows.length ? <table><thead><tr><th>潜客</th><th>评分</th><th>需求</th><th>位置 / 预算</th><th>摘要</th><th>出现</th><th>状态</th></tr></thead><tbody>{rows.map((lead: RecordShape, index) => <tr key={lead.id} onClick={() => void openLead(lead)}><td><div className="lead-person"><span className={`lead-avatar lead-${index % 4}`}>{(lead.nickname || '客')[0]}</span><span><b>{lead.nickname || '未提供昵称'}</b><small>抖音 · {lead.platform_user_id || '未知用户'}</small></span></div></td><td><span className={`lead-score score-${(lead.lead_level || 'A').toLowerCase()}`}>{Math.round(lead.lead_score || 0)} <small>{lead.lead_level || 'C'}</small></span></td><td><b>{lead.need || '待补充'}</b><small>{lead.pain_point || '暂无痛点'}</small></td><td><b>{lead.location || '待确认'}</b><small>{lead.budget || '预算待确认'}</small></td><td className="quote">{lead.summary || '暂无摘要'}</td><td><span className="occurrence">{lead.occurrence_count || 1} 次</span></td><td><StatusPill tone={lead.status === 'CONTACTED' ? 'blue' : lead.status === 'NEW' ? 'amber' : 'green'}>{statusLabels[lead.status] || lead.status || '待跟进'}</StatusPill></td></tr>)}</tbody></table> : <EmptyState icon={Target} text={error ? '无法加载真实潜客数据。' : filter === 'all' ? '当前还没有潜客，完成一次扫描后会显示。' : '当前筛选没有匹配的潜客。'} action={error ? { label: '重试', onClick: reload } : undefined} />}</section>{selected && <LeadDrawer lead={selected} close={() => setSelected(undefined)} project={project} onUpdated={updateSelected} />}</div>
}

function LeadsView({ project }: RecordShape) {
  const [items, setItems] = useState<RecordShape[]>([])
  const [selected, setSelected] = useState<RecordShape>()
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const reload = async () => { setLoading(true); try { setItems(await request(`/api/leads?project_id=${project.id}`)); setError('') } catch (err) { setError(errorText(err)) } finally { setLoading(false) } }
  useEffect(() => { void reload() }, [project.id])
  useEffect(() => { setSelected(undefined); setError(''); setFilter('all'); setQuery('') }, [project.id])
  const openLead = async (lead: RecordShape) => { try { setSelected(await request(`/api/leads/${lead.id}`)) } catch (err) { setError(errorText(err)); setSelected(lead) } }
  const updateSelected = (updated: RecordShape) => { setSelected((current) => ({ ...current, ...updated })); setItems((current) => current.map((item) => item.id === updated.id ? { ...item, ...updated } : item)) }
  const tabs = [{ key: 'all', label: '全部' }, { key: 'S', label: 'S 级' }, { key: 'A', label: 'A 级' }, { key: 'NEW', label: '待跟进' }]
  const counts: Record<string, number> = { all: items.length, S: items.filter((item) => item.lead_level === 'S').length, A: items.filter((item) => item.lead_level === 'A').length, NEW: items.filter((item) => item.status === 'NEW').length }
  const rows = items.filter((lead) => { const haystack = `${lead.nickname || ''} ${lead.platform_user_id || ''} ${lead.need || ''} ${lead.location || ''} ${lead.summary || ''}`.toLowerCase(); return (filter === 'all' || (filter === 'NEW' ? lead.status === 'NEW' : lead.lead_level === filter)) && (!query.trim() || haystack.includes(query.trim().toLowerCase())) })
  const statusLabels: Record<string, string> = { NEW: '待跟进', FOLLOW_UP: '跟进中', CONTACTED: '已联系', QUALIFIED: '有效客户', WON: '已成交', LOST: '未成交', IGNORED: '已忽略' }
  return <div className="page"><PageHeader eyebrow="CUSTOMER SIGNALS" title="潜客池" description="把真实购买信号排成一条可以跟进的清晰队列。" actions={<><Button icon={RefreshCw} onClick={() => void reload()} disabled={loading}>{loading ? '加载中…' : '刷新'}</Button><Button icon={ArrowDownRight} onClick={() => downloadJson(`leads-${project.id}.json`, items)}>导出列表</Button></>} />{error && <div className="error-banner"><X size={15} /><span>{error}</span><button onClick={() => setError('')} aria-label="关闭错误">关闭</button></div>}<div className="lead-tabs">{tabs.map((tab) => <button key={tab.key} className={filter === tab.key ? 'active' : ''} onClick={() => setFilter(tab.key)}>{tab.label} <b>{counts[tab.key]}</b></button>)}<label className="table-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索用户、需求或地区" aria-label="搜索潜客" /></label></div><section className="panel data-panel lead-data">{loading ? <TableSkeleton rows={5} /> : rows.length ? <div className="table-scroll"><table><thead><tr><th>潜客</th><th>评分</th><th>需求</th><th>位置 / 预算</th><th>摘要</th><th>出现</th><th>状态</th></tr></thead><tbody>{rows.map((lead: RecordShape, index) => <tr key={lead.id} onClick={() => void openLead(lead)}><td><div className="lead-person"><span className={`lead-avatar lead-${index % 4}`}>{(lead.nickname || '客')[0]}</span><span><b>{lead.nickname || '未提供昵称'}</b><small>抖音 · {lead.platform_user_id || '未知用户'}</small></span></div></td><td><span className={`lead-score score-${(lead.lead_level || 'A').toLowerCase()}`}>{Math.round(lead.lead_score || 0)} <small>{lead.lead_level || 'C'}</small></span></td><td><b>{lead.need || '待补充'}</b><small>{lead.pain_point || '暂无痛点'}</small></td><td><b>{lead.location || '待确认'}</b><small>{lead.budget || '预算待确认'}</small></td><td className="quote">{lead.summary || '暂无摘要'}</td><td><span className="occurrence">{lead.occurrence_count || 1} 次</span></td><td><StatusPill tone={lead.status === 'CONTACTED' ? 'blue' : lead.status === 'NEW' ? 'amber' : 'green'}>{statusLabels[lead.status] || lead.status || '待跟进'}</StatusPill></td></tr>)}</tbody></table></div> : <EmptyState icon={Target} text={error ? '无法加载真实潜客数据。' : items.length ? '当前筛选没有匹配的潜客。' : '当前还没有潜客，完成一次扫描后会显示。'} action={error ? { label: '重试', onClick: () => void reload() } : undefined} />}</section>{selected && <LeadDrawer lead={selected} close={() => setSelected(undefined)} project={project} onUpdated={updateSelected} />}</div>
}

function ProvidersRegistryView() {
  const [items, setItems] = useState<RecordShape[]>([])
  const [activeProvider, setActiveProvider] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const refresh = async () => {
    setBusy(true)
    setLoading(true)
    try {
      const [providers, settings] = await Promise.all([request('/api/providers'), request('/api/settings')])
      setItems(providers)
      setActiveProvider(settings.content_provider || '')
      setError('')
    } catch (err: any) { setError(errorText(err)) } finally { setBusy(false); setLoading(false) }
  }
  useEffect(() => { void refresh() }, [])
  const health = async (provider: RecordShape) => { setBusy(true); try { const updated = await request(`/api/providers/${provider.id}/health`, { method: 'POST' }); setItems((current) => current.map((item) => item.id === updated.id ? updated : item)); setError(''); setNotice(`${provider.name} 连接状态已更新`) } catch (err: any) { setError(errorText(err)) } finally { setBusy(false) } }
  const activate = async (provider: RecordShape) => { setBusy(true); setError(''); setNotice(''); try { const result = await request(`/api/providers/${provider.id}/activate`, { method: 'POST' }); setActiveProvider(result.active || provider.name); setNotice(`已切换到 ${result.active || provider.name}`) } catch (err: any) { setError(errorText(err)) } finally { setBusy(false) } }
  const isActive = (provider: RecordShape) => provider.name === activeProvider || provider.name?.toLowerCase().replace(/\s+/g, '-') === String(activeProvider).toLowerCase()
  return <div className="page"><PageHeader eyebrow="PROVIDER REGISTRY" title="数据源" description="当前版本启用抖音 Playwright；采集来自真实 DOM 和公开文本。" actions={<Button icon={RefreshCw} onClick={() => void refresh()} disabled={busy}>{busy ? '检查中…' : '刷新状态'}</Button>} />{error && <div className="error-banner" role="alert"><X size={15} /><span>{error}</span><Button onClick={() => void refresh()}>重试</Button></div>}{notice && <div className="success-banner" role="status"><Check size={15} /><span>{notice}</span><button onClick={() => setNotice('')} aria-label="关闭提示">关闭</button></div>}{loading ? <div className="panel form-loading"><LoaderCircle size={18} className="loading-spin" /><span>正在读取数据源状态…</span></div> : <>{items.length ? <div className="provider-grid">{items.map((provider: RecordShape, index) => { const active = isActive(provider); return <section className="panel provider-card" key={provider.id || provider.name}><div className="provider-card-head"><span className={`provider-mark provider-${index}`}><Database size={18} /></span><div className="toolbar-actions"><StatusPill tone={active ? 'accent' : provider.status === 'connected' ? 'green' : 'neutral'}>{active ? '当前使用' : provider.status || 'unknown'}</StatusPill>{active && provider.status && <StatusPill tone={provider.status === 'connected' ? 'green' : 'neutral'}>{provider.status}</StatusPill>}</div></div><h2>{provider.name}</h2><p>{provider.note}</p>{provider.endpoint && <code>{provider.endpoint}</code>}<div className="capability-list">{Object.entries(provider.capabilities || {}).map(([key, value]) => <span className={value ? 'on' : ''} key={key}><i />{key.replace(/_/g, ' ')}</span>)}</div><div className="provider-actions"><Button variant="secondary" icon={Wifi} onClick={() => void health(provider)} disabled={busy}>{busy ? '检查中…' : '检查真实连接'}</Button>{!active && <Button variant="accent" onClick={() => void activate(provider)} disabled={busy}>切换为当前源</Button>}</div></section>})}</div> : <EmptyState icon={Database} text="暂无已注册数据源。" action={{ label: '重试', onClick: () => void refresh() }} />}</>}<div className="compliance-bar"><Check size={15} /><span><b>边界声明：</b>抖音采集仍不使用视觉模型；系统不接入视觉模型，不做 OCR、视频帧分析、图片理解或风控绕过。</span></div></div>
}

function DouyinConnectionView() {
  const [state, setState] = useState<RecordShape>({ browser: 'stopped', login: 'NOT_STARTED' })
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(true)
  const [statusLoaded, setStatusLoaded] = useState(false)
  const refresh = async () => { setChecking(true); try { setState(await requestWithRetry('/api/douyin/status')); setStatusLoaded(true); setError('') } catch (err: any) { setError(err.message) } finally { setChecking(false) } }
  useEffect(() => { void refresh() }, [])
  const start = async () => { setChecking(true); try { setState(await request('/api/douyin/browser/start', { method: 'POST' })); setStatusLoaded(true); setError('') } catch (err: any) { setError(err.message) } finally { setChecking(false) } }
  const close = async () => { setChecking(true); try { setState(await request('/api/douyin/browser/close', { method: 'POST' })); setStatusLoaded(true); setError('') } catch (err: any) { setError(err.message) } finally { setChecking(false) } }
  const loggedIn = state.login === 'LOGGED_IN'
  const needsVerification = state.login === 'VERIFICATION_REQUIRED'
  const statusUnavailable = !checking && !statusLoaded
  const statusLabel = checking && !statusLoaded ? '检查中…' : statusUnavailable ? '无法读取会话' : loggedIn ? '已恢复会话' : needsVerification ? '需要人工验证' : state.login
  const actionLabel = checking ? '读取持久化会话…' : statusUnavailable ? '重试检查' : loggedIn ? '复用已保存会话' : needsVerification ? '打开抖音完成验证' : '打开抖音登录'
  const detailLabel = checking && !statusLoaded ? '正在读取持久化会话' : statusUnavailable ? '状态读取失败' : loggedIn ? '已登录（持久化会话）' : needsVerification ? '已检测到平台验证页' : state.login
  const helperText = checking && !statusLoaded ? '正在检查本地持久化 Profile；暂时不会要求重新登录。' : statusUnavailable ? '暂时无法读取后端登录状态；系统没有清除 Cookie，请点击“重试检查”。' : loggedIn ? '已检测到本地持久化会话。重启服务或关闭浏览器后，系统会继续使用同一 Profile，不会主动清除登录态。' : needsVerification ? '抖音当前要求人工完成安全验证。请在打开的真实浏览器中操作；系统不会绕过验证码，验证完成后点击“检查状态”。' : '需要登录时请在打开的真实抖音浏览器中扫码或人工完成验证；系统不会保存明文密码，也不会绕过验证码。'
  return <div className="page"><PageHeader eyebrow="DOUYIN CONNECTION" title="抖音账号" description="通过真实可见浏览器登录；采集只读取 DOM 文本和公开视频元数据。" actions={<Button icon={RefreshCw} onClick={refresh} disabled={checking}>检查状态</Button>} />{error && <div className="error-banner"><X size={15} />{error}</div>}<section className="panel provider-card"><div className="provider-card-head"><span className="provider-mark provider-0"><Database size={18} /></span><StatusPill tone={loggedIn ? 'green' : 'neutral'}>{statusLabel}</StatusPill></div><h2>Douyin Playwright</h2><p>浏览器：{state.browser} · 登录：{detailLabel}</p><code>{state.profile_dir || '本地持久化浏览器 Profile'}</code><div className="provider-actions"><Button variant="accent" icon={Wifi} onClick={start} disabled={checking}>{actionLabel}</Button><Button icon={X} onClick={close} disabled={checking}>关闭浏览器</Button></div><div className="compliance-bar"><Check size={15} /><span>{helperText}</span></div></section></div>
}

function SettingsViewLive({ project }: RecordShape) {
  const [form, setForm] = useState<RecordShape>({ llm_base_url: '', llm_api_key: '', llm_model: 'deepseek-chat', llm_temperature: '0.2', llm_timeout: '45' })
  const [status, setStatus] = useState('')
  const [testing, setTesting] = useState(false)
  const [policy, setPolicy] = useState<RecordShape>({ enabled: true, auto_reply_enabled: false, minimum_confidence: 0.8, minimum_lead_score: 70, allowed_intents: [], blocked_intents: [], max_replies_per_hour: 10, max_replies_per_day: 50, minimum_interval_seconds: 30, auto_reply_own_content_only: false })
  const [policyStatus, setPolicyStatus] = useState('')
  const [policyBusy, setPolicyBusy] = useState(false)
  useEffect(() => { request('/api/settings').then((settings) => setForm((current) => ({ ...current, ...settings, llm_api_key: '' }))).catch((err: any) => setStatus(err.message)) }, [])
  useEffect(() => { if (!project?.id) return; request(`/api/projects/${project.id}/reply-policy`).then(setPolicy).catch((err: any) => setPolicyStatus(err.message)) }, [project?.id])
  const set = (key: string, value: string) => setForm((current) => ({ ...current, [key]: value }))
  const setPolicyValue = (key: string, value: string | boolean) => setPolicy((current) => ({ ...current, [key]: value }))
  const save = async () => { try { await request('/api/settings', { method: 'PUT', body: JSON.stringify(form) }); setStatus('已保存文本 LLM 配置') } catch (err: any) { setStatus(err.message) } }
  const test = async () => { setTesting(true); setStatus('测试连接中…'); try { const result = await request('/api/settings/test-llm', { method: 'POST', body: JSON.stringify(form) }); setStatus(result.message || result.code) } catch (err: any) { setStatus(err.message) } finally { setTesting(false) } }
  const savePolicy = async () => {
    if (!project?.id) { setPolicyStatus('请先创建或选择项目'); return }
    setPolicyBusy(true)
    try {
      const payload = { ...policy, minimum_confidence: Number(policy.minimum_confidence), minimum_lead_score: Number(policy.minimum_lead_score), max_replies_per_hour: Number(policy.max_replies_per_hour), max_replies_per_day: Number(policy.max_replies_per_day), minimum_interval_seconds: Number(policy.minimum_interval_seconds), allowed_intents: String(policy.allowed_intents || '').split(',').map((item: string) => item.trim()).filter(Boolean), blocked_intents: String(policy.blocked_intents || '').split(',').map((item: string) => item.trim()).filter(Boolean) }
      setPolicy(await request(`/api/projects/${project.id}/reply-policy`, { method: 'PUT', body: JSON.stringify(payload) }))
      setPolicyStatus(policy.auto_reply_enabled ? '已保存回复策略；满足全部安全条件后将按策略真实发送' : '已保存回复策略；当前为人工审核草稿模式')
    } catch (err: any) { setPolicyStatus(err.message) } finally { setPolicyBusy(false) }
  }
  const allowedIntents = Array.isArray(policy.allowed_intents) ? policy.allowed_intents.join(', ') : String(policy.allowed_intents || '')
  const blockedIntents = Array.isArray(policy.blocked_intents) ? policy.blocked_intents.join(', ') : String(policy.blocked_intents || '')
  return <div className="page"><PageHeader eyebrow="WORKSPACE SETTINGS" title="系统设置" description="当前版本只接入文本模型；行业理解、关键词、机会评分、潜客判断和回复建议都基于文字与结构化字段。" actions={<Button variant="accent" onClick={save}>保存更改</Button>} /><section className="panel text-model-settings"><div className="settings-heading"><div><SectionLabel>OPENAI COMPATIBLE · TEXT ONLY</SectionLabel><h2>文本模型配置</h2><p>只需配置 Base URL、API Key、Model、Temperature 和 Timeout。DeepSeek、Qwen、GPT 及其他 OpenAI Compatible 文本模型均可接入。</p></div><StatusPill tone="neutral">{form.llm_api_key_configured ? '已配置' : '未配置'}</StatusPill></div><div className="settings-form-grid"><Field label="Base URL" value={form.llm_base_url || ''} onChange={(value: string) => set('llm_base_url', value)} placeholder="https://api.deepseek.com" /><Field label="API Key" type="password" value={form.llm_api_key || ''} onChange={(value: string) => set('llm_api_key', value)} placeholder={form.llm_api_key_configured ? '已保存，留空保持不变' : '仅发送到后端'} /><Field label="Text Model" list="text-models" value={form.llm_model || ''} onChange={(value: string) => set('llm_model', value)} placeholder="deepseek-chat" /><Field label="Temperature" value={String(form.llm_temperature ?? '')} onChange={(value: string) => set('llm_temperature', value)} placeholder="0.2" /><Field label="Timeout (seconds)" value={String(form.llm_timeout ?? '')} onChange={(value: string) => set('llm_timeout', value)} placeholder="45" /></div><div className="settings-actions"><Button icon={Wifi} onClick={test} disabled={testing}>{testing ? '测试中…' : '测试文本连接'}</Button>{status && <span className="settings-status"><span className="status-dot" />{status}</span>}</div></section><section className="panel text-model-settings"><div className="settings-heading"><div><SectionLabel>REPLY SAFETY · HUMAN REVIEW</SectionLabel><h2>回复策略</h2><p>默认人工审核；开启自动回复后，仅在模型判断安全、知识可核验、阈值和限速均通过时，通过真实抖音页面发送。</p></div><StatusPill tone={policy.auto_reply_enabled ? 'amber' : 'green'}>{policy.auto_reply_enabled ? '自动发送已开启' : '人工审核模式'}</StatusPill></div>{!project?.id && <div className="error-inline">请先创建或选择项目后配置项目级回复策略。</div>}<div className="settings-policy-toggles"><label className="schedule-toggle"><input type="checkbox" checked={Boolean(policy.enabled)} onChange={(event) => setPolicyValue('enabled', event.target.checked)} disabled={!project?.id} />启用回复策略</label><label className="schedule-toggle"><input type="checkbox" checked={Boolean(policy.auto_reply_enabled)} onChange={(event) => setPolicyValue('auto_reply_enabled', event.target.checked)} disabled={!project?.id} />启用自动回复模式（满足策略后真实发送）</label><label className="schedule-toggle"><input type="checkbox" checked={Boolean(policy.auto_reply_own_content_only)} onChange={(event) => setPolicyValue('auto_reply_own_content_only', event.target.checked)} disabled={!project?.id} />仅处理自有内容（需平台所有权证据）</label></div><div className="settings-form-grid"><Field label="最低置信度（0-1）" type="number" value={String(policy.minimum_confidence ?? '')} onChange={(value: string) => setPolicyValue('minimum_confidence', value)} placeholder="0.8" /><Field label="最低潜客分数（0-100）" type="number" value={String(policy.minimum_lead_score ?? '')} onChange={(value: string) => setPolicyValue('minimum_lead_score', value)} placeholder="70" /><Field label="每小时最多回复" type="number" value={String(policy.max_replies_per_hour ?? '')} onChange={(value: string) => setPolicyValue('max_replies_per_hour', value)} placeholder="10" /><Field label="每天最多回复" type="number" value={String(policy.max_replies_per_day ?? '')} onChange={(value: string) => setPolicyValue('max_replies_per_day', value)} placeholder="50" /><Field label="最小回复间隔（秒）" type="number" value={String(policy.minimum_interval_seconds ?? '')} onChange={(value: string) => setPolicyValue('minimum_interval_seconds', value)} placeholder="30" /><Field label="允许的意图（逗号分隔）" value={allowedIntents} onChange={(value: string) => setPolicyValue('allowed_intents', value)} placeholder="high, medium" /><Field label="屏蔽的意图（逗号分隔）" value={String(policy.blocked_intents || '')} onChange={(value: string) => setPolicyValue('blocked_intents', value)} placeholder="low, spam" /></div><div className="settings-actions"><Button variant="accent" onClick={savePolicy} disabled={policyBusy || !project?.id}>{policyBusy ? '保存中…' : '保存回复策略'}</Button>{policyStatus && <span className="settings-status"><span className="status-dot" />{policyStatus}</span>}</div></section><datalist id="text-models"><option value="deepseek-chat" /><option value="deepseek-v4-flash" /><option value="deepseek-v4-pro" /><option value="gpt-4o-mini" /><option value="qwen-plus" /></datalist></div>
}

function SettingRow({ title, desc, action }: RecordShape) { return <div className="panel setting-row"><div><h2>{title}</h2><p>{desc}</p></div>{action}</div> }

function KeywordsViewLive({ project }: RecordShape) {
  const [items, setItems] = useState<RecordShape[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [category, setCategory] = useState('全部')
  const [query, setQuery] = useState('')
  const reload = async () => { setLoading(true); try { setItems(await request(`/api/projects/${project.id}/keywords`)); setError('') } catch (err: any) { setError(err.message) } finally { setLoading(false) } }
  useEffect(() => { void reload() }, [project.id])
  const categories = ['全部', '购买意向', '痛点词', '地域词', '长尾词']
  const rows = items.filter((item) => (category === '全部' || item.category === category) && (!query.trim() || String(item.keyword).includes(query.trim())))
  const toggle = async (item: RecordShape) => { try { const updated = await request(`/api/keywords/${item.id}?enabled=${!item.enabled}`, { method: 'PATCH' }); setItems((current) => current.map((row) => row.id === item.id ? updated : row)) } catch (err: any) { setError(err.message) } }
  return <div className="page"><PageHeader eyebrow="OPPORTUNITY ENGINE" title="关键词雷达" description="让购买意图决定扫描优先级，而不是让关键词数量制造噪音。" actions={<span className="provider-chip">{items.length} 个文本关键词</span>} />{error && <div className="error-banner"><X size={15} />{error}</div>}<div className="kpi-strip"><div><b>{items.length}</b><span>关键词总数</span></div><div><b>{items.filter((item) => Number(item.opportunity_score) >= 90).length}</b><span>高机会词</span></div><div><b>{items.length ? Math.round(items.reduce((sum, item) => sum + Number(item.commercial_score || 0), 0) / items.length) : 0}</b><span>平均商业价值</span></div><div className="kpi-note"><Zap size={15} /><span>数据来自当前项目，不使用演示回退</span></div></div><section className="panel data-panel"><div className="data-toolbar"><div className="filter-tabs">{categories.map((item) => <button key={item} className={category === item ? 'selected' : ''} onClick={() => setCategory(item)}>{item}</button>)}</div><input className="table-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索关键词" /></div>{loading ? <TableSkeleton rows={5} /> : rows.length ? <table><thead><tr><th>关键词</th><th>类型</th><th>商业价值</th><th>机会评分</th><th>视频</th><th>评论</th><th>潜客</th><th>状态</th></tr></thead><tbody>{rows.map((item: RecordShape, index) => <tr key={item.id}><td><b>{item.keyword}</b><small>{item.reason || '文本模型推荐'}</small></td><td><StatusPill tone={item.category === '购买意向' ? 'accent' : 'neutral'}>{item.category}</StatusPill></td><td><div className="mini-meter"><i style={{ width: `${item.commercial_score || 0}%` }} /></div><span className="meter-number">{Math.round(item.commercial_score || 0)}</span></td><td><span className={`score-value ${Number(item.opportunity_score) > 90 ? 'hot' : ''}`}>{Math.round(item.opportunity_score || 0)}</span></td><td>{item.video_count || 0}</td><td>{item.comment_count || 0}</td><td><b className="accent-text">{item.lead_count || 0}</b></td><td><button className="enabled" onClick={() => toggle(item)}><i />{item.enabled ? '已启用' : '已停用'}</button></td></tr>)}</tbody></table> : <EmptyState icon={Radar} text={error ? '无法加载真实关键词。' : '当前项目还没有关键词，请先运行智能截流。'} />}</section></div>
}

function VideosViewLive({ project }: RecordShape) {
  const [items, setItems] = useState<RecordShape[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [sort, setSort] = useState('机会排序')
  const [selected, setSelected] = useState<RecordShape>()
  const [detail, setDetail] = useState<RecordShape>()
  const [detailLoading, setDetailLoading] = useState(false)
  const [syncBusy, setSyncBusy] = useState(false)
  const reload = async () => { setLoading(true); try { setItems(await request(`/api/videos?project_id=${project.id}`)); setError('') } catch (err: any) { setError(err.message) } finally { setLoading(false) } }
  useEffect(() => { void reload() }, [project.id])
  useEffect(() => { setSelected(undefined); setDetail(undefined); setError('') }, [project.id])
  const scan = async () => { setBusy(true); setError(''); try { await request(`/api/projects/${project.id}/scan?full=true`, { method: 'POST' }); await reload() } catch (err: any) { setError(errorText(err)) } finally { setBusy(false) } }
  const openDetails = async (item: RecordShape) => { setSelected(item); setDetail(undefined); setDetailLoading(true); try { const [video, comments] = await Promise.all([request(`/api/videos/${item.id}`), request(`/api/comments?project_id=${project.id}&limit=500`)]); setDetail({ video, comments: comments.filter((comment: RecordShape) => comment.video_id === item.id) }); setError('') } catch (err) { setError(errorText(err)) } finally { setDetailLoading(false) } }
  const syncComments = async () => { if (!selected?.id) return; setSyncBusy(true); setError(''); try { await request(`/api/douyin/videos/${selected.id}/comments/sync`, { method: 'POST' }); await openDetails(selected); } catch (err) { setError(errorText(err)) } finally { setSyncBusy(false) } }
  const rows = [...items].sort((a, b) => sort === '最新发现' ? String(b.discovered_at || '').localeCompare(String(a.discovered_at || '')) : sort === '评论密度' ? Number(b.comments || 0) - Number(a.comments || 0) : Number(b.opportunity_score || 0) - Number(a.opportunity_score || 0))
  return <div className="page"><PageHeader eyebrow="CONTENT RADAR" title="热门视频" description="只依据标题、描述、作者和公开互动数据判断机会，不读取画面。" actions={<Button variant="accent" icon={RefreshCw} onClick={scan} disabled={busy}>{busy ? '扫描排队中…' : '扫描全部'}</Button>} />{error && <div className="error-banner"><X size={15} />{error}</div>}<div className="video-toolbar"><div className="filter-tabs">{['机会排序', '最新发现', '评论密度'].map((item) => <button key={item} className={sort === item ? 'selected' : ''} onClick={() => setSort(item)}>{item}</button>)}</div><span className="toolbar-meta"><span className="status-dot" />{items.length} 个真实视频记录</span></div>{loading ? <TableSkeleton rows={6} /> : rows.length ? <div className="video-list">{rows.map((item: RecordShape, index) => <article className="video-row" key={item.id} role="button" tabIndex={0} onClick={() => void openDetails(item)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); void openDetails(item) } }}><div className={`video-thumb thumb-${index % 4}`}><span>TEXT / META</span><b>{item.level || 'C'}<br /><em>机会记录</em></b><i aria-hidden="true">↗</i></div><div className="video-info"><div className="video-title-row"><h3>{item.title || '无标题视频'}</h3><StatusPill tone={item.level === 'A' || item.level === 'S' ? 'accent' : 'neutral'}>{item.level || 'C'} 级机会</StatusPill></div><div className="video-source"><span className="creator-dot">{(item.creator || '匿')[0]}</span>{item.creator || '未知作者'}<span>·</span><span>{item.keyword || '未关联关键词'}</span></div><div className="video-stats"><span>赞 {Number(item.likes || 0).toLocaleString()}</span><span>评论 {Number(item.comments || 0).toLocaleString()}</span><span>收藏 {Number(item.collects || 0).toLocaleString()}</span></div></div><div className="video-score"><span>机会评分</span><b>{Math.round(item.opportunity_score || 0)}</b><small>行业相关度 {Math.round(item.industry_relevance_score || 0)}%</small></div></article>)}</div> : <EmptyState icon={Video} text={error ? '无法加载真实视频。' : '当前还没有视频记录，请先执行扫描。'} />}{selected && <div className="drawer-backdrop" onClick={() => setSelected(undefined)}><aside className="comment-drawer lead-drawer" onClick={(event) => event.stopPropagation()} aria-label="视频详情"><div className="drawer-head"><span className="eyebrow">VIDEO DETAIL</span><button className="icon-button" onClick={() => setSelected(undefined)} aria-label="关闭视频详情"><X size={17} /></button></div>{detailLoading ? <div className="drawer-loading"><LoaderCircle size={20} className="loading-spin" /><span>正在读取视频详情…</span></div> : detail ? <><div className="drawer-section"><SectionLabel>PUBLIC METADATA</SectionLabel><h2>{detail.video.title || '无标题视频'}</h2><p>{detail.video.description || '暂无公开描述'}</p><small className="drawer-meta-line">作者：{detail.video.creator || '未知作者'} · 关键词：{detail.video.keyword || '未关联'}</small><small className="drawer-meta-line">发布时间：{formatDateTime(detail.video.publish_time)} · 赞：{Number(detail.video.likes || 0).toLocaleString()} · 评论：{Number(detail.video.comments || 0).toLocaleString()} · 分享：{Number(detail.video.shares || 0).toLocaleString()} · 收藏：{Number(detail.video.collects || 0).toLocaleString()}</small>{detail.video.url && <a className="drawer-source-link" href={detail.video.url} target="_blank" rel="noreferrer">打开真实视频页面 <ArrowUpRight size={12} /></a>}</div><div className="drawer-facts"><div><span>机会评分</span><b>{Math.round(detail.video.opportunity_score || 0)}</b></div><div><span>行业相关度</span><b>{Math.round(detail.video.industry_relevance_score || 0)}%</b></div><div><span>已入库评论</span><b>{detail.comments.length}</b></div><div><span>潜客机会</span><b>{Math.round(detail.video.lead_opportunity_score || 0)}</b></div></div><div className="drawer-section"><SectionLabel>COMMENT COLLECTION</SectionLabel><p>只同步公开评论文本和结构化字段，覆盖范围以真实 Provider 返回为准。</p><Button variant="accent" onClick={() => void syncComments()} disabled={syncBusy}>{syncBusy ? '同步中…' : '同步此视频评论'}</Button></div></> : <div className="drawer-error"><X size={18} /><p>视频详情读取失败，请关闭后重试。</p></div>}</aside></div>}</div>
}
