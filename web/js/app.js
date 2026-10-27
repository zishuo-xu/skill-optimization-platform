/**
 * Application shell + hash router.
 *
 * Routes
 *   #/projects
 *   #/projects/:pid/overview|skill|environment|dataset|versions
 *   #/runs/:rid[/result|cases|diff|log]
 */

import { api, isMockMode } from './api_v3.js';
import { h, mount, icon, focusElement, clear } from './dom.js';
import { button, statusBadge, emptyState, errorState, loadingState, toast, confirmDialog, closeAllOverlays } from './components.js';
import { relativeTime, shortenId, modeLabel } from './format.js';
import { RUN_STATUS_META, MODE_META } from './eventTypes.js';

import projectsView from './views/projects.js';
import overviewView from './views/overview.js';
import skillView from './views/skill.js';
import environmentView from './views/environment.js';
import datasetView from './views/dataset.js';
import runDetailsView from './views/runDetails.js';
import versionsView from './views/versions.js';

const APP_TITLE = 'Skill 优化平台';
const viewRoot = () => document.getElementById('view');
const SIDEBAR_STORAGE_KEY = 'skillopt.sidebarCollapsed';

const state = {
  currentCleanup: null,
  dirtyMessage: null,
  lastHash: location.hash || '#/projects',
  lastRenderedHash: '',
  skipGuardOnce: false,
  health: 'unknown',
  renderToken: 0,
  project: null,
  run: null,
  recentRuns: [],
};

/* ------------------------------------------------------------------ */
/* dirty-navigation guard                                              */
/* ------------------------------------------------------------------ */

function hasUnsavedChanges() {
  return Boolean(state.dirtyMessage);
}

export function setDirty(message) {
  state.dirtyMessage = message || null;
}

async function confirmLeave() {
  const confirmed = await confirmDialog({
    title: '有未保存的修改',
    message: state.dirtyMessage || '离开当前页面会丢失未保存的修改，确定要离开吗？',
    confirmText: '离开并丢弃',
    cancelText: '留在此页',
    danger: true,
  });
  if (confirmed) {
    state.dirtyMessage = null;
    return true;
  }
  return false;
}

window.addEventListener('beforeunload', (event) => {
  if (!hasUnsavedChanges()) return;
  event.preventDefault();
  event.returnValue = '';
});

document.addEventListener('click', async (event) => {
  const anchor = event.target instanceof Element ? event.target.closest('a[href^="#/"]') : null;
  if (!anchor || anchor.target === '_blank' || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  if (anchor.dataset.bypassGuard === 'true') return;
  const targetHash = anchor.getAttribute('href');
  if (!targetHash || targetHash === location.hash) {
    if (targetHash === location.hash) event.preventDefault();
    return;
  }
  event.preventDefault();
  if (hasUnsavedChanges() && !(await confirmLeave())) return;
  location.hash = targetHash;
});

/* ------------------------------------------------------------------ */
/* shell                                                               */
/* ------------------------------------------------------------------ */

function buildShell() {
  const app = document.getElementById('app');
  const sidebar = h(
    'aside.sidebar',
    { 'aria-label': '主导航' },
    h(
      'div.brand',
      null,
      h('span.brand-mark', { 'aria-hidden': 'true' }, 'S'),
      h('span.brand-text', null, h('strong', { text: APP_TITLE }), h('small', { text: '单轮评测与优化' }))
    ),
    h('div', { class: 'sidebar__scroll', id: 'sidebar-nav' }),
    h('div', { class: 'sidebar__footer', id: 'sidebar-footer' })
  );
  const topbar = h('header.topbar', { id: 'topbar' });
  const main = h('main', { class: 'content', id: 'view', tabindex: '-1' });
  mount(app, sidebar, h('div.app-main', null, topbar, main));

  document.getElementById('sidebar-nav').appendChild(buildSidebarNav());
  document.getElementById('sidebar-footer').appendChild(buildSidebarFooter());

  const menuButton = h(
    'button.btn.btn--ghost.btn--icon.topbar__menu',
    {
      type: 'button',
      title: '收起 / 展开导航',
      'aria-label': '收起或展开导航',
      onclick: toggleSidebar,
    },
    icon('menu', { size: 16 })
  );
  topbar.appendChild(menuButton);
  topbar.appendChild(h('div.topbar__left', { id: 'topbar-left' }));
  topbar.appendChild(h('div.topbar__right', { id: 'topbar-right' }));

  applyStoredSidebarState();
}

function applyStoredSidebarState() {
  const app = document.getElementById('app');
  try {
    if (localStorage.getItem(SIDEBAR_STORAGE_KEY) === '1') app.classList.add('sidebar-collapsed');
  } catch {
    /* ignore */
  }
}

function toggleSidebar() {
  const app = document.getElementById('app');
  app.classList.toggle('sidebar-collapsed');
  try {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, app.classList.contains('sidebar-collapsed') ? '1' : '0');
  } catch {
    /* ignore */
  }
}

function navLink({ href, iconName, label, active = false, badge = null }) {
  return h(
    `a.nav-link${active ? ' is-active' : ''}`,
    { href, title: label },
    icon(iconName, { size: 16 }),
    h('span.nav-link__label', { text: label }),
    badge ? h('span.nav-link__badge', { text: String(badge) }) : null
  );
}

function buildSidebarNav() {
  const fragment = document.createDocumentFragment();
  const groupAll = h('div.nav-group', null, navLink({ href: '#/projects', iconName: 'projects', label: '全部项目', active: !state.project && !state.run }));
  fragment.appendChild(groupAll);

  if (state.project) {
    const pid = state.project.id;
    const base = `#/projects/${encodeURIComponent(pid)}`;
    const route = parseHash(location.hash);
    const activeFor = (view) => route.name === 'project' && route.view === view;
    const group = h(
      'div.nav-group',
      null,
      h('div.nav-group__title', { text: '项目' }),
      navLink({ href: `${base}/overview`, iconName: 'overview', label: '工作台', active: activeFor('overview') || (route.name === 'run' && state.run) }),
      navLink({ href: `${base}/skill`, iconName: 'skill', label: 'Skill 包', active: activeFor('skill') }),
      navLink({ href: `${base}/environment`, iconName: 'environment', label: '执行环境', active: activeFor('environment') }),
      navLink({ href: `${base}/dataset`, iconName: 'dataset', label: '数据集', active: activeFor('dataset') }),
      navLink({ href: `${base}/versions`, iconName: 'versions', label: '版本记录', active: activeFor('versions') })
    );
    fragment.appendChild(group);

    if (state.recentRuns.length) {
      const runsGroup = h('div.nav-group', null, h('div.nav-group__title', { text: '最近运行' }));
      for (const run of state.recentRuns.slice(0, 5)) {
        const meta = RUN_STATUS_META[run.status] || RUN_STATUS_META.queued;
        runsGroup.appendChild(
          h(
            'a.nav-run',
            { href: `#/runs/${encodeURIComponent(run.id)}`, title: `${modeLabel(run.mode)} · ${run.id}` },
            h('span.severity', { class: `severity--${meta.tone}`, title: meta.label }, icon(meta.icon, { size: 13 })),
            h(
              'span.nav-run__body',
              null,
              h('span.nav-run__title', { text: modeLabel(run.mode) }),
              h('span.nav-run__meta', { text: relativeTime(run.created_at) || shortenId(run.id, 12) })
            )
          )
        );
      }
      fragment.appendChild(runsGroup);
    }
  }
  return fragment;
}

function buildSidebarFooter() {
  const fragment = document.createDocumentFragment();
  fragment.appendChild(
    h(
      'div.service-status',
      { id: 'service-status' },
      h('span.service-status__dot', { 'aria-hidden': 'true' }),
      h('span.footer-label', { text: state.health === 'online' ? '后端已连接' : state.health === 'offline' ? '后端未连接' : '正在检查后端…' })
    )
  );
  fragment.appendChild(h('div.footer-label', { text: isMockMode() ? '演示数据（?mock=1）' : 'Web v1.1 · 单轮优化' }));
  return fragment;
}

function refreshSidebar() {
  const nav = document.getElementById('sidebar-nav');
  const footer = document.getElementById('sidebar-footer');
  if (nav) {
    mount(nav, buildSidebarNav());
  }
  if (footer) {
    mount(footer, buildSidebarFooter());
  }
}

function updateServiceStatus(health) {
  state.health = health;
  const status = document.getElementById('service-status');
  if (!status) return;
  status.className = `service-status service-status--${health === 'online' ? 'online' : health === 'offline' ? 'offline' : ''}`;
  const label = status.querySelector('.footer-label');
  if (label) {
    label.textContent = health === 'online' ? '后端已连接' : health === 'offline' ? '后端未连接' : '正在检查后端…';
  }
}

function renderTopbar() {
  const left = document.getElementById('topbar-left');
  const right = document.getElementById('topbar-right');
  if (!left || !right) return;
  const route = parseHash(location.hash);
  const crumbs = [];

  if (route.name === 'run' && state.run) {
    crumbs.push(h('a', { href: '#/projects', text: '全部项目' }));
    crumbs.push(h('span.breadcrumb__sep', { 'aria-hidden': 'true' }, '/'));
    if (state.project) {
      crumbs.push(h('a', { href: `#/projects/${encodeURIComponent(state.project.id)}/overview`, text: state.project.name }));
      crumbs.push(h('span.breadcrumb__sep', { 'aria-hidden': 'true' }, '/'));
    }
    crumbs.push(h('span.breadcrumb__current', { text: `运行 ${shortenId(state.run.id, 14)}` }));
  } else if (state.project) {
    crumbs.push(h('a', { href: '#/projects', text: '全部项目' }));
    crumbs.push(h('span.breadcrumb__sep', { 'aria-hidden': 'true' }, '/'));
    crumbs.push(h('span.breadcrumb__current', { text: state.project.name }));
  } else {
    crumbs.push(h('span.breadcrumb__current', { text: route.name === 'projects' ? '全部项目' : 'Skill 优化平台' }));
  }

  mount(left, h('nav.breadcrumb', { 'aria-label': '面包屑' }, ...crumbs));

  const rightItems = [];
  if (state.project && state.project.current_skill_version && route.name !== 'run') {
    const version = state.project.current_skill_version;
    rightItems.push(
      h(
        'span.topbar__version',
        { title: '当前 Skill 版本' },
        `${shortenId(version.id || '', 12)} · ${version.status === 'draft' ? '草稿' : '已发布'}`
      )
    );
  }
  if (state.project && route.name !== 'run') {
    // Only surface a run here while it is still in progress; finished runs are
    // already summarised on the page itself.
    const active = state.recentRuns.find((run) => ['queued', 'running', 'cancel_requested'].includes(run.status));
    if (active) {
      rightItems.push(h('a.topbar__run-link', { href: `#/runs/${encodeURIComponent(active.id)}`, title: '查看运行进度' }, statusBadge(active.status, 'run')));
    }
  }
  if (isMockMode()) {
    rightItems.push(h('span.mock-badge', { text: '演示数据' }));
  }
  mount(right, ...rightItems);
}

/* ------------------------------------------------------------------ */
/* routing                                                             */
/* ------------------------------------------------------------------ */

/**
 * @returns {{name:'projects'}|{name:'project',pid:string,view:string}|{name:'run',rid:string,tab:string|null}}
 */
export function parseHash(hash) {
  const raw = (hash || '').replace(/^#/, '');
  const segments = raw.split('/').filter(Boolean).map((segment) => {
    try {
      return decodeURIComponent(segment);
    } catch {
      return segment;
    }
  });
  if (!segments.length || segments[0] === 'projects') {
    if (segments.length >= 3 && segments[0] === 'projects') {
      return { name: 'project', pid: segments[1], view: segments[2] || 'overview' };
    }
    return { name: 'projects' };
  }
  if (segments[0] === 'runs' && segments[1]) {
    return { name: 'run', rid: segments[1], tab: segments[2] || null };
  }
  return { name: 'notFound', path: raw };
}

const PROJECT_VIEWS = {
  overview: { label: '工作台', component: overviewView },
  skill: { label: 'Skill 包', component: skillView },
  environment: { label: '执行环境', component: environmentView },
  dataset: { label: '数据集', component: datasetView },
  versions: { label: '版本记录', component: versionsView },
};

function viewTitle(route) {
  if (route.name === 'projects') return '全部项目';
  if (route.name === 'project') return (PROJECT_VIEWS[route.view] || {}).label || '项目';
  if (route.name === 'run') return '运行详情';
  return '页面不存在';
}

function setDocumentTitle(route) {
  let title = viewTitle(route);
  if (state.run) title = `运行 ${shortenId(state.run.id, 10)} · ${title}`;
  else if (state.project) title = `${state.project.name} · ${title}`;
  document.title = `${title} · ${APP_TITLE}`;
}

function showViewLoading(message = '加载中…') {
  mount(viewRoot(), h('div.page-loading', null, loadingState(message)));
}

async function loadContext(route) {
  state.project = null;
  state.run = null;
  state.recentRuns = [];
  if (route.name === 'project') {
    const [project, runs] = await Promise.all([
      api.getProject(route.pid),
      api.listRuns({ projectId: route.pid, limit: 6 }).catch(() => ({ items: [] })),
    ]);
    state.project = project;
    state.recentRuns = runs.items || [];
  } else if (route.name === 'run') {
    const run = await api.getRun(route.rid);
    state.run = run;
    const [project, runs] = await Promise.all([
      api.getProject(run.project_id).catch(() => null),
      api.listRuns({ projectId: run.project_id, limit: 6 }).catch(() => ({ items: [] })),
    ]);
    state.project = project;
    state.recentRuns = runs.items || [];
  }
}

function resolveRunTab(route, run) {
  if (route.tab) return route.tab;
  const terminal = ['completed', 'failed', 'cancelled', 'interrupted'].includes(run.status);
  if (terminal) return 'result';
  return 'log';
}

function buildCtx(route) {
  const ctx = {
    route,
    project: state.project,
    run: state.run,
    recentRuns: state.recentRuns,
    navigate: (hash) => {
      if (location.hash === hash) return;
      location.hash = hash;
    },
    reload: () => renderRoute(),
    refreshShell: async () => {
      const previous = { project: state.project, run: state.run, recentRuns: state.recentRuns };
      try {
        await loadContext(route);
      } catch {
        // loadContext clears these fields before fetching. Restore them on failure.
        Object.assign(state, previous);
      }
      refreshSidebar();
      renderTopbar();
    },
    updateRunState: (run) => {
      const currentRoute = parseHash(location.hash);
      if (currentRoute.name !== 'run' || currentRoute.rid !== run.id || state.run?.id !== run.id) return;
      state.run = run;
      state.recentRuns = state.recentRuns.map(item => item.id === run.id ? { ...item, ...run } : item);
      refreshSidebar();
      renderTopbar();
      setDocumentTitle(route);
    },
    setDirty,
    isMock: isMockMode(),
  };
  if (route.name === 'run') {
    ctx.activeTab = resolveRunTab(route, state.run);
  }
  return ctx;
}

async function renderRoute() {
  const token = ++state.renderToken;
  const hash = location.hash || '#/projects';
  state.lastHash = hash;
  const route = parseHash(hash);
  setDocumentTitle(route);

  // Drawers and dialogs belong to the page that opened them.
  closeAllOverlays();

  if (state.currentCleanup) {
    try {
      state.currentCleanup();
    } catch {
      /* ignore */
    }
    state.currentCleanup = null;
  }

  refreshSidebar();
  showViewLoading(route.name === 'projects' ? '正在加载项目…' : '正在加载…');

  try {
    await loadContext(route);
    if (token !== state.renderToken) return;
  } catch (error) {
    if (token !== state.renderToken) return;
    state.project = null;
    state.run = null;
    refreshSidebar();
    renderTopbar();
    mount(
      viewRoot(),
      h(
        'div',
        null,
        errorState(error, {
          title: route.name === 'projects' ? '无法加载项目列表' : '无法加载页面数据',
          retry: () => renderRoute(),
        }),
        route.name !== 'projects'
          ? h('div', null, button({ label: '返回项目列表', href: '#/projects', variant: 'secondary' }))
          : null
      )
    );
    setDocumentTitle(route);
    return;
  }

  refreshSidebar();
  renderTopbar();
  setDocumentTitle(route);
  state.lastRenderedHash = hash;

  const ctx = buildCtx(route);
  const container = viewRoot();
  clear(container);

  try {
    let cleanup = null;
    if (route.name === 'projects') {
      cleanup = await projectsView(container, ctx);
    } else if (route.name === 'project') {
      const definition = PROJECT_VIEWS[route.view];
      if (!definition) {
        renderNotFound();
        return;
      }
      cleanup = await definition.component(container, ctx);
    } else if (route.name === 'run') {
      cleanup = await runDetailsView(container, ctx);
    } else {
      renderNotFound();
      return;
    }
    if (token !== state.renderToken) {
      if (typeof cleanup === 'function') cleanup();
      return;
    }
    state.currentCleanup = typeof cleanup === 'function' ? cleanup : null;
  } catch (error) {
    console.error('页面渲染失败', error);
    if (token !== state.renderToken) return;
    mount(
      viewRoot(),
      h(
        'div',
        null,
        errorState(error, { title: '页面渲染失败', retry: () => renderRoute() }),
        h('div', { style: { marginTop: '12px' } }, button({ label: '返回项目列表', href: '#/projects', variant: 'secondary' }))
      )
    );
  }
}

function renderNotFound() {
  mount(
    viewRoot(),
    emptyState({
      iconName: 'info',
      title: '页面不存在',
      description: '链接可能已失效，或页面路径有误。',
      actions: [{ label: '返回项目列表', href: '#/projects', variant: 'primary', iconName: 'arrowLeft' }],
    })
  );
}

async function handleHashChange() {
  if (state.skipGuardOnce) {
    state.skipGuardOnce = false;
    return;
  }
  if (hasUnsavedChanges()) {
    const ok = await confirmLeave();
    if (!ok) {
      state.skipGuardOnce = true;
      location.hash = state.lastRenderedHash || '#/projects';
      return;
    }
  }
  renderRoute();
}

/* ------------------------------------------------------------------ */
/* bootstrap                                                           */
/* ------------------------------------------------------------------ */

async function bootstrap() {
  const params = new URLSearchParams(location.search);
  if (params.get('mock') === '1') {
    try {
      const { installMock } = await import('./mock.js');
      installMock();
    } catch (error) {
      console.error('启用 mock 失败', error);
    }
  }

  buildShell();
  window.addEventListener('hashchange', handleHashChange);

  api
    .health()
    .then(() => updateServiceStatus('online'))
    .catch(() => updateServiceStatus('offline'));

  if (!location.hash) {
    location.hash = '#/projects';
    // hashchange fires and renders; still call renderRoute for browsers that
    // normalize the hash synchronously.
  }
  await renderRoute();
  refreshSidebar();
  window.__SKILLOPT_APP__ = { renderRoute, state, parseHash };
}

bootstrap().catch((error) => {
  console.error('应用启动失败', error);
  const root = viewRoot();
  if (root) {
    mount(root, errorState(error, { title: '应用启动失败', retry: () => location.reload() }));
  }
});
