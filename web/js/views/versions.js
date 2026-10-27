/** 版本记录：Skill / 环境 / 数据集版本列表、比较、设为当前、下载。 */

import { api, download } from '../api_v3.js';
import { h, mount, icon } from '../dom.js';
import {
  button,
  chip,
  statusBadge,
  pageHeader,
  tabsBar,
  emptyState,
  errorState,
  loadingState,
  openDrawer,
  confirmDialog,
  toast,
  kvList,
  jsonViewer,
  unifiedDiff,
} from '../components.js';
import { formatDateTime, jsonPathDiffs, relativeTime, safeJsonStringify, shortenId } from '../format.js';

const TYPES = {
  skill: { label: 'Skill 包', iconName: 'skill', route: 'skill' },
  environment: { label: '执行环境', iconName: 'environment', route: 'environment' },
  dataset: { label: '数据集', iconName: 'dataset', route: 'dataset' },
};

function normalizeVersions(payload, project) {
  if (Array.isArray(payload)) return payload.map((item) => normalizeVersionItem(item, project));
  if (payload && Array.isArray(payload.items)) return payload.items.map((item) => normalizeVersionItem(item, project));
  const items = [];
  if (payload && typeof payload === 'object') {
    for (const version of payload.skill_versions || payload.skillVersions || []) {
      items.push(normalizeVersionItem({ ...version, type: 'skill' }, project));
    }
    for (const version of payload.environments || []) {
      items.push(normalizeVersionItem({ ...version, type: 'environment' }, project));
    }
    for (const version of payload.datasets || []) {
      items.push(normalizeVersionItem({ ...version, type: 'dataset' }, project));
    }
  }
  return items;
}

function normalizeVersionItem(item, project) {
  const type = item.type || item.kind || 'skill';
  return {
    ...item,
    type,
    id: item.id,
    versionLabel:
      item.version_label ||
      item.versionLabel ||
      (type === 'skill'
        ? shortenId(item.id || '', 14)
        : item.version_seq
        ? `v${item.version_seq}`
        : shortenId(item.id || '', 10)),
    status: item.status || 'published',
    parentId: item.parent_id || item.parentId || null,
    sourceRunId: item.source_run_id || item.sourceRunId || null,
    createdAt: item.created_at || item.createdAt || null,
    message: item.message || '',
    isCurrent: Boolean(
      item.is_current ??
        item.isCurrent ??
        (type === 'skill'
          ? item.id === project?.current_skill_version_id
          : type === 'environment'
          ? item.id === project?.current_environment_id
          : item.id === project?.current_dataset_id)
    ),
    hash: item.content_hash || item.hash || '',
    caseCount: item.case_count ?? item.caseCount ?? null,
    metrics: item.metrics || null,
  };
}

export default async function versionsView(container, ctx) {
  const { project } = ctx;
  const state = {
    versions: [],
    typeFilter: 'all',
    loading: true,
    error: null,
  };

  const tableHost = h('div');
  const tabsHost = h('div');

  function filtered() {
    return state.versions.filter((version) => state.typeFilter === 'all' || version.type === state.typeFilter);
  }

  function typeCounts() {
    return {
      all: state.versions.length,
      skill: state.versions.filter((version) => version.type === 'skill').length,
      environment: state.versions.filter((version) => version.type === 'environment').length,
      dataset: state.versions.filter((version) => version.type === 'dataset').length,
    };
  }

  function renderTabs() {
    const counts = typeCounts();
    mount(
      tabsHost,
      tabsBar({
        tabs: [
          { id: 'all', label: '全部版本', count: counts.all },
          { id: 'skill', label: 'Skill 包', count: counts.skill },
          { id: 'environment', label: '执行环境', count: counts.environment },
          { id: 'dataset', label: '数据集', count: counts.dataset },
        ],
        active: state.typeFilter,
        onSelect: (id) => {
          state.typeFilter = id;
          renderTabs();
          renderTable();
        },
      })
    );
  }

  async function setCurrent(version) {
    const meta = TYPES[version.type] || TYPES.skill;
    const messages = {
      skill: '将把项目当前 Skill 包指针切换到该版本。已有运行的快照不受影响，基线缓存会按新指纹失效。',
      environment: '将把项目当前执行环境指针切换到该版本。旧运行保留原环境快照；新运行使用该版本。',
      dataset: '将把项目当前数据集指针切换到该版本。历史成绩来自不同数据集时不会冒充当前评测。',
    };
    const ok = await confirmDialog({
      title: `设为当前${meta.label}版本`,
      message: `${messages[version.type] || ''} 操作保留完整历史记录，可随时切回。`,
      confirmText: '设为当前',
    });
    if (!ok) return;
    try {
      if (version.type === 'skill') await api.publishSkillVersion(version.id);
      else if (version.type === 'environment') await api.publishEnvironment(version.id);
      else await api.publishDataset(version.id);
      toast('已设为当前版本', { type: 'success' });
      await load();
      await ctx.refreshShell();
    } catch (error) {
      toast(`操作失败：${error.message}`, { type: 'error' });
    }
  }

  function versionRow(version) {
    const meta = TYPES[version.type] || TYPES.skill;
    const route = `#/projects/${encodeURIComponent(project.id)}/${meta.route}`;
    return h(
      'tr',
      null,
      h(
        'td.nowrap',
        null,
        h('span.row', { style: { gap: '6px' } }, icon(meta.iconName, { size: 14 }), h('span', { text: meta.label }))
      ),
      h(
        'td.nowrap',
        null,
        h('a.mono', { href: route, title: version.id }, version.versionLabel),
        version.id === project.current_skill_version_id || version.isCurrent ? chip('当前', 'accent') : null
      ),
      h('td.nowrap', null, statusBadge(version.status, 'version')),
      h(
        'td',
        { style: { maxWidth: '320px' } },
        h('div', { text: version.message || '—' }),
        version.hash ? h('div.mono.xsmall.muted', { text: version.hash }) : null
      ),
      h(
        'td',
        null,
        version.parentId
          ? h('span.mono.xsmall', { text: String(version.parentId).replace('skillv_', 'v') })
          : h('span.muted', { text: '—' })
      ),
      h(
        'td',
        null,
        version.sourceRunId
          ? h('a.mono.xsmall', { href: `#/runs/${encodeURIComponent(version.sourceRunId)}` }, shortenId(version.sourceRunId, 12))
          : h('span.muted', { text: '—' })
      ),
      h(
        'td.nowrap.xsmall.muted',
        null,
        h('div', { text: formatDateTime(version.createdAt) }),
        h('div', { text: relativeTime(version.createdAt) })
      ),
      h(
        'td.cell-actions',
        null,
        h(
          'div.version-actions',
          null,
          button({ label: '比较', variant: 'ghost', size: 'sm', iconName: 'versions', onClick: () => openCompare(version) }),
          version.isCurrent
            ? h('span.chip.chip--neutral', { text: '已是当前' })
            : version.status === 'candidate'
            ? h('span.chip.chip--warning', { title: '未达标的候选版本不能设为当前', text: '候选未应用' })
            : button({ label: '设为当前', variant: 'ghost', size: 'sm', iconName: 'check', onClick: () => setCurrent(version) }),
          version.type === 'skill'
            ? button({
                label: '下载',
                variant: 'ghost',
                size: 'sm',
                iconName: 'download',
                onClick: () => download(api.exportSkillVersionUrl(version.id), `${version.id}.zip`),
              })
            : null
        )
      )
    );
  }

  function renderTable() {
    const versions = filtered();
    if (state.loading) {
      mount(tableHost, loadingState('正在加载版本记录…'));
      return;
    }
    if (state.error) {
      mount(tableHost, errorState(state.error, { title: '无法加载版本记录', retry: () => load() }));
      return;
    }
    if (!versions.length) {
      mount(
        tableHost,
        emptyState({
          iconName: 'versions',
          title: state.typeFilter === 'all' ? '还没有版本记录' : '该类型还没有版本',
          description: '导入 Skill 包、发布执行环境或数据集后，这里会记录每个不可变版本及来源运行。',
          actions: [
            { label: '去 Skill 包', variant: 'secondary', href: `#/projects/${encodeURIComponent(project.id)}/skill` },
            { label: '去执行环境', variant: 'secondary', href: `#/projects/${encodeURIComponent(project.id)}/environment` },
            { label: '去数据集', variant: 'secondary', href: `#/projects/${encodeURIComponent(project.id)}/dataset` },
          ],
        })
      );
      return;
    }
    mount(
      tableHost,
      h(
        'div.table-wrap',
        null,
        h(
          'table.data-table',
          null,
          h(
            'thead',
            null,
            h(
              'tr',
              null,
              h('th', { text: '类型' }),
              h('th', { text: '版本' }),
              h('th', { text: '状态' }),
              h('th', { text: '说明' }),
              h('th', { text: '父版本' }),
              h('th', { text: '来源运行' }),
              h('th', { text: '创建时间' }),
              h('th', { text: '操作', class: 'cell-actions' })
            )
          ),
          h('tbody', null, ...versions.map(versionRow))
        )
      )
    );
  }

  function openCompare(target) {
    const sameType = state.versions.filter((version) => version.type === target.type);
    let fromId = target.parentId && sameType.some((version) => version.id === target.parentId) ? target.parentId : null;
    if (!fromId) {
      const fallback = sameType.find((version) => version.id !== target.id && (version.isCurrent || version.status === 'published'));
      fromId = fallback ? fallback.id : null;
    }
    let toId = target.id;

    const bodyHost = h('div', { style: { minHeight: '260px' } });
    const fromSelect = h('select.select', { style: { minWidth: '240px' }, 'aria-label': '比较基准版本' });
    const toSelect = h('select.select', { style: { minWidth: '240px' }, 'aria-label': '比较目标版本' });

    function fillSelects() {
      for (const [select, selectedId] of [
        [fromSelect, fromId],
        [toSelect, toId],
      ]) {
        select.replaceChildren();
        select.appendChild(h('option', { value: '', selected: !selectedId }, '（不选择）'));
        for (const version of sameType) {
          select.appendChild(
            h(
              'option',
              { value: version.id, selected: version.id === selectedId },
              `${version.versionLabel} · ${version.message || version.id}`
            )
          );
        }
      }
    }

    async function renderDiff() {
      mount(bodyHost, loadingState('正在加载差异…'));
      fromId = fromSelect.value || null;
      toId = toSelect.value || null;
      try {
        const result = await computeDiff(target.type, fromId, toId);
        mount(bodyHost, result);
      } catch (error) {
        mount(bodyHost, errorState(error, { title: '无法加载版本差异' }));
      }
    }

    fromSelect.addEventListener('change', renderDiff);
    toSelect.addEventListener('change', renderDiff);
    fillSelects();

    const drawer = openDrawer({
      title: `比较${(TYPES[target.type] || TYPES.skill).label}版本`,
      subtitle: '选择两个同类型版本，比较文件或配置差异。',
      size: 'xl',
      body: h(
        'div.stack',
        null,
        h(
          'div.compare-meta',
          null,
          h('div.version-picker', null, h('span', { text: '基准' }), fromSelect),
          h('div.version-picker', null, h('span', { text: '目标' }), toSelect)
        ),
        bodyHost
      ),
      footer: h('div.btn-row', null, button({ label: '关闭', variant: 'secondary', onClick: () => drawer.close() })),
    });
    renderDiff();
  }

  async function computeDiff(type, fromId, toId) {
    if (!toId) {
      return emptyState({ iconName: 'versions', title: '请选择目标版本', description: '选择基准与目标版本后展示差异。' });
    }
    if (type === 'skill') {
      const payload = await api.getSkillVersionDiff(toId, fromId || undefined);
      const changes = payload?.changes || payload?.items || [];
      if (!changes.length) return emptyState({ iconName: 'check', title: '两个版本内容一致', description: '没有可展示的文件差异。' });
      return h(
        'div.stack',
        null,
        h('p.small.muted', { text: `共 ${changes.length} 个文件发生变化。` }),
        ...changes.map((change) =>
          h(
            'div',
            null,
            h(
              'div.diff-pane__head',
              null,
              icon('file', { size: 14 }),
              h('span.diff-pane__path', { text: change.path }),
              chip(change.status === 'added' ? '新增' : change.status === 'removed' ? '删除' : '修改', change.status === 'added' ? 'success' : change.status === 'removed' ? 'danger' : 'accent'),
              h('span.mono.xsmall.muted', { text: `+${change.additions ?? 0} −${change.deletions ?? 0}` })
            ),
            unifiedDiff(change.diff || '', { maxHeight: '380px' })
          )
        )
      );
    }
    if (type === 'environment') {
      const [fromEnv, toEnv] = await Promise.all([
        fromId ? api.getEnvironment(fromId) : Promise.resolve(null),
        api.getEnvironment(toId),
      ]);
      const fields = ['prompt', 'model_config', 'optimizer_config', 'loader_config', 'run_config', 'budget_config', 'scorer_config', 'acceptance_config'];
      return h(
        'div.stack',
        null,
        kvList([
          { label: '基准版本', value: fromEnv ? `v${fromEnv.version_seq} · ${fromEnv.hash || fromEnv.id}` : '（未选择）', mono: true },
          { label: '目标版本', value: `v${toEnv.version_seq} · ${toEnv.hash || toEnv.id}`, mono: true },
        ]),
        ...fields.map((field) => {
          const before = fromEnv ? fromEnv[field] : undefined;
          const after = toEnv[field];
          const changed = safeJsonStringify(before) !== safeJsonStringify(after);
          return h(
            'div',
            { style: { borderTop: '1px solid var(--line-soft)', paddingTop: '10px' } },
            h(
              'div.row',
              { style: { gap: '8px', marginBottom: '6px' } },
              h('strong.small', { text: field }),
              changed ? chip('有变化', 'warning') : chip('无变化', 'neutral'),
              field === 'prompt' ? chip('只读文本', 'neutral') : null
            ),
            field === 'prompt'
              ? h(
                  'div.form-grid',
                  null,
                  h('pre.code-block__pre', { style: { maxHeight: '220px', whiteSpace: 'pre-wrap' }, text: before || '（无）' }),
                  h('pre.code-block__pre', { style: { maxHeight: '220px', whiteSpace: 'pre-wrap' }, text: after || '（无）' })
                )
              : changed
              ? h(
                  'div',
                  null,
                  h('p.xsmall.muted', { style: { marginBottom: '4px' }, text: '差异字段：' }),
                  h(
                    'div.param-diff-list',
                    null,
                    ...jsonPathDiffs(before || {}, after || {}).slice(0, 60).map((diff) =>
                      h(
                        'div.param-diff',
                        null,
                        h('div.param-diff__path', { text: diff.path }),
                        h(
                          'div.param-diff__cell',
                          null,
                          h('span.param-diff__label', { text: '基准' }),
                          h('span.param-diff__value.is-expected', { text: stringify(diff.expected) })
                        ),
                        h(
                          'div.param-diff__cell',
                          null,
                          h('span.param-diff__label', { text: '目标' }),
                          h('span.param-diff__value.is-actual', { text: stringify(diff.actual) })
                        )
                      )
                    )
                  )
                )
              : h('p.xsmall.muted', { text: '配置一致。' })
          );
        })
      );
    }
    const [fromDataset, toDataset] = await Promise.all([
      fromId ? api.getDataset(fromId) : Promise.resolve(null),
      api.getDataset(toId),
    ]);
    return h(
      'div.stack',
      null,
      kvList([
        { label: '基准版本', value: fromDataset ? `v${fromDataset.version_seq} · ${fromDataset.case_count ?? '?'} 条案例` : '（未选择）' },
        { label: '目标版本', value: `v${toDataset.version_seq} · ${toDataset.case_count ?? '?'} 条案例` },
        { label: '基准哈希', value: fromDataset?.hash || '—', mono: true },
        { label: '目标哈希', value: toDataset.hash || '—', mono: true },
        { label: '来源文件', value: `${fromDataset?.source_name || '—'} → ${toDataset.source_name || '—'}` },
      ]),
      h(
        'p.small.muted',
        null,
        '已发布数据集不可原地修改。比较仅展示版本级摘要；逐案例差异需要重新运行基线后查看运行详情。'
      )
    );
  }

  function stringify(value) {
    if (value === undefined) return '（缺失）';
    if (typeof value === 'string') return value;
    return safeJsonStringify(value);
  }

  async function load() {
    state.loading = true;
    state.error = null;
    renderTable();
    try {
      const payload = await api.listVersions(project.id);
      state.versions = normalizeVersions(payload, project).sort((a, b) => String(b.createdAt || '').localeCompare(String(a.createdAt || '')));
    } catch (error) {
      state.error = error;
    } finally {
      state.loading = false;
      renderTabs();
      renderTable();
    }
  }

  mount(
    container,
    pageHeader({
      title: '版本记录',
      description: '所有 Skill、环境与数据集版本均不可变；设为当前只切换项目指针，历史运行保留原快照。',
      actions: [
        button({
          label: '比较版本',
          variant: 'primary',
          iconName: 'versions',
          onClick: () => {
            const target = filtered()[0] || state.versions[0];
            if (target) openCompare(target);
            else toast('还没有可比较的版本', { type: 'warning' });
          },
        }),
      ],
    }),
    tabsHost,
    tableHost
  );

  await load();
  return () => {};
}
