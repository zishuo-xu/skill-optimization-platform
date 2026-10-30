/**
 * 设置：Skill / Prompt 与模型 / 测试集 / 版本历史。
 *
 * 编辑即保存到工作副本；下一次在首页启动测试或优化时自动冻结成新版本。
 * 历史运行仍绑定当时的版本，不受影响。
 */

import { api } from '../api_v3.js';
import { h, mount, icon } from '../dom.js';
import { tabsBar, button, pageHeader } from '../components.js';
import skillView from './skill.js';
import environmentView from './environment.js';
import datasetView from './dataset.js';
import versionsView from './versions.js';

export const SETTINGS_TABS = [
  { id: 'skill', label: 'Skill', kind: 'skill', component: skillView },
  { id: 'model', label: 'Prompt 与模型', kind: 'environment', component: environmentView },
  { id: 'dataset', label: '测试集', kind: 'dataset', component: datasetView },
  { id: 'history', label: '版本历史', component: versionsView },
];

export default async function settingsView(container, ctx) {
  const { project } = ctx;
  const active = SETTINGS_TABS.find((tab) => tab.id === ctx.route.sub) || SETTINGS_TABS[0];
  const base = `#/projects/${encodeURIComponent(project.id)}`;
  let pending = [];

  const noticeHost = h('div');
  const tabsHost = h('div.settings-tabs');
  const body = h('div.settings-body');

  function renderNotice() {
    if (!pending.length) {
      mount(noticeHost);
      return;
    }
    mount(noticeHost, h('div.pending-notice', null,
      icon('clock', { size: 15 }),
      h('span', null,
        h('strong', { text: `${pending.map((item) => item.label).join('、')} 有修改尚未生效。` }),
        ' 下次在首页运行测试或优化时会自动生效；之前的运行结果不受影响。'),
      h('span.pending-notice__spacer'),
      button({ label: '回到首页', variant: 'ghost', size: 'sm', iconName: 'arrowRight', href: base })));
  }

  function renderTabs() {
    const pendingKinds = new Set(pending.map((item) => item.kind));
    mount(tabsHost, tabsBar({
      tabs: SETTINGS_TABS.map((tab) => ({
        id: tab.id,
        label: tab.label,
        title: tab.kind && pendingKinds.has(tab.kind) ? '有修改尚未生效' : undefined,
        badge: tab.kind && pendingKinds.has(tab.kind) ? h('span.tab__dot', { 'aria-label': '有修改尚未生效' }) : null,
      })),
      active: active.id,
      onSelect: (id) => ctx.navigate(`${base}/settings/${id}`),
    }));
  }

  async function refreshPending() {
    try {
      const summary = await api.getWorkspace(project.id);
      pending = summary.pending || [];
    } catch {
      pending = [];
    }
    renderNotice();
    renderTabs();
  }

  mount(container,
    pageHeader({ title: '设置', description: '修改后点保存即可，不需要发布；下次运行时自动生效。' }),
    noticeHost,
    tabsHost,
    body);
  renderTabs();
  refreshPending();

  const cleanup = await active.component(body, {
    ...ctx,
    workspace: true,
    onWorkspaceChange: refreshPending,
  });
  return () => {
    if (typeof cleanup === 'function') cleanup();
  };
}
