/** 项目列表：新建、进入、归档，空态给导入引导。 */

import { api } from '../api_v3.js';
import { h, mount, icon, debounce } from '../dom.js';
import {
  button,
  iconButton,
  chip,
  statusBadge,
  pageHeader,
  emptyState,
  errorState,
  loadingState,
  openModal,
  confirmDialog,
  formField,
  toast,
} from '../components.js';
import { formatDate, modeLabel, relativeTime, shortenId } from '../format.js';

export default async function projectsView(container, ctx) {
  const state = {
    projects: [],
    includeArchived: false,
    search: '',
    loading: true,
    error: null,
  };

  const listHost = h('div.projects-list');
  const countHost = h('span.muted.small');

  const searchInput = h('input.input', {
    type: 'search',
    placeholder: '搜索项目名称',
    'aria-label': '搜索项目',
    oninput: debounce((event) => {
      state.search = event.target.value.trim().toLowerCase();
      renderList();
    }, 180),
  });

  const archivedToggle = h(
    'label.checkbox-line',
    null,
    h('input', {
      type: 'checkbox',
      onchange: (event) => {
        state.includeArchived = event.target.checked;
        load();
      },
    }),
    h('span', { text: '显示已归档' })
  );

  function visibleProjects() {
    return state.projects.filter((project) => {
      const archived = project.archived === 1 || project.archived === '1' || project.archived === true;
      if (!state.includeArchived && archived) return false;
      if (state.search) {
        const haystack = `${project.name} ${project.description || ''}`.toLowerCase();
        if (!haystack.includes(state.search)) return false;
      }
      return true;
    });
  }

  function openCreateDialog() {
    const nameInput = h('input.input', { type: 'text', placeholder: '例如：客户服务 Skill 优化', maxlength: '80' });
    const descInput = h('textarea.textarea', { rows: 3, placeholder: '可选：简单描述这个项目要优化的目标' });
    const errorHost = h('div');
    const form = h(
      'form',
      {
        onsubmit: (event) => {
          event.preventDefault();
          submit();
        },
      },
      h(
        'div.stack',
        null,
        formField({ label: '项目名称', htmlFor: 'project-name', required: true, control: nameInput }),
        formField({ label: '项目描述', htmlFor: 'project-desc', control: descInput, hint: '用于团队内区分项目，可稍后修改。' }),
        errorHost
      )
    );
    let submitting = false;
    const submitButton = button({ label: '创建项目', variant: 'primary', type: 'submit', onClick: () => submit() });
    const handle = openModal({
      title: '新建项目',
      subtitle: '创建后先导入 Skill 包，再配置执行环境与数据集。',
      size: 'md',
      body: form,
      footer: h(
        'div.btn-row',
        null,
        button({ label: '取消', variant: 'ghost', onClick: () => handle.close() }),
        submitButton
      ),
    });
    nameInput.id = 'project-name';
    descInput.id = 'project-desc';
    setTimeout(() => nameInput.focus(), 30);

    async function submit() {
      if (submitting) return;
      const name = nameInput.value.trim();
      mount(errorHost);
      if (!name) {
        mount(errorHost, h('p.form-field__error', { role: 'alert' }, icon('alert', { size: 13 }), h('span', { text: '请输入项目名称' })));
        nameInput.focus();
        return;
      }
      submitting = true;
      submitButton.disabled = true;
      submitButton.textContent = '创建中…';
      try {
        const project = await api.createProject({ name, description: descInput.value.trim() });
        toast('项目已创建，下一步导入 Skill 包', { type: 'success' });
        handle.close();
        ctx.navigate(`#/projects/${encodeURIComponent(project.id)}/skill`);
      } catch (error) {
        submitting = false;
        submitButton.disabled = false;
        submitButton.textContent = '创建项目';
        mount(
          errorHost,
          error.field_path === 'name'
            ? h('p.form-field__error', { role: 'alert' }, icon('alert', { size: 13 }), h('span', { text: error.message }))
            : h('div.inline-error', { role: 'alert' }, icon('alert', { size: 14 }), h('span', { text: error.message }))
        );
      }
    }
  }

  async function archiveProject(project) {
    const confirmed = await confirmDialog({
      title: '归档项目',
      message: `归档「${project.name}」后，它会从默认列表中隐藏，但版本与运行记录会保留，可随时恢复。`,
      confirmText: '归档项目',
      danger: true,
    });
    if (!confirmed) return;
    try {
      await api.updateProject(project.id, { archived: true });
      toast('项目已归档', { type: 'success' });
      await load();
    } catch (error) {
      toast(`归档失败：${error.message}`, { type: 'error' });
    }
  }

  async function restoreProject(project) {
    try {
      await api.updateProject(project.id, { archived: false });
      toast('项目已恢复', { type: 'success' });
      await load();
    } catch (error) {
      toast(`恢复失败：${error.message}`, { type: 'error' });
    }
  }

  function projectCard(project) {
    const latest = project.latest_run;
    const version = project.current_skill_version;
    const archived = project.archived === 1 || project.archived === '1' || project.archived === true;
    return h(
      `article.project-card${archived ? ' project-card--archived' : ''}`,
      null,
      h(
        'div.project-card__head',
        null,
        h(
          'div',
          { style: { minWidth: '0' } },
          h('a.project-card__name', { href: `#/projects/${encodeURIComponent(project.id)}/overview` }, project.name),
          h('p.project-card__desc', { text: project.description || '暂无项目描述' })
        ),
        archived ? chip('已归档', 'neutral', { iconName: 'archive' }) : null
      ),
      h(
        'div.project-card__meta',
        null,
        version ? chip(`Skill ${shortenId(version.id, 10)}`, 'accent', { iconName: 'skill' }) : chip('未导入 Skill 包', 'warning', { iconName: 'alert' }),
        latest ? statusBadge(latest.status, 'run') : chip('尚无运行', 'neutral', { iconName: 'clock' }),
        h('span', { text: `${project.run_count || 0} 次运行` })
      ),
      h('div.project-card__spacer'),
      h(
        'div.project-card__footer',
        null,
        h(
          'span.project-card__time',
          { text: latest ? `最近活动 ${relativeTime(latest.created_at)}` : `创建于 ${formatDate(project.created_at)}` }
        ),
        h(
          'div.row',
          { style: { gap: '6px' } },
          archived
            ? button({ label: '恢复', variant: 'ghost', size: 'sm', iconName: 'refresh', onClick: () => restoreProject(project) })
            : iconButton('archive', { label: '归档项目', title: '归档项目', onClick: () => archiveProject(project) }),
          button({
            label: archived ? '查看' : '进入',
            variant: archived ? 'secondary' : 'primary',
            size: 'sm',
            iconName: 'arrowRight',
            href: `#/projects/${encodeURIComponent(project.id)}/overview`,
          })
        )
      )
    );
  }

  function renderList() {
    const projects = visibleProjects();
    countHost.textContent = state.projects.length ? `共 ${state.projects.length} 个项目` : '';
    if (state.loading) {
      mount(listHost, loadingState('正在加载项目…'));
      return;
    }
    if (state.error) {
      mount(listHost, errorState(state.error, { title: '无法加载项目列表', retry: () => load() }));
      return;
    }
    if (!state.projects.length) {
      mount(
        listHost,
        emptyState({
          iconName: 'projects',
          title: '还没有项目',
          description: '先创建一个项目，然后按「导入 Skill 包 → 配置执行环境 → 导入测试集 → 运行评测」的顺序开始。',
          actions: [
            { label: '新建项目', variant: 'primary', iconName: 'plus', onClick: openCreateDialog },
            { label: '查看导入说明', variant: 'ghost', href: '#/projects' },
          ],
        })
      );
      return;
    }
    if (!projects.length) {
      mount(
        listHost,
        emptyState({
          iconName: 'search',
          title: '没有匹配的项目',
          description: '尝试修改搜索关键词，或显示已归档项目。',
          actions: [
            {
              label: '清空搜索',
              variant: 'secondary',
              onClick: () => {
                searchInput.value = '';
                state.search = '';
                renderList();
              },
            },
          ],
        })
      );
      return;
    }
    const grid = h('div.project-grid');
    for (const project of projects) grid.appendChild(projectCard(project));
    grid.appendChild(
      h(
        'button.project-card.project-card--new',
        { type: 'button', onclick: openCreateDialog },
        icon('plus', { size: 22 }),
        h('span', { text: '新建项目' }),
        h('span.small.muted', { text: '从一个空的 Skill 包开始' })
      )
    );
    mount(listHost, grid);
  }

  async function load() {
    state.loading = true;
    state.error = null;
    renderList();
    try {
      const result = await api.listProjects({ includeArchived: state.includeArchived });
      state.projects = Array.isArray(result) ? result : result.items || [];
    } catch (error) {
      state.error = error;
    } finally {
      state.loading = false;
      renderList();
    }
  }

  mount(
    container,
    pageHeader({
      title: '项目',
      description: '每个项目对应一个 Skill 包与一套冻结的执行环境、数据集和评测记录。',
      actions: [button({ label: '新建项目', variant: 'primary', iconName: 'plus', onClick: openCreateDialog })],
    }),
    h(
      'div.projects-toolbar',
      null,
      h('div.search-field', null, icon('search', { size: 15 }), searchInput),
      h('div.row', { style: { gap: '14px' } }, archivedToggle, countHost)
    ),
    listHost
  );

  await load();
  return () => {};
}
