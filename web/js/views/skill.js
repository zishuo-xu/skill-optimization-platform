/** Skill 包：版本选择、240px 文件树、文本编辑、保存草稿 / 发布版本、导入导出。 */

import { api, download } from '../api_v3.js';
import { h, mount, icon, focusElement } from '../dom.js';
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
  openDrawer,
  confirmDialog,
  formField,
  toast,
  inlineError,
  copyButton,
  jsonViewer,
} from '../components.js';
import { formatDateTime, relativeTime, shortenId, truncate } from '../format.js';

function asItems(payload) {
  if (Array.isArray(payload)) return payload;
  if (payload && Array.isArray(payload.items)) return payload.items;
  return [];
}

export default async function skillView(container, ctx) {
  const { project } = ctx;
  const state = {
    versions: [],
    version: null,
    versionsLoading: true,
    versionError: null,
    files: [],
    activePath: null,
    editorValue: '',
    savedValue: '',
    fileLoading: false,
    fileError: null,
    busy: false,
  };

  const treeHost = h('div.file-tree');
  const editorHost = h('div.editor-body');
  const versionBarHost = h('div');
  const fileInput = h('input', { type: 'file', accept: '.zip,application/zip', style: { display: 'none' } });

  function isDraft() {
    return state.version?.status === 'draft';
  }

  function isDirty() {
    return state.activePath && state.editorValue !== state.savedValue;
  }

  function syncDirty() {
    ctx.setDirty(isDirty() ? 'Skill 文件有未保存的修改，离开当前页面会丢失。' : null);
  }

  function currentVersionId() {
    return project.current_skill_version_id || null;
  }

  function pickInitialVersion() {
    const current = state.versions.find((version) => version.id === currentVersionId());
    if (current) return current.id;
    return (state.versions[0] || {}).id || null;
  }

  async function loadVersions(preferredId = null) {
    state.versionsLoading = true;
    state.versionError = null;
    renderVersionBar();
    renderTree();
    renderEditor();
    try {
      const payload = await api.listSkillVersions(project.id);
      state.versions = asItems(payload).sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
      const targetId = preferredId || state.version?.id || pickInitialVersion();
      const target = state.versions.find((version) => version.id === targetId) || state.versions[0] || null;
      if (target) await selectVersion(target.id, { skipDirtyCheck: true });
      else {
        state.version = null;
        state.files = [];
        state.activePath = null;
      }
    } catch (error) {
      state.versionError = error;
    } finally {
      state.versionsLoading = false;
      renderVersionBar();
      renderTree();
      renderEditor();
    }
  }

  async function selectVersion(versionId, { skipDirtyCheck = false, path = null } = {}) {
    if (isDirty() && !skipDirtyCheck) {
      const ok = await confirmDialog({
        title: '切换版本会丢失未保存修改',
        message: '当前文件有未保存的修改。是否放弃修改并切换版本？',
        confirmText: '放弃并切换',
        danger: true,
      });
      if (!ok) return;
    }
    ctx.setDirty(null);
    state.fileLoading = true;
    state.versionError = null;
    state.activePath = null;
    state.fileError = null;
    renderTree();
    renderEditor();
    try {
      const detail = await api.getSkillVersion(versionId);
      state.version = detail;
      state.files = (detail.files || [])
        .map((file) => (typeof file === 'string' ? { path: file } : file))
        .sort((a, b) => a.path.localeCompare(b.path));
      const nextPath = path || state.activePath || (state.files.find((file) => file.path === 'SKILL.md') || state.files[0] || {}).path || null;
      renderVersionBar();
      if (nextPath) await openFile(nextPath);
      else {
        state.editorValue = '';
        state.savedValue = '';
      }
    } catch (error) {
      state.versionError = error;
    } finally {
      state.fileLoading = false;
      renderVersionBar();
      renderTree();
      renderEditor();
    }
  }

  async function openFile(path, { skipDirtyCheck = false } = {}) {
    if (state.activePath === path && state.fileLoading === false) return;
    if (isDirty() && !skipDirtyCheck) {
      const ok = await confirmDialog({
        title: '切换文件会丢失未保存修改',
        message: '当前文件有未保存的修改。是否放弃修改并打开其它文件？',
        confirmText: '放弃并切换',
        danger: true,
      });
      if (!ok) return;
    }
    ctx.setDirty(null);
    state.activePath = path;
    state.fileLoading = true;
    state.fileError = null;
    renderTree();
    renderEditor();
    try {
      const file = await api.getSkillFile(state.version.id, path);
      const content = typeof file === 'string' ? file : file.content ?? '';
      state.editorValue = content;
      state.savedValue = content;
    } catch (error) {
      state.fileError = error;
      state.editorValue = '';
      state.savedValue = '';
    } finally {
      state.fileLoading = false;
      renderTree();
      renderEditor();
    }
  }

  function manifestSubskills() {
    const manifest = state.version?.manifest || {};
    const subskills = Array.isArray(manifest.subskills) ? manifest.subskills : [];
    return subskills.map((sub) => ({
      skill_id: sub.skill_id,
      name: sub.name || sub.skill_id,
      path: sub.path,
    }));
  }

  function renderVersionBar() {
    if (state.versionsLoading && !state.version) {
      mount(versionBarHost, loadingState('正在加载版本…', { compact: true }));
      return;
    }
    if (state.versionError) {
      mount(versionBarHost, errorState(state.versionError, { title: '无法加载 Skill 版本', retry: () => loadVersions() }));
      return;
    }
    if (!state.version) {
      mount(versionBarHost);
      return;
    }
    const version = state.version;
    const select = h(
      'select.select',
      {
        style: { width: 'auto', minWidth: '260px' },
        'aria-label': '选择 Skill 版本',
        onchange: (event) => selectVersion(event.target.value),
      },
      ...state.versions.map((item) =>
        h(
          'option',
          {
            value: item.id,
            selected: item.id === version.id,
          },
          `${shortenId(item.id, 14)} · ${item.status === 'draft' ? '草稿' : item.status === 'candidate' ? '候选' : '已发布'} · ${truncate(item.message || item.id, 26)}`
        )
      )
    );
    mount(
      versionBarHost,
      h(
        'div.version-bar',
        null,
        h('span.muted.small', { text: '版本' }),
        select,
        statusBadge(version.status, 'version'),
        version.id === currentVersionId() ? chip('当前版本', 'accent') : null,
        h('span.mono.xsmall.muted', { text: version.content_hash || '' }),
        h('span.xsmall.muted', { text: `创建于 ${formatDateTime(version.created_at)}` }),
        h('span.version-bar__spacer'),
        button({
          label: '版本历史',
          variant: 'ghost',
          size: 'sm',
          iconName: 'history',
          onClick: openHistoryDrawer,
        }),
        button({
          label: '导出 ZIP',
          variant: 'ghost',
          size: 'sm',
          iconName: 'download',
          onClick: () => download(api.exportSkillVersionUrl(version.id), `${version.id}.zip`),
        }),
        version.status === 'published'
          ? button({ label: '创建草稿', variant: 'secondary', size: 'sm', iconName: 'edit', onClick: createDraft, disabled: state.busy })
          : null,
        version.status === 'draft'
          ? button({ label: '发布版本', variant: 'primary', size: 'sm', iconName: 'check', onClick: publishVersion, disabled: state.busy })
          : null,
        version.status === 'draft'
          ? button({ label: '保存草稿', variant: 'secondary', size: 'sm', iconName: 'save', onClick: saveDraft, disabled: state.busy || !isDirty() })
          : null
      )
    );
  }

  function fileNode(path, label, { active = false, child = false } = {}) {
    return h(
      `button.file-tree__node${active ? ' is-active' : ''}${child ? ' file-tree__node--child' : ''}`,
      {
        type: 'button',
        title: path,
        onclick: () => openFile(path),
      },
      icon(path === 'SKILL.md' ? 'skill' : 'file', { size: 14 }),
      h('span.file-tree__label', { text: label })
    );
  }

  function renderTree() {
    if (state.versionsLoading && !state.version) {
      mount(treeHost, loadingState('加载中…', { compact: true }));
      return;
    }
    if (!state.version) {
      mount(treeHost, h('p.muted.small', { style: { padding: '12px' }, text: '尚未导入 Skill 包' }));
      return;
    }
    if (state.fileLoading && !state.files.length) {
      mount(treeHost, loadingState('加载文件列表…', { compact: true }));
      return;
    }
    const fragment = document.createDocumentFragment();
    fragment.appendChild(h('div.file-tree__head', null, h('span', { text: '文件' }), h('span', { text: `${state.files.length} 个` })));

    const subskills = manifestSubskills();
    const filePaths = new Set(state.files.map((file) => file.path));
    const mainPaths = state.files.filter((file) => file.path === 'SKILL.md');
    const subPaths = subskills.filter((sub) => filePaths.has(sub.path));
    const mappedPaths = new Set(subPaths.map((sub) => sub.path));
    const otherPaths = state.files.filter((file) => !mainPaths.includes(file) && file.path !== 'SKILL.md' && !mappedPaths.has(file.path));

    if (mainPaths.length) {
      fragment.appendChild(h('div.file-tree__group-title', { text: '主 Skill' }));
      for (const file of mainPaths) fragment.appendChild(fileNode(file.path, 'SKILL.md', { active: file.path === state.activePath }));
    }
    if (subPaths.length) {
      fragment.appendChild(h('div.file-tree__group-title', { text: `子技能（${subPaths.length}）` }));
      for (const sub of subPaths) {
        fragment.appendChild(
          fileNode(sub.path, sub.name, { active: sub.path === state.activePath, child: false })
        );
      }
    }
    if (otherPaths.length) {
      fragment.appendChild(h('div.file-tree__group-title', { text: '其他文件' }));
      for (const file of otherPaths) {
        fragment.appendChild(fileNode(file.path, file.path.split('/').pop(), { active: file.path === state.activePath, child: file.path.includes('/') }));
      }
    }
    if (!state.files.length) {
      fragment.appendChild(h('p.muted.small', { style: { padding: '12px' }, text: '版本中没有文件。' }));
    }
    mount(treeHost, fragment);
  }

  function renderEditor() {
    if (state.versionsLoading && !state.version) {
      mount(editorHost, h('div.editor-empty', null, loadingState('正在加载版本…')));
      return;
    }
    if (state.versionError) {
      mount(editorHost, h('div.editor-empty', null, errorState(state.versionError, { title: '无法加载 Skill 版本', retry: () => loadVersions() })));
      return;
    }
    if (!state.version) {
      mount(
        editorHost,
        emptyState({
          iconName: 'skill',
          title: '还没有导入 Skill 包',
          description: '上传包含 SKILL.md 与 manifest.json 的 ZIP。导入后可以查看文件树、编辑草稿并发布不可变版本。',
          actions: [{ label: '导入 Skill 包', variant: 'primary', iconName: 'upload', onClick: () => fileInput.click() }],
        })
      );
      return;
    }
    if (!state.activePath) {
      mount(editorHost, h('div.editor-empty', null, loadingState(state.fileLoading ? '正在加载文件…' : '从左侧选择一个文件')));
      return;
    }
    if (state.fileLoading) {
      mount(editorHost, h('div.editor-empty', null, loadingState('正在加载文件…')));
      return;
    }
    if (state.fileError) {
      mount(
        editorHost,
        h(
          'div.editor-empty',
          null,
          errorState(state.fileError, { title: '无法读取文件', retry: () => openFile(state.activePath, { skipDirtyCheck: true }) })
        )
      );
      return;
    }
    const editable = isDraft();
    const dirty = isDirty();
    const textarea = h('textarea.editor-textarea', {
      spellcheck: 'false',
      readonly: !editable,
      value: state.editorValue,
      'aria-label': `${state.activePath} 文件内容`,
      oninput: (event) => {
        state.editorValue = event.target.value;
        syncDirty();
        updateFooter();
      },
      onkeydown: (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
          event.preventDefault();
          if (editable && isDirty()) saveDraft();
          return;
        }
        if (event.key === 'Tab' && editable) {
          event.preventDefault();
          const target = event.target;
          const start = target.selectionStart;
          const end = target.selectionEnd;
          const value = `${target.value.slice(0, start)}  ${target.value.slice(end)}`;
          target.value = value;
          target.selectionStart = start + 2;
          target.selectionEnd = start + 2;
          state.editorValue = value;
          syncDirty();
          updateFooter();
        }
      },
    });
    const footerStatus = h('span.muted.xsmall');
    const footerMeta = h('span.muted.xsmall');
    const footerActions = h('div.row', { style: { gap: '6px' } });
    const toolbar = h(
      'div.editor-toolbar',
      null,
      icon(state.activePath === 'SKILL.md' ? 'skill' : 'file', { size: 15 }),
      h('span.editor-toolbar__path', { text: state.activePath }),
      editable ? chip('草稿可编辑', 'warning', { iconName: 'edit' }) : chip('已发布 · 只读', 'neutral', { iconName: 'lock' }),
      h('span.spacer'),
      h('span.editor-toolbar__spacer'),
      editable
        ? button({ label: '保存草稿', variant: 'secondary', size: 'sm', iconName: 'save', disabled: !dirty || state.busy, onClick: saveDraft })
        : null,
      editable
        ? button({ label: '发布版本', variant: 'primary', size: 'sm', iconName: 'check', disabled: state.busy, onClick: publishVersion })
        : button({ label: '创建草稿', variant: 'secondary', size: 'sm', iconName: 'edit', disabled: state.busy, onClick: createDraft })
    );

    function updateFooter() {
      const lines = state.editorValue.split('\n').length;
      footerStatus.textContent = isDirty() ? '● 有未保存修改' : '已保存';
      footerStatus.className = `xsmall ${isDirty() ? 'text-warning' : 'muted'}`;
      footerMeta.textContent = `${state.editorValue.length} 字符 · ${lines} 行 · UTF-8${isDraft() ? ' · Ctrl+S 保存草稿' : ''}`;
    }
    updateFooter();

    mount(
      editorHost,
      h(
        'div.editor-pane',
        null,
        toolbar,
        h('div.editor-body', null, textarea),
        h('div.editor-footer', null, footerMeta, h('span.spacer'), footerStatus)
      )
    );
  }

  async function saveDraft() {
    if (!state.version || state.version.status !== 'draft' || !state.activePath) return false;
    if (!isDirty()) return true;
    state.busy = true;
    renderVersionBar();
    try {
      await api.saveSkillFile(state.version.id, state.activePath, state.editorValue);
      state.savedValue = state.editorValue;
      syncDirty();
      toast('草稿已保存', { type: 'success' });
      return true;
    } catch (error) {
      toast(`保存失败：${error.message}`, { type: 'error' });
      return false;
    } finally {
      state.busy = false;
      renderVersionBar();
      renderEditor();
    }
  }

  async function publishVersion() {
    if (!state.version || state.version.status !== 'draft') return;
    if (isDirty()) {
      const saved = await saveDraft();
      if (!saved) return;
    }
    const ok = await confirmDialog({
      title: '发布 Skill 版本',
      message: '发布后会生成不可变版本并设为当前版本。后续修改需要再次创建草稿。',
      confirmText: '发布并设为当前',
    });
    if (!ok) return;
    state.busy = true;
    renderVersionBar();
    try {
      const published = await api.publishSkillVersion(state.version.id);
      toast(`版本已发布${published?.id ? `：${published.id}` : ''}`, { type: 'success' });
      await loadVersions(published?.id || state.version.id);
      await ctx.refreshShell();
    } catch (error) {
      toast(`发布失败：${error.message}`, { type: 'error' });
    } finally {
      state.busy = false;
      renderVersionBar();
      renderEditor();
    }
  }

  async function createDraft() {
    if (!state.version || state.version.status !== 'draft') {
      const source = state.version;
      if (!source) return;
      const ok = await confirmDialog({
        title: '创建草稿版本',
        message: '将基于当前已发布版本创建草稿，草稿不会改变当前版本指针。创建后即可编辑文件并发布。',
        confirmText: '创建草稿',
      });
      if (!ok) return;
      state.busy = true;
      renderVersionBar();
      renderEditor();
      try {
        const files = [];
        const sourceFiles = state.files.length ? state.files : (source.files || []).map((file) => ({ path: typeof file === 'string' ? file : file.path }));
        for (const file of sourceFiles) {
          const detail = await api.getSkillFile(source.id, file.path);
          files.push({ path: file.path, content: typeof detail === 'string' ? detail : detail.content ?? '' });
        }
        const draft = await api.importSkillVersion(project.id, {
          files,
          manifest: source.manifest,
          message: `基于 ${source.id} 创建草稿`,
          status: 'draft',
          parentId: source.id,
          setCurrent: false,
        });
        toast('草稿已创建', { type: 'success' });
        await loadVersions(draft.id);
      } catch (error) {
        toast(`创建草稿失败：${error.message}`, { type: 'error' });
      } finally {
        state.busy = false;
        renderVersionBar();
        renderEditor();
      }
    }
  }

  async function importZip(file) {
    if (!file) return;
    state.busy = true;
    toast('正在导入 Skill 包…', { type: 'info' });
    try {
      const version = await api.uploadSkillZip(project.id, file, { message: `导入 ${file.name}` });
      toast('Skill 包已导入并设为当前版本', { type: 'success' });
      await loadVersions(version?.id || null);
      await ctx.refreshShell();
    } catch (error) {
      openImportError(error);
    } finally {
      state.busy = false;
      fileInput.value = '';
    }
  }

  function openImportError(error) {
    const details = error.details || {};
    const errors = details.errors || details.issues || [];
    const modal = openModal({
      title: '导入失败',
      size: 'md',
      body: h(
        'div.stack',
        null,
        calloutError(error.message),
        errors.length
          ? h(
              'div',
              null,
              h('p.small.muted', { text: `共 ${errors.length} 个问题：` }),
              h(
                'ul.error-list',
                { style: { paddingLeft: '1.2em', fontSize: '12.5px', color: 'var(--text-2)' } },
                ...errors.slice(0, 40).map((issue) =>
                  h('li', {
                    text: `${issue.path || issue.code || ''}${issue.path ? '：' : ''}${issue.message || issue.code || JSON.stringify(issue)}`,
                  })
                )
              )
            )
          : null,
        h('p.small.muted', { text: '修复后重新导入。支持 ZIP（根目录包含 SKILL.md 与 manifest.json）。' })
      ),
      footer: h('div.btn-row', null, button({ label: '知道了', variant: 'secondary', onClick: () => modal.close() })),
    });
  }

  function calloutError(message) {
    return h('div.inline-error', { role: 'alert' }, icon('alert', { size: 15 }), h('span', { text: message || '导入失败' }));
  }

  function openHistoryDrawer() {
    const drawer = openDrawer({
      title: '版本历史',
      subtitle: '主 Skill 与所有子技能按整个包一起版本化；草稿不会改变当前版本。',
      size: 'md',
      body: h(
        'div.stack',
        null,
        ...state.versions.map((version) =>
          h(
            'button.version-history-item',
            {
              type: 'button',
              class: `version-history-item${version.id === state.version?.id ? ' is-active' : ''}`,
              style: {
                textAlign: 'left',
                padding: '10px 12px',
                borderRadius: '8px',
                border: '1px solid var(--line-soft)',
                background: version.id === state.version?.id ? 'var(--accent-soft)' : 'var(--bg-soft)',
                color: 'var(--text-2)',
                display: 'flex',
                gap: '10px',
                alignItems: 'center',
              },
              onclick: () => {
                drawer.close();
                selectVersion(version.id);
              },
            },
            statusBadge(version.status, 'version'),
            h(
              'span',
              { style: { minWidth: 0, flex: 1 } },
              h('span.mono.xsmall', { text: shortenId(version.id, 14) }),
              h('span.xsmall', { style: { display: 'block', color: 'var(--text-4)' } }, version.message || '无版本说明'),
              h(
                'span.xsmall',
                { style: { display: 'block', color: 'var(--text-4)' } },
                `${formatDateTime(version.created_at)} · ${relativeTime(version.created_at)}`
              )
            ),
            version.id === currentVersionId() ? chip('当前', 'accent') : null
          )
        )
      ),
      footer: h(
        'div.btn-row.btn-row--between',
        null,
        button({ label: '在版本记录中比较', variant: 'ghost', onClick: () => ctx.navigate(`#/projects/${encodeURIComponent(project.id)}/versions`) }),
        button({ label: '关闭', variant: 'secondary', onClick: () => drawer.close() })
      ),
    });
  }

  fileInput.addEventListener('change', () => importZip(fileInput.files && fileInput.files[0]));

  mount(
    container,
    pageHeader({
      title: 'Skill 包',
      description: '编辑主 Skill 与子技能参考文件；保存草稿不会改变当前版本，发布后生成不可变版本。',
      actions: [
        button({ label: '导入 ZIP', variant: 'primary', iconName: 'upload', onClick: () => fileInput.click() }),
        button({ label: '刷新', variant: 'ghost', iconName: 'refresh', onClick: () => loadVersions(state.version?.id) }),
      ],
    }),
    versionBarHost,
    h(
      'div.skill-layout',
      null,
      h('div.file-tree-pane', null, treeHost),
      h('div.editor-pane', null, editorHost)
    ),
    h('div', { style: { marginTop: '12px' } }, fileInput)
  );

  await loadVersions();
  return () => {
    ctx.setDirty(null);
  };
}
