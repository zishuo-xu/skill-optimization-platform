/**
 * Shared UI components: overlays, toasts, empty/error states, badges,
 * collapsible sections, tabs, JSON viewer, unified/split diff views.
 *
 * All user data is inserted with text nodes (via ``dom.h``) — never innerHTML.
 */

import {
  h,
  icon,
  copyText,
  focusElement,
  isEditableTarget,
} from './dom.js';
import {
  parseUnifiedDiff,
  buildSplitDiff,
  highlightedJson,
  jsonPretty,
  redactValue,
  redactStructuredText,
  safeJsonStringify,
} from './format.js';
import {
  RUN_STATUS_META,
  DECISION_META,
  LEVEL_META,
  VERSION_STATUS_META,
  CASE_STATUS_META,
  SPLIT_META,
  CHANGE_TYPE_META,
} from './eventTypes.js';

/* ------------------------------------------------------------------ */
/* buttons / chips / badges                                            */
/* ------------------------------------------------------------------ */

export function button({
  label,
  iconName = null,
  variant = 'secondary',
  size = 'md',
  onClick = null,
  disabled = false,
  title = '',
  type = 'button',
  href = null,
  className = '',
} = {}) {
  const classes = `btn btn--${variant}${size === 'sm' ? ' btn--sm' : ''}${className ? ` ${className}` : ''}`;
  const children = [];
  if (iconName) children.push(icon(iconName, { size: size === 'sm' ? 14 : 16 }));
  if (label) children.push(h('span', { text: label }));
  if (href) {
    return h('a', {
      class: classes,
      href,
      title: title || undefined,
      'aria-disabled': disabled ? 'true' : undefined,
      onclick: disabled ? (event) => event.preventDefault() : onClick,
    }, ...children);
  }
  return h('button', {
    class: classes,
    type,
    title: title || undefined,
    disabled,
    onclick: onClick,
  }, ...children);
}

export function iconButton(iconName, { label, onClick, disabled = false, title = '', variant = 'ghost', size = 'sm' } = {}) {
  return h('button', {
    class: `btn btn--${variant} btn--icon${size === 'sm' ? ' btn--sm' : ''}`,
    type: 'button',
    title: title || label,
    'aria-label': label,
    disabled,
    onclick: onClick,
  }, icon(iconName, { size: 15 }));
}

export function chip(label, tone = 'neutral', { iconName = null } = {}) {
  return h(
    `span.chip.chip--${tone}`,
    null,
    iconName ? icon(iconName, { size: 12 }) : null,
    h('span', { text: label })
  );
}

export function statusBadge(status, kind = 'run') {
  let meta = null;
  if (kind === 'decision') meta = DECISION_META[status];
  else if (kind === 'level') meta = LEVEL_META[status];
  else if (kind === 'version') meta = VERSION_STATUS_META[status];
  else if (kind === 'case') meta = CASE_STATUS_META[status];
  else if (kind === 'split') meta = SPLIT_META[status];
  else if (kind === 'change') meta = CHANGE_TYPE_META[status];
  else meta = RUN_STATUS_META[status];
  if (!meta) meta = { label: status || '未知', icon: 'dot', tone: 'neutral' };
  return h(
    `span.status-badge.status-badge--${meta.tone}${meta.pulse ? ' is-pulse' : ''}`,
    { title: meta.label },
    icon(meta.icon || 'dot', { size: 13 }),
    h('span', { text: meta.label })
  );
}

export function severityIcon(level) {
  const meta = LEVEL_META[level] || LEVEL_META.info;
  return h(`span.severity.severity--${meta.tone}`, { title: meta.label, 'aria-label': meta.label }, icon(meta.icon, { size: 14 }));
}

export function toneChipForLevel(level) {
  return (LEVEL_META[level] || LEVEL_META.info).tone;
}

/* ------------------------------------------------------------------ */
/* loading / empty / error                                             */
/* ------------------------------------------------------------------ */

export function spinner(size = 'md', label = '加载中') {
  return h('span.spinner', { class: `spinner spinner--${size}`, role: 'status', 'aria-label': label });
}

export function loadingState(message = '加载中…', { compact = false } = {}) {
  return h(
    `div.loading-state${compact ? ' loading-state--compact' : ''}`,
    null,
    spinner(compact ? 'sm' : 'md'),
    h('p', { text: message })
  );
}

export function emptyState({ iconName = 'info', title = '暂无内容', description = '', actions = [] } = {}) {
  return h(
    'div.empty-state',
    null,
    h('div.empty-state__icon', { 'aria-hidden': 'true' }, icon(iconName, { size: 26 })),
    h('h3.empty-state__title', { text: title }),
    description ? h('p.empty-state__description', { text: description }) : null,
    actions.length
      ? h(
          'div.empty-state__actions',
          null,
          ...actions.map((action) =>
            action.href
              ? button({ label: action.label, href: action.href, variant: action.variant || 'secondary', iconName: action.iconName })
              : button({
                  label: action.label,
                  variant: action.variant || 'secondary',
                  iconName: action.iconName,
                  onClick: action.onClick,
                })
          )
        )
      : null
  );
}

export function inlineError(message, { tone = 'danger' } = {}) {
  if (!message) return null;
  return h(
    `div.inline-error.inline-error--${tone}`,
    { role: 'alert' },
    icon(tone === 'danger' ? 'alert' : 'info', { size: 14 }),
    h('span', { text: message })
  );
}

export function errorState(error, { title = '加载失败', retry = null, extra = null } = {}) {
  const code = error && error.code ? String(error.code) : 'error';
  const message = (error && error.message) || '发生未知错误';
  const network = error && error.network;
  const notFound = error && error.status === 404;
  const actions = [];
  if (retry) actions.push(button({ label: '重试', iconName: 'refresh', variant: 'secondary', onClick: retry }));
  if (notFound) actions.push(button({ label: '返回项目列表', href: '#/projects', variant: 'secondary' }));
  return h(
    'div.error-state',
    { role: 'alert' },
    h('div.error-state__icon', null, icon(network ? 'info' : 'alert', { size: 24 })),
    h('h3.error-state__title', { text: network ? '无法连接后端服务' : title }),
    h('p.error-state__message', { text: message }),
    network ? h('p.error-state__hint', { text: '请确认 app.server 已启动，且页面通过 /api/v3 访问同一后端。' }) : null,
    h('div.error-state__meta', null, chip(`错误码 ${code}`, 'neutral')),
    extra,
    actions.length ? h('div.error-state__actions', null, ...actions) : null
  );
}

/* ------------------------------------------------------------------ */
/* overlays: modal / drawer / confirm                                  */
/* ------------------------------------------------------------------ */

const overlayStack = [];
let overlayCounter = 0;

function syncBodyLock() {
  document.body.classList.toggle('has-overlay', overlayStack.length > 0);
}

function handleOverlayKeydown(event) {
  if (!overlayStack.length) return;
  const top = overlayStack[overlayStack.length - 1];
  if (event.key === 'Escape') {
    if (top.dismissible) {
      event.preventDefault();
      top.close('escape');
    }
    return;
  }
  if (event.key === 'Tab') {
    const focusables = top.panel.querySelectorAll(
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
}

document.addEventListener('keydown', handleOverlayKeydown);

function createOverlay({ variant = 'modal', title, subtitle, size = 'md', body, footer = null, dismissible = true, onClose, onBeforeClose = null, initialFocus = null }) {
  const root = document.getElementById('overlay-root');
  const layer = h(`div.overlay.overlay--${variant}`, { role: 'presentation' });
  const panel = h(
    'div',
    {
      class: `${variant} ${variant}--${size}`,
      role: 'dialog',
      'aria-modal': 'true',
      'aria-label': title || '对话框',
      tabindex: '-1',
    },
    h(
      'header',
      { class: `${variant}__header` },
      h(
        'div',
        { class: `${variant}__heading` },
        h('h2', { text: title || '' }),
        subtitle ? h('p', { text: subtitle }) : null
      ),
      dismissible
        ? iconButton('x', { label: '关闭', onClick: () => close('button'), variant: 'ghost' })
        : null
    ),
    h('div', { class: `${variant}__body` }, body),
    footer ? h('footer', { class: `${variant}__footer` }, footer) : null
  );
  layer.appendChild(panel);
  root.appendChild(layer);

  const previousActive = document.activeElement;
  const id = ++overlayCounter;
  let closed = false;
  let closing = false;

  async function close(reason = 'programmatic', { force = false } = {}) {
    if (closed || (closing && !force)) return false;
    if (!force && onBeforeClose) {
      closing = true;
      try {
        const allowed = await onBeforeClose(reason);
        if (allowed === false) {
          closing = false;
          return false;
        }
      } catch {
        closing = false;
        return false;
      }
      closing = false;
    }
    if (closed) return false;
    closed = true;
    const index = overlayStack.findIndex((entry) => entry.id === id);
    if (index >= 0) overlayStack.splice(index, 1);
    layer.classList.add('is-closing');
    syncBodyLock();
    setTimeout(() => {
      layer.remove();
      if (!overlayStack.length && previousActive && typeof previousActive.focus === 'function') {
        try {
          previousActive.focus({ preventScroll: true });
        } catch {
          /* ignore */
        }
      }
    }, 120);
    try {
      onClose && onClose(reason);
    } catch {
      /* listener errors are non-fatal */
    }
    return true;
  }

  if (dismissible) {
    layer.addEventListener('mousedown', (event) => {
      if (event.target === layer) close('backdrop');
    });
  }

  overlayStack.push({ id, panel, close, dismissible });
  syncBodyLock();
  requestAnimationFrame(() => {
    const target = initialFocus || panel.querySelector('input, textarea, select, button');
    focusElement(target || panel);
  });

  return {
    close,
    forceClose: () => {
      const index = overlayStack.findIndex((entry) => entry.id === id);
      if (index >= 0) {
        for (const child of overlayStack.slice(index + 1).reverse()) child.close('force', { force: true });
      }
      return close('force', { force: true });
    },
    layer,
    panel,
    body: panel.querySelector(`.${variant}__body`),
    setBody(nextBody) {
      const container = panel.querySelector(`.${variant}__body`);
      if (container) {
        container.replaceChildren();
        if (nextBody) container.appendChild(nextBody);
      }
    },
    setFooter(nextFooter) {
      let footerEl = panel.querySelector(`.${variant}__footer`);
      if (!footerEl && nextFooter) {
        footerEl = h('footer', { class: `${variant}__footer` });
        panel.appendChild(footerEl);
      }
      if (footerEl) {
        footerEl.replaceChildren();
        if (nextFooter) footerEl.appendChild(nextFooter);
      }
    },
  };
}

export function openModal(options) {
  return createOverlay({ variant: 'modal', ...options });
}

export function openDrawer(options) {
  return createOverlay({ variant: 'drawer', size: options.size || 'lg', ...options });
}

export function confirmDialog({
  title = '确认操作',
  message = '',
  details = null,
  confirmText = '确定',
  cancelText = '取消',
  danger = false,
  onConfirm = null, // async () => { close() }
} = {}) {
  return new Promise((resolve) => {
    let done = false;
    let handle = null;

    function finish(result) {
      if (done) return;
      done = true;
      resolve(result);
      if (handle) handle.close('confirm');
    }

    handle = createOverlay({
      variant: 'modal',
      size: 'sm',
      title,
      dismissible: true,
      body: h(
        'div.confirm-body',
        null,
        message ? h('p', { text: message }) : null,
        details || null
      ),
      footer: h(
        'div.btn-row',
        null,
        button({ label: cancelText, variant: 'ghost', onClick: () => finish(false) }),
        button({
          label: confirmText,
          variant: danger ? 'danger' : 'primary',
          onClick: async () => {
            if (onConfirm) {
              const result = await onConfirm();
              if (result === false) return;
            }
            finish(true);
          },
        })
      ),
      onClose: (reason) => {
        if (!done) {
          done = true;
          resolve(false);
        }
      },
    });
  });
}

/* ------------------------------------------------------------------ */
/* toasts                                                              */
/* ------------------------------------------------------------------ */

export function toast(message, { type = 'info', duration = 4200, action = null } = {}) {
  const root = document.getElementById('toast-root');
  if (!root) return () => {};
  const tone = { info: 'info', success: 'success', warning: 'warning', error: 'danger', danger: 'danger' }[type] || 'info';
  const iconName = { info: 'info', success: 'check', warning: 'alert', danger: 'alert' }[tone] || 'info';
  const el = h(
    `div.toast.toast--${tone}`,
    { role: 'status' },
    icon(iconName, { size: 16 }),
    h('span.toast__message', { text: message }),
    action
      ? h('button.toast__action', { type: 'button', text: action.label, onclick: () => { action.onClick && action.onClick(); dismiss(); } })
      : null,
    iconButton('x', { label: '关闭提示', onClick: () => dismiss(), variant: 'ghost' })
  );
  root.appendChild(el);
  let timer = setTimeout(dismiss, duration);
  function dismiss() {
    if (!el.isConnected) return;
    clearTimeout(timer);
    el.classList.add('is-leaving');
    setTimeout(() => el.remove(), 160);
  }
  el.addEventListener('mouseenter', () => clearTimeout(timer));
  el.addEventListener('mouseleave', () => {
    timer = setTimeout(dismiss, 1200);
  });
  // Keep at most 4 toasts on screen.
  while (root.children.length > 4) root.firstElementChild.remove();
  return dismiss;
}

/* ------------------------------------------------------------------ */
/* layout helpers                                                      */
/* ------------------------------------------------------------------ */

export function pageHeader({ title, description = '', actions = [], meta = null, backHref = null, backLabel = null }) {
  return h(
    'div.page-head',
    null,
    h(
      'div.page-head__main',
      null,
      backHref
        ? h(
            'a.page-head__back',
            { href: backHref },
            icon('arrowLeft', { size: 14 }),
            h('span', { text: backLabel || '返回' })
          )
        : null,
      h('div.page-head__title-row', null, h('h1', { text: title }), meta),
      description ? h('p.page-head__description', { text: description }) : null
    ),
    actions.length ? h('div.page-head__actions', null, ...actions) : null
  );
}

export function sectionCard({
  title,
  description = '',
  actions = [],
  body,
  collapsible = false,
  open = true,
  padded = true,
  className = '',
  titleHint = null,
}) {
  const content = h(`div.section-card__body${padded ? ' is-padded' : ''}`, null, body);
  const head = h(
    'div.section-card__head',
    null,
    h(
      'div.section-card__heading',
      null,
      h('h2', { text: title || '' }),
      titleHint,
      description ? h('p', { text: description }) : null
    ),
    actions.length ? h('div.section-card__actions', null, ...actions) : null
  );
  if (!collapsible) {
    return h(`section.section-card${className ? ` ${className}` : ''}`, null, head, content);
  }
  const details = h(
    `details.section-card.section-card--collapsible${className ? ` ${className}` : ''}`,
    { open },
    h('summary.section-card__head', null, head.childNodes[0], head.childNodes[1] || null),
    content
  );
  return details;
}

export function collapsible({ summary, body, open = false, className = '' }) {
  return h(
    `details.collapsible${className ? ` ${className}` : ''}`,
    { open },
    h('summary.collapsible__summary', null, icon('chevronRight', { size: 14, className: 'collapsible__chevron' }), summary),
    h('div.collapsible__body', null, body)
  );
}

export function callout({ tone = 'info', iconName = null, title = '', body = '', actions = [] } = {}) {
  const map = { info: 'info', success: 'check', warning: 'alert', danger: 'alert', accent: 'spark', neutral: 'info' };
  return h(
    `div.callout.callout--${tone}`,
    { role: tone === 'danger' ? 'alert' : undefined },
    h('div.callout__icon', null, icon(iconName || map[tone] || 'info', { size: 18 })),
    h(
      'div.callout__content',
      null,
      title ? h('h3.callout__title', { text: title }) : null,
      body ? (typeof body === 'string' ? h('div.callout__body', { text: body }) : h('div.callout__body', null, body)) : null,
      actions.length ? h('div.callout__actions', null, ...actions) : null
    )
  );
}

export function kvList(items, { compact = false } = {}) {
  return h(
    `dl.kv-list${compact ? ' kv-list--compact' : ''}`,
    null,
    ...items
      .filter(Boolean)
      .map((item) =>
        h(
          'div.kv-list__row',
          null,
          h('dt', { text: item.label }),
          h(
            `dd${item.mono ? '.mono' : ''}`,
            null,
            item.value instanceof Node ? item.value : h('span', { text: item.value ?? '—' })
          )
        )
      )
  );
}

export function metricCard({ label, value, sub = '', delta = null, status = null, tone = 'neutral', hint = null }) {
  return h(
    `div.metric-card.metric-card--${tone}${status === 'temporary' ? ' is-temporary' : ''}`,
    null,
    h('div.metric-card__label', null, h('span', { text: label }), status === 'temporary' ? chip('临时值', 'warning') : null),
    h('div.metric-card__value', { text: value }),
    delta
      ? h('div.metric-card__delta', null, delta)
      : null,
    sub ? h('div.metric-card__sub', { text: sub }) : null,
    hint
  );
}

export function progressBar({ value = 0, max = 1, label = '', indeterminate = false, tone = 'accent' }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (Number(value) / Number(max)) * 100)) : 0;
  return h(
    'div.progress',
    { role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': String(max), 'aria-valuenow': String(value), 'aria-label': label || '进度' },
    h(
      'div.progress__track',
      null,
      h(`div.progress__fill.progress__fill--${tone}${indeterminate ? ' is-indeterminate' : ''}`, {
        style: { width: indeterminate ? '35%' : `${pct}%` },
      })
    ),
    label ? h('div.progress__label', { text: label }) : null
  );
}

export function tabsBar({ tabs, active, onSelect, ariaLabel = '标签页' }) {
  const bar = h('div.tabs', { role: 'tablist', 'aria-label': ariaLabel });
  for (const tab of tabs) {
    const selected = tab.id === active;
    bar.appendChild(
      h(
        `button.tab${selected ? ' is-active' : ''}`,
        {
          type: 'button',
          role: 'tab',
          'aria-selected': selected ? 'true' : 'false',
          tabindex: selected ? '0' : '-1',
          disabled: Boolean(tab.disabled),
          title: tab.title || undefined,
          onclick: () => onSelect && onSelect(tab.id),
        },
        h('span', { text: tab.label }),
        tab.count !== undefined && tab.count !== null
          ? h('span.tab__count', { text: String(tab.count) })
          : null,
        tab.badge || null
      )
    );
  }
  bar.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    const buttons = [...bar.querySelectorAll('button.tab:not([disabled])')];
    const index = buttons.indexOf(document.activeElement);
    if (index < 0) return;
    event.preventDefault();
    const next = buttons[(index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length];
    next.focus();
    next.click();
  });
  return bar;
}

export function pagination({ total = 0, limit = 50, offset = 0, onChange }) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  const disabledPrev = offset <= 0;
  const disabledNext = to >= total;
  return h(
    'div.pagination',
    null,
    h('span.pagination__info', { text: `第 ${from}–${to} 条，共 ${total} 条` }),
    h(
      'div.pagination__actions',
      null,
      button({
        label: '上一页',
        size: 'sm',
        variant: 'ghost',
        disabled: disabledPrev,
        onClick: () => onChange(Math.max(0, offset - limit)),
      }),
      button({
        label: '下一页',
        size: 'sm',
        variant: 'ghost',
        disabled: disabledNext,
        onClick: () => onChange(offset + limit),
      })
    )
  );
}

/* ------------------------------------------------------------------ */
/* forms                                                               */
/* ------------------------------------------------------------------ */

export function formField({ label, htmlFor = null, required = false, hint = '', error = '', control, className = '' }) {
  return h(
    `div.form-field${error ? ' has-error' : ''}${className ? ` ${className}` : ''}`,
    null,
    h(
      'label.form-field__label',
      { for: htmlFor || undefined },
      h('span', { text: label }),
      required ? h('span.form-field__required', { text: '必填' }) : null
    ),
    control,
    hint && !error ? h('p.form-field__hint', { text: hint }) : null,
    error ? h('p.form-field__error', { role: 'alert' }, icon('alert', { size: 13 }), h('span', { text: error })) : null
  );
}

export function toggleSwitch({ checked = false, onChange, label, disabled = false }) {
  return h(
    'label.switch',
    null,
    h('input', {
      type: 'checkbox',
      checked,
      disabled,
      onchange: (event) => onChange && onChange(event.target.checked),
    }),
    h('span.switch__track', { 'aria-hidden': 'true' }),
    label ? h('span.switch__label', { text: label }) : null
  );
}

/* ------------------------------------------------------------------ */
/* copy + code + JSON                                                  */
/* ------------------------------------------------------------------ */

export function copyButton(getValue, { label = '复制', small = true, onCopied = null } = {}) {
  return button({
    label,
    iconName: 'copy',
    variant: 'ghost',
    size: small ? 'sm' : 'md',
    title: '复制脱敏内容',
    onClick: async () => {
      const raw = typeof getValue === 'function' ? getValue() : getValue;
      const value = raw === null || raw === undefined ? '' : typeof raw === 'string' ? raw : safeJsonStringify(redactValue(raw));
      const ok = await copyText(value);
      if (ok) {
        toast('已复制', { type: 'success', duration: 1800 });
        onCopied && onCopied();
      } else {
        toast('复制失败，请手动选择文本', { type: 'warning' });
      }
    },
  });
}

/**
 * Read-only JSON viewer. Long payloads stay collapsed until the user opens
 * the details element; credentials are redacted when ``redact`` is true.
 */
export function jsonViewer(value, { label = 'JSON', open = false, maxHeight = '340px', redact = false, copyLabel = '复制' } = {}) {
  if (value === null || value === undefined || value === '') {
    return h('p.muted.small', { text: '无数据' });
  }
  const safeValue = redact ? redactValue(value) : value;
  const pretty = typeof safeValue === 'string' ? safeValue : jsonPretty(safeValue);
  const pre = h('pre.code-block__pre.json', { style: { maxHeight } });
  pre.appendChild(highlightedJson(typeof safeValue === 'string' ? tryParseOrText(safeValue) : safeValue));
  return h(
    'details.json-viewer',
    { open },
    h(
      'summary.json-viewer__summary',
      null,
      icon('chevronRight', { size: 13, className: 'json-viewer__chevron' }),
      h('span', { text: label }),
      h('span.json-viewer__size', { text: pretty.length > 1024 ? `${(pretty.length / 1024).toFixed(1)} KB` : `${pretty.length} 字符` }),
      h('span.json-viewer__spacer'),
      h('span.json-viewer__copy', { onclick: (event) => event.stopPropagation() }, copyButton(pretty, { label: copyLabel }))
    ),
    h('div.code-block', null, pre)
  );
}

function tryParseOrText(value) {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

/** Plain / JSON / diff code block with optional wrapping and copy. */
export function codeBlock(text, { label = '', maxHeight = '320px', wrap = false, copy = true, language = 'text' } = {}) {
  const content = text === null || text === undefined ? '' : String(text);
  const pre = h('pre.code-block__pre', { class: `${wrap ? 'is-wrap' : ''} ${language === 'json' ? 'json' : ''}`.trim(), style: { maxHeight } });
  if (language === 'json') {
    pre.appendChild(highlightedJson(tryParseOrText(content)));
  } else {
    pre.textContent = content;
  }
  return h(
    'div.code-block-wrap',
    null,
    label || copy
      ? h(
          'div.code-block__bar',
          null,
          h('span.code-block__label', { text: label }),
          copy ? copyButton(content) : null
        )
      : null,
    h('div.code-block', null, pre)
  );
}

/* ------------------------------------------------------------------ */
/* diffs                                                               */
/* ------------------------------------------------------------------ */

export function unifiedDiff(diffText, { maxHeight = 'none', wrap = true } = {}) {
  const lines = parseUnifiedDiff(diffText);
  if (!lines.length) {
    return h('p.muted.small', { text: '没有可展示的差异内容' });
  }
  const body = h('div.diff.diff--unified', { style: { maxHeight } });
  for (const line of lines) {
    if (line.type === 'hunk' || line.type === 'meta') {
      body.appendChild(h('div.diff__line.diff__line--meta', null, h('span.diff__text', { text: line.text })));
      continue;
    }
    if (line.type === 'note') {
      body.appendChild(h('div.diff__line.diff__line--note', null, h('span.diff__text', { text: line.text })));
      continue;
    }
    const prefix = line.type === 'add' ? '+' : line.type === 'del' ? '-' : ' ';
    body.appendChild(
      h(
        `div.diff__line.diff__line--${line.type}`,
        null,
        h('span.diff__no', { text: line.oldNo ?? '' }),
        h('span.diff__no', { text: line.newNo ?? '' }),
        h('span.diff__sign', { text: prefix }),
        h(`span.diff__text${wrap ? ' is-wrap' : ''}`, { text: line.text })
      )
    );
  }
  return body;
}

export function splitDiff(diffText, { maxHeight = 'none' } = {}) {
  const rows = buildSplitDiff(diffText);
  if (!rows.length) return h('p.muted.small', { text: '没有可展示的差异内容' });
  const body = h('div.diff.diff--split', { style: { maxHeight } });
  for (const row of rows) {
    if (row.kind === 'hunk') {
      body.appendChild(h('div.diff__line.diff__line--meta', null, h('span.diff__text', { text: row.text })));
      continue;
    }
    const left = row.left;
    const right = row.right;
    const side = (cell, sideName) => {
      if (!cell) return h(`div.diff__cell.diff__cell--empty`, null, h('span.diff__no', { text: '' }), h('span.diff__text', { text: '' }));
      return h(
        `div.diff__cell.diff__cell--${cell.type}`,
        null,
        h('span.diff__no', { text: cell.type === 'ctx' ? (sideName === 'left' ? cell.oldNo : cell.newNo) : (sideName === 'left' ? cell.oldNo : cell.newNo) ?? '' }),
        h('span.diff__text.is-wrap', { text: cell.text })
      );
    };
    body.appendChild(h('div.diff__row', null, side(left, 'left'), side(right, 'right')));
  }
  return body;
}

/* ------------------------------------------------------------------ */
/* run log formatting helpers (used by runLog.js)                      */
/* ------------------------------------------------------------------ */

export function copyRedactedDetail(value, label = '复制脱敏详情') {
  return button({
    label,
    iconName: 'copy',
    variant: 'ghost',
    size: 'sm',
    onClick: async () => {
      const safe = redactValue(value);
      const asText = typeof safe === 'string' ? redactStructuredText(safe) : safeJsonStringify(safe);
      const ok = await copyText(asText);
      toast(ok ? '已复制脱敏详情' : '复制失败', { type: ok ? 'success' : 'warning', duration: 1800 });
    },
  });
}

export { focusElement, isEditableTarget };
