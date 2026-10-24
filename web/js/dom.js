/**
 * Minimal DOM helpers used by every view.
 *
 * ``h()`` builds elements without innerHTML so user supplied strings
 * (project names, queries, event payloads) can never be interpreted as markup.
 * Only the static SVG icon paths below are injected as trusted markup.
 */

const SVG_NS = 'http://www.w3.org/2000/svg';

/* Static, authored icon paths (16x16 viewBox, stroke based). */
const ICONS = {
  projects: '<path d="M2.5 4.5h4l1.4 1.8h5.6v6.2a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1z"/><path d="M2.5 6.3V4.2a1 1 0 0 1 1-1h1.6"/>',
  overview: '<rect x="2.5" y="2.5" width="4.4" height="4.4" rx="1"/><rect x="9.1" y="2.5" width="4.4" height="4.4" rx="1"/><rect x="2.5" y="9.1" width="4.4" height="4.4" rx="1"/><rect x="9.1" y="9.1" width="4.4" height="4.4" rx="1"/>',
  skill: '<path d="M4 2.5h5.2l3.3 3.3v7.7a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1z"/><path d="M9 2.6v3.6h3.4"/><path d="M6 9.4l-1.2 1.3L6 12"/>',
  environment: '<path d="M3 4.5h7M12.5 4.5H13M3 11.5h1.5M7 11.5h6"/><circle cx="11" cy="4.5" r="1.6"/><circle cx="5.5" cy="11.5" r="1.6"/>',
  dataset: '<ellipse cx="8" cy="4" rx="5" ry="2"/><path d="M3 4v4c0 1.1 2.2 2 5 2s5-.9 5-2V4"/><path d="M3 8v4c0 1.1 2.2 2 5 2s5-.9 5-2V8"/>',
  versions: '<path d="M2.8 8a5.2 5.2 0 1 0 1.5-3.7"/><path d="M2.6 2.6v2.6h2.6"/><path d="M8 5.2V8l2 1.2"/>',
  run: '<path d="M2.5 8h2.2l1.5-3.6L8.7 12l1.6-4h3.2"/>',
  plus: '<path d="M8 3v10M3 8h10"/>',
  search: '<circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2 14 14"/>',
  filter: '<path d="M2.5 3h11L9.5 8v4.2l-3 1.6V8z"/>',
  refresh: '<path d="M13.2 8a5.2 5.2 0 1 1-1.5-3.7"/><path d="M13.4 2.6v2.6h-2.6"/>',
  check: '<path d="M3 8.4l3.2 3.2L13 4.8"/>',
  x: '<path d="M4 4l8 8M12 4l-8 8"/>',
  alert: '<path d="M8 2.5l6 10.5H2z"/><path d="M8 6.2v3.1"/><circle cx="8" cy="11.4" r=".6" fill="currentColor" stroke="none"/>',
  info: '<circle cx="8" cy="8" r="5.6"/><path d="M8 7.2v4"/><circle cx="8" cy="4.9" r=".6" fill="currentColor" stroke="none"/>',
  clock: '<circle cx="8" cy="8" r="5.6"/><path d="M8 4.8V8l2.2 1.4"/>',
  chevronDown: '<path d="M4 6l4 4 4-4"/>',
  chevronRight: '<path d="M6 4l4 4-4 4"/>',
  arrowLeft: '<path d="M13 8H3M7 4L3 8l4 4"/>',
  arrowRight: '<path d="M3 8h10M9 4l4 4-4 4"/>',
  external: '<path d="M9.5 3H13v3.5"/><path d="M13 3 7.5 8.5"/><path d="M11.5 9.5V12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5.5a1 1 0 0 1 1-1h2.5"/>',
  download: '<path d="M8 2.8v7.4"/><path d="M4.8 7.2 8 10.4l3.2-3.2"/><path d="M3 12.6h10"/>',
  copy: '<rect x="5.4" y="5.4" width="8" height="8" rx="1"/><path d="M10.6 3.4v-.2a.8.8 0 0 0-.8-.8H3.4a.8.8 0 0 0-.8.8v6.4c0 .4.4.8.8.8h.2"/>',
  cancel: '<circle cx="8" cy="8" r="5.6"/><path d="M4.2 4.2l7.6 7.6"/>',
  play: '<path d="M5 3.2l7 4.8-7 4.8z"/>',
  pause: '<path d="M5.5 3.5v9M10.5 3.5v9"/>',
  edit: '<path d="M9.8 3.4l2.8 2.8L6 12.8 2.8 13.2l.4-3.2z"/><path d="M8.6 4.6l2.8 2.8"/>',
  lock: '<rect x="3.4" y="7.2" width="9.2" height="6" rx="1"/><path d="M5.4 7.2V5.4a2.6 2.6 0 0 1 5.2 0v1.8"/>',
  archive: '<rect x="2.5" y="3" width="11" height="3.2" rx=".8"/><path d="M3.6 6.2v6a1 1 0 0 0 1 1h6.8a1 1 0 0 0 1-1v-6"/><path d="M6.6 8.6h2.8"/>',
  menu: '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>',
  save: '<path d="M3 3h8l2 2v8a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M5.5 3v3.5h4.5V3"/><path d="M5.5 13V9h5v4"/>',
  upload: '<path d="M8 11V3.4"/><path d="M4.8 6.6 8 3.4l3.2 3.2"/><path d="M3 12.6h10"/>',
  folder: '<path d="M2.5 4.5h4l1.4 1.8h5.6v6.2a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1z"/>',
  file: '<path d="M4 2.5h4.6l3.4 3.4v7.6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1z"/><path d="M8.6 2.6v3.4h3.4"/>',
  terminal: '<rect x="2.2" y="3" width="11.6" height="10" rx="1.2"/><path d="M4.6 6.4 6.6 8l-2 1.6M8.4 10h3"/>',
  spark: '<path d="M8 2.4l1.5 4.1 4.1 1.5-4.1 1.5L8 13.6l-1.5-4.1L2.4 8l4.1-1.5z"/>',
  link: '<path d="M6.6 9.4a2.6 2.6 0 0 1 0-3.7l1.5-1.5a2.6 2.6 0 0 1 3.7 3.7l-.8.8"/><path d="M9.4 6.6a2.6 2.6 0 0 1 0 3.7l-1.5 1.5a2.6 2.6 0 0 1-3.7-3.7l.8-.8"/>',
  eye: '<path d="M1.8 8S4.2 4.2 8 4.2 14.2 8 14.2 8 11.8 11.8 8 11.8 1.8 8 1.8 8z"/><circle cx="8" cy="8" r="1.8"/>',
  more: '<circle cx="3.8" cy="8" r=".9" fill="currentColor" stroke="none"/><circle cx="8" cy="8" r=".9" fill="currentColor" stroke="none"/><circle cx="12.2" cy="8" r=".9" fill="currentColor" stroke="none"/>',
  shield: '<path d="M8 2.2l5 1.8v4.2c0 3-2 5.1-5 5.9-3-.8-5-2.9-5-5.9V4z"/><path d="M5.8 8l1.6 1.6L10.4 6.6"/>',
  code: '<path d="M5.4 4.8 2.2 8l3.2 3.2M10.6 4.8 13.8 8l-3.2 3.2"/>',
  dot: '<circle cx="8" cy="8" r="3" fill="currentColor" stroke="none"/>',
  minus: '<path d="M3.4 8h9.2"/>',
  history: '<path d="M2.5 8a5.5 5.5 0 1 0 1.4-3.7"/><path d="M2.4 2.6v2.8h2.8"/><path d="M8 5.4V8l2 1.2"/>',
};

/**
 * Build an element.
 *
 * Tag syntax: ``div.class#id`` / ``div.a.b``.
 * Attribute handling:
 *  - ``class`` / ``className`` merge with the tag classes
 *  - ``text`` sets textContent (safe for user data)
 *  - ``dataset`` assigns data-* keys
 *  - ``style`` accepts an object or a string
 *  - ``onclick`` / ``onClick`` / ``on:click`` register listeners
 *  - ``value`` / ``checked`` / ``selected`` etc. are applied after children
 *  - everything else becomes an attribute when it is a string/number,
 *    or a DOM property when the key exists on the element.
 *
 * @param {string} tag
 * @param {object|null} attrs
 * @param {...any} children
 * @returns {HTMLElement|SVGElement}
 */
export function h(tag, attrs, ...children) {
  let name = 'div';
  let id = null;
  const classes = [];
  if (typeof tag === 'string' && tag.trim()) {
    // Accept "a.nav-link is-active" (spaces become additional classes) so
    // dynamic class strings can be used directly as the tag expression.
    const tokens = tag.trim().split(/\s+/);
    const head = tokens[0];
    const match = head.match(/^([a-zA-Z][\w-]*)?(?:#([\w-]+))?((?:\.[\w-]+)*)$/);
    if (match) {
      name = match[1] || 'div';
      id = match[2] || null;
      if (match[3]) classes.push(...match[3].split('.').filter(Boolean));
      classes.push(...tokens.slice(1).filter(Boolean));
    } else {
      name = /^[a-zA-Z][\w-]*$/.test(head) ? head : 'div';
      classes.push(head, ...tokens.slice(1));
    }
  }
  const el = document.createElement(name);
  if (id) el.id = id;

  const deferred = {};
  if (attrs && typeof attrs === 'object') {
    for (const [rawKey, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      const key = rawKey === 'className' ? 'class' : rawKey;
      if (key === 'class') {
        classes.push(...String(value).split(/\s+/).filter(Boolean));
      } else if (key === 'text') {
        el.textContent = String(value);
      } else if (key === 'dataset' && typeof value === 'object') {
        for (const [dk, dv] of Object.entries(value)) {
          if (dv !== null && dv !== undefined) el.dataset[dk] = String(dv);
        }
      } else if (key === 'style') {
        if (typeof value === 'string') el.setAttribute('style', value);
        else Object.assign(el.style, value);
      } else if (key === 'attrs' && typeof value === 'object') {
        for (const [ak, av] of Object.entries(value)) {
          if (av !== null && av !== undefined && av !== false) el.setAttribute(ak, String(av));
        }
      } else if (/^on[:A-Za-z]/.test(key) && typeof value === 'function') {
        const evt = key.replace(/^on:?/, '').toLowerCase();
        el.addEventListener(evt, value);
      } else if (key.startsWith('data-') || key.startsWith('aria-')) {
        el.setAttribute(key, value === true ? 'true' : String(value));
      } else if (key === 'value' || key === 'checked' || key === 'selected') {
        deferred[key] = value;
      } else if (key in el && typeof value !== 'string') {
        try {
          el[key] = value;
        } catch {
          el.setAttribute(key, String(value));
        }
      } else {
        el.setAttribute(key, String(value));
      }
    }
  }
  if (classes.length) el.className = [...new Set(classes)].join(' ');

  appendChildren(el, children);

  if ('value' in deferred) {
    if (el instanceof HTMLSelectElement) el.value = String(deferred.value);
    else if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
      el.value = deferred.value;
    }
  }
  if ('checked' in deferred) el.checked = Boolean(deferred.checked);
  if ('selected' in deferred && 'selected' in el) el.selected = Boolean(deferred.selected);
  return el;
}

function appendChildren(parent, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false || child === true) continue;
    if (Array.isArray(child)) {
      appendChildren(parent, child);
    } else if (child instanceof Node) {
      parent.appendChild(child);
    } else {
      parent.appendChild(document.createTextNode(String(child)));
    }
  }
}

export function frag(...children) {
  const f = document.createDocumentFragment();
  appendChildren(f, children);
  return f;
}

export function clear(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function mount(container, ...children) {
  clear(container);
  appendChildren(container, children);
  return container;
}

export function text(value) {
  return document.createTextNode(value === null || value === undefined ? '' : String(value));
}

/** Static icon (trusted constant path data). */
export function icon(name, { size = 16, className = '' } = {}) {
  const path = ICONS[name] || ICONS.dot;
  const span = document.createElement('span');
  span.className = `icon ${className}`.trim();
  span.setAttribute('aria-hidden', 'true');
  span.innerHTML = `<svg xmlns="${SVG_NS}" viewBox="0 0 16 16" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
  return span;
}

export function hasIcon(name) {
  return Object.prototype.hasOwnProperty.call(ICONS, name);
}

export function debounce(fn, wait = 250) {
  let timer = null;
  return function debounced(...args) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      fn.apply(this, args);
    }, wait);
  };
}

let counter = 0;
export function uid(prefix = 'id') {
  counter += 1;
  return `${prefix}-${counter}`;
}

export async function copyText(value) {
  const str = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(str);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  const area = document.createElement('textarea');
  area.value = str;
  area.setAttribute('readonly', 'readonly');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}

export function isEditableTarget(target) {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

/** Focus helper used by the log "定位错误" flow. */
export function focusElement(el) {
  if (!el) return;
  if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
  try {
    el.focus({ preventScroll: true });
  } catch {
    el.focus();
  }
}

export function scrollToTop() {
  const main = document.getElementById('view');
  if (main) main.scrollTop = 0;
  window.scrollTo({ top: 0 });
}
