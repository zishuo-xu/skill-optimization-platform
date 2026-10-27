/**
 * `/api/v3` HTTP client.
 *
 * * All paths are relative to the page origin, so the SPA works whether it is
 *   served by the backend or by a local static server with a proxy.
 * * Errors are normalised into ``ApiError`` with code / field_path / details.
 * * ``?mock=1`` installs a fake in-memory backend (see mock.js) through
 *   ``installMockTransport``; production code paths stay untouched.
 * * Real-time events use EventSource. The log component deduplicates by
 *   ``seq`` and asks ``event-history`` to fill reconnect gaps.
 */

const API_BASE = '/api/v3';

export class ApiError extends Error {
  /**
   * @param {string} message
   * @param {{code?:string, status?:number, field_path?:string|null, details?:any, network?:boolean, cause?:any}} [meta]
   */
  constructor(message, meta = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = meta.code || 'error';
    this.status = meta.status || 0;
    this.field_path = meta.field_path || null;
    this.details = meta.details || null;
    this.network = Boolean(meta.network);
    if (meta.cause) this.cause = meta.cause;
  }

  get isNotFound() {
    return this.status === 404 || this.code === 'not_found';
  }

  get isConflict() {
    return this.status === 409 || this.code === 'conflict';
  }
}

/* ------------------------------------------------------------------ */
/* transport plumbing                                                  */
/* ------------------------------------------------------------------ */

let transport = null;
let eventSourceFactory = null;
let downloadHandler = null;
let mockMode = false;

function defaultEventSourceFactory(url) {
  if (typeof window.EventSource !== 'function') {
    throw new Error('当前浏览器不支持实时事件（EventSource）');
  }
  return new window.EventSource(url);
}

function currentTransport() {
  return transport || httpTransport;
}

function currentEventSourceFactory() {
  return eventSourceFactory || defaultEventSourceFactory;
}

/** Used by mock.js only. */
export function installMockTransport({ fetchImpl, eventSource, download }) {
  transport = fetchImpl;
  eventSourceFactory = eventSource || null;
  downloadHandler = download || null;
  mockMode = true;
}

export function isMockMode() {
  return mockMode;
}

/** Test/debug helper: restore the real transport. */
export function resetTransport() {
  transport = null;
  eventSourceFactory = null;
  downloadHandler = null;
  mockMode = false;
}

function queryString(query) {
  if (!query) return '';
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue;
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
    } else {
      params.append(key, String(value));
    }
  }
  const str = params.toString();
  return str ? `?${str}` : '';
}

async function httpTransport({ method, path, query, body, headers, signal }) {
  const url = `${API_BASE}${path}${queryString(query)}`;
  const init = { method, headers: { Accept: 'application/json', ...headers }, signal };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json; charset=utf-8';
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new ApiError('请求已取消', { code: 'aborted', cause: error });
    }
    throw new ApiError('无法连接后端服务（/api/v3）。请确认后端已启动或检查网络。', {
      code: 'network_error',
      network: true,
      cause: error,
    });
  }
  const contentType = response.headers.get('content-type') || '';
  let data = null;
  if (response.status !== 204) {
    if (contentType.includes('application/json')) {
      data = await response.json().catch(() => null);
    } else {
      data = await response.text();
    }
  }
  if (!response.ok) {
    const errorBody = data && typeof data === 'object' ? data.error : null;
    const message =
      (errorBody && errorBody.message) ||
      (typeof data === 'string' && data.trim() ? data.trim().slice(0, 300) : '') ||
      `请求失败（HTTP ${response.status}）`;
    throw new ApiError(message, {
      code: (errorBody && errorBody.code) || `http_${response.status}`,
      status: response.status,
      field_path: errorBody && errorBody.field_path,
      details: errorBody && errorBody.details,
    });
  }
  return { status: response.status, data };
}

/** Run a trial over the opt-in NDJSON stream, invoking onEvent as events arrive. */
async function streamTrial(path, body, { onEvent, signal } = {}) {
  if (mockMode) {
    const data = await request('POST', path, { body, signal });
    for (const event of data?.events || []) {
      try { onEvent && onEvent(event); } catch { /* listener errors are isolated */ }
    }
    return data;
  }
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { Accept: 'application/x-ndjson', 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error?.name === 'AbortError') throw new ApiError('请求已取消', { code: 'aborted', cause: error });
    throw new ApiError('无法连接后端服务（/api/v3）', { code: 'network_error', network: true, cause: error });
  }
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const detail = data?.error || {};
    throw new ApiError(detail.message || `请求失败（HTTP ${response.status}）`, {
      code: detail.code || `http_${response.status}`, status: response.status,
      field_path: detail.field_path, details: detail.details,
    });
  }
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('ndjson') || !response.body) return response.json();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result = null;
  const consume = (line) => {
    if (!line.trim()) return;
    let packet;
    try { packet = JSON.parse(line); } catch (error) {
      throw new ApiError('试跑流包含无效事件数据', { code: 'trial_protocol_error', cause: error });
    }
    if (packet.type === 'event' && packet.event) {
      try { onEvent && onEvent(packet.event); } catch { /* listener errors are isolated */ }
    } else if (packet.type === 'result') result = packet.result;
    else if (packet.type === 'error') {
      const detail = packet.error || {};
      throw new ApiError(detail.message || '试跑失败', { code: detail.code || 'trial_error', details: detail.details });
    }
  };
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      lines.forEach(consume);
    }
    buffer += decoder.decode();
    consume(buffer);
  } catch (error) {
    if (error?.name === 'AbortError') throw new ApiError('请求已取消', { code: 'aborted', cause: error });
    throw error;
  } finally {
    try { await reader.cancel(); } catch { /* stream already closed */ }
    reader.releaseLock();
  }
  if (result === null) throw new ApiError('试跑流结束但没有结果', { code: 'trial_incomplete' });
  return result;
}

/**
 * Low-level request. Returns the parsed payload; throws ApiError on failure.
 */
export async function request(method, path, options = {}) {
  const result = await currentTransport()({
    method,
    path,
    query: options.query,
    body: options.body,
    headers: options.headers,
    signal: options.signal,
  });
  return result ? result.data : null;
}

function encodePath(path) {
  return String(path)
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/');
}

export function newIdempotencyKey(prefix = 'ui') {
  try {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
      return `${prefix}-${globalThis.crypto.randomUUID()}`;
    }
  } catch {
    /* ignore */
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function apiUrl(path, query) {
  return `${API_BASE}${path}${queryString(query)}`;
}

/* ------------------------------------------------------------------ */
/* public API methods                                                  */
/* ------------------------------------------------------------------ */

export const api = {
  /* health ---------------------------------------------------------- */
  health() {
    return request('GET', '/healthz');
  },

  /* projects -------------------------------------------------------- */
  listProjects({ includeArchived = false } = {}) {
    return request('GET', '/projects', { query: { include_archived: includeArchived || undefined } });
  },
  createProject({ name, description = '' }) {
    return request('POST', '/projects', { body: { name, description } });
  },
  getProject(projectId) {
    return request('GET', `/projects/${encodeURIComponent(projectId)}`);
  },
  updateProject(projectId, patch) {
    return request('PATCH', `/projects/${encodeURIComponent(projectId)}`, { body: patch });
  },
  archiveProject(projectId, archived = true) {
    return request('PATCH', `/projects/${encodeURIComponent(projectId)}`, { body: { archived } });
  },
  getOverview(projectId) {
    return request('GET', `/projects/${encodeURIComponent(projectId)}/overview`);
  },

  /* skill versions -------------------------------------------------- */
  listSkillVersions(projectId) {
    return request('GET', `/projects/${encodeURIComponent(projectId)}/skill-versions`);
  },
  getSkillVersion(versionId) {
    return request('GET', `/skill-versions/${encodeURIComponent(versionId)}`);
  },
  importSkillVersion(projectId, { files, manifest, message, status = 'published', parentId, setCurrent } = {}) {
    const body = { files, message, status };
    if (manifest !== undefined) body.manifest = manifest;
    if (parentId) body.parent_id = parentId;
    if (setCurrent !== undefined) body.set_current = setCurrent;
    return request('POST', `/projects/${encodeURIComponent(projectId)}/skill-versions`, { body });
  },
  uploadSkillZip(projectId, file, { message = '', setCurrent = true } = {}) {
    const form = new FormData();
    form.append('file', file, file.name || 'skill.zip');
    if (message) form.append('message', message);
    form.append('set_current', String(setCurrent));
    return request('POST', `/projects/${encodeURIComponent(projectId)}/skill-versions`, { body: form });
  },
  publishSkillVersion(versionId, setCurrent = true) {
    return request('POST', `/skill-versions/${encodeURIComponent(versionId)}/publish`, {
      body: { set_current: setCurrent },
    });
  },
  activateSkillVersion(versionId, expectedCurrentVersion) {
    return request('POST', `/skill-versions/${encodeURIComponent(versionId)}/activate`, {
      body: { expected_current_version: expectedCurrentVersion },
    });
  },
  async getSkillFile(versionId, filePath) {
    const data = await request(
      'GET',
      `/skill-versions/${encodeURIComponent(versionId)}/files/${encodePath(filePath)}`
    );
    if (typeof data === 'string') return { path: filePath, content: data, encoding: 'utf-8' };
    if (data && typeof data === 'object') {
      return {
        ...data,
        path: data.path || filePath,
        content: data.content ?? data.text ?? '',
        encoding: data.encoding || 'utf-8',
      };
    }
    return { path: filePath, content: '', encoding: 'utf-8' };
  },
  saveSkillFile(versionId, filePath, content) {
    return request('PUT', `/skill-versions/${encodeURIComponent(versionId)}/files/${encodePath(filePath)}`, {
      body: { content },
    });
  },
  getSkillVersionDiff(versionId, fromId) {
    return request('GET', `/skill-versions/${encodeURIComponent(versionId)}/diff`, {
      query: { from: fromId || undefined },
    });
  },
  exportSkillVersionUrl(versionId) {
    return apiUrl(`/skill-versions/${encodeURIComponent(versionId)}/export`);
  },

  /* environments ---------------------------------------------------- */
  listEnvironments(projectId) {
    return request('GET', `/projects/${encodeURIComponent(projectId)}/environments`);
  },
  getEnvironment(environmentId) {
    return request('GET', `/environments/${encodeURIComponent(environmentId)}`);
  },
  createEnvironment(projectId, payload) {
    return request('POST', `/projects/${encodeURIComponent(projectId)}/environments`, { body: payload });
  },
  publishEnvironment(environmentId, setCurrent = true) {
    return request('POST', `/environments/${encodeURIComponent(environmentId)}/publish`, {
      body: { set_current: setCurrent },
    });
  },
  testConnection(payload) {
    return request('POST', '/environments/test-connection', { body: payload });
  },

  /* datasets -------------------------------------------------------- */
  listDatasets(projectId) {
    return request('GET', `/projects/${encodeURIComponent(projectId)}/datasets`);
  },
  getDataset(datasetId) {
    return request('GET', `/datasets/${encodeURIComponent(datasetId)}`);
  },
  preflightDataset({ filename, content, splitConfig, seed, importPurpose }) {
    return request('POST', '/datasets/preflight', {
      body: {
        filename,
        content,
        split_config: splitConfig,
        import_purpose: importPurpose,
        seed,
      },
    });
  },
  createDataset(projectId, { filename, content, splitConfig, seed, excludeInvalid, sourceName, importPurpose }) {
    return request('POST', `/projects/${encodeURIComponent(projectId)}/datasets`, {
      body: {
        filename,
        content,
        split_config: splitConfig,
        import_purpose: importPurpose,
        seed,
        exclude_invalid: Boolean(excludeInvalid),
        source_name: sourceName || filename,
      },
    });
  },
  createDatasetDraft(datasetId) {
    return request('POST', `/datasets/${encodeURIComponent(datasetId)}/draft`, { body: {} });
  },
  updateDatasetCase(datasetId, caseId, { case: caseValue, expectedHash } = {}) {
    return request('PUT', `/datasets/${encodeURIComponent(datasetId)}/cases/${encodeURIComponent(caseId)}`, {
      body: { case: caseValue, expected_hash: expectedHash },
    });
  },
  publishDataset(datasetId, { expectedHash, expectedCurrentVersion, setCurrent = true } = {}) {
    return request('POST', `/datasets/${encodeURIComponent(datasetId)}/publish`, {
      body: {
        expected_hash: expectedHash,
        expected_current_version: expectedCurrentVersion,
        set_current: setCurrent,
      },
    });
  },
  listDatasetCases(datasetId, { split, search, tag, status, limit = 200, offset = 0 } = {}) {
    return request('GET', `/datasets/${encodeURIComponent(datasetId)}/cases`, {
      query: { split, search, tag, status, limit, offset },
    });
  },
  getDatasetCase(datasetId, caseId) {
    return request('GET', `/datasets/${encodeURIComponent(datasetId)}/cases/${encodeURIComponent(caseId)}`);
  },

  /* versions -------------------------------------------------------- */
  listVersions(projectId) {
    return request('GET', `/projects/${encodeURIComponent(projectId)}/versions`);
  },

  /* runs ------------------------------------------------------------ */
  listRuns({ projectId, status, limit = 20, offset = 0 } = {}) {
    return request('GET', '/runs', { query: { project_id: projectId, status, limit, offset } });
  },
  createRun(payload, { idempotencyKey } = {}) {
    return request('POST', '/runs', {
      body: payload,
      headers: { 'Idempotency-Key': idempotencyKey || newIdempotencyKey('run') },
    });
  },
  getRun(runId) {
    return request('GET', `/runs/${encodeURIComponent(runId)}`);
  },
  cancelRun(runId) {
    return request('POST', `/runs/${encodeURIComponent(runId)}/cancel`, { body: {} });
  },
  applyRun(runId, { expectedCurrentVersion, candidateId }) {
    return request('POST', `/runs/${encodeURIComponent(runId)}/apply`, {
      body: {
        expected_current_version: expectedCurrentVersion ?? null,
        candidate_id: candidateId || undefined,
      },
    });
  },
  getRunCases(runId, { phase, onlyFailed, search, limit = 200, offset = 0 } = {}) {
    return request('GET', `/runs/${encodeURIComponent(runId)}/cases`, {
      query: {
        phase,
        only_failed: onlyFailed || undefined,
        search,
        limit,
        offset,
      },
    });
  },
  getRunCase(runId, caseId) {
    return request('GET', `/runs/${encodeURIComponent(runId)}/cases/${encodeURIComponent(caseId)}`);
  },
  getRunDiff(runId) {
    return request('GET', `/runs/${encodeURIComponent(runId)}/diff`);
  },
  getRunIssues(runId) {
    return request('GET', `/runs/${encodeURIComponent(runId)}/issues`);
  },
  getEventHistory(runId, { afterSeq = 0, limit = 200, level, type, caseId, stage } = {}) {
    return request('GET', `/runs/${encodeURIComponent(runId)}/event-history`, {
      query: {
        after_seq: afterSeq,
        limit,
        level,
        type,
        case_id: caseId,
        stage,
      },
    });
  },
  getModelOutput(runId, seq) {
    return request('GET', `/runs/${encodeURIComponent(runId)}/events/${encodeURIComponent(seq)}/model-output`);
  },
  async getTraceArtifact(runId, artifactId) {
    const data = await request(
      'GET',
      `/runs/${encodeURIComponent(runId)}/trace-artifacts/${encodeURIComponent(artifactId)}`
    );
    if (data && typeof data === 'object' && !Array.isArray(data) && Object.prototype.hasOwnProperty.call(data, 'content')) {
      return { artifact: data.artifact || null, content: data.content };
    }
    return { artifact: null, content: data };
  },
  downloadReportUrl(runId, format = 'json') {
    return apiUrl(`/runs/${encodeURIComponent(runId)}/report.${format}`);
  },
  /** 试跑：用某次运行的基线 / 候选 Skill 重跑一个案例（不写正式成绩）。 */
  trialRunCase(runId, payload, options = {}) {
    return streamTrial(`/runs/${encodeURIComponent(runId)}/debug-cases`, payload, options);
  },

  /** 试跑：用项目当前（或指定）版本跑一个案例或一句自由输入。 */
  trialProjectCase(projectId, payload, options = {}) {
    return streamTrial(`/projects/${encodeURIComponent(projectId)}/debug-cases`, payload, options);
  },
};

/* ------------------------------------------------------------------ */
/* downloads                                                           */
/* ------------------------------------------------------------------ */

function triggerBlobDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename || 'download';
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/**
 * Download through the mock backend when active, otherwise let the browser
 * follow the relative URL (Content-Disposition from the API).
 */
export async function download(url, filename = '') {
  if (mockMode && downloadHandler) {
    const blob = await downloadHandler(url);
    triggerBlobDownload(blob, filename);
    return;
  }
  const anchor = document.createElement('a');
  anchor.href = url;
  if (filename) anchor.download = filename;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

/* ------------------------------------------------------------------ */
/* SSE subscription                                                    */
/* ------------------------------------------------------------------ */

/**
 * Subscribe to `/runs/{rid}/events`.
 *
 * The browser reconnects EventSource automatically; on every (re)open the
 * caller receives ``onGap(lastSeq)`` so it can fetch the missing range from
 * ``event-history``. Duplicate ``seq`` values are filtered in the log layer.
 *
 * @param {string} runId
 * @param {{afterSeq?:number, onEvent:(event:object)=>void, onStatus:(state:'open'|'error'|'closed')=>void, onGap?:(lastSeq:number)=>void}} options
 * @returns {{close:()=>void, lastSeq:number}}
 */
export function subscribeRunEvents(runId, { afterSeq = 0, onEvent, onStatus, onGap } = {}) {
  let closed = false;
  let source = null;
  let lastSeq = Number(afterSeq) || 0;
  let openedOnce = false;
  let retryTimer = null;
  let attempt = 0;
  const baseUrl = `${API_BASE}/runs/${encodeURIComponent(runId)}/events`;

  const report = (state) => {
    try {
      onStatus && onStatus(state);
    } catch {
      /* listener errors must not kill the stream */
    }
  };

  const handleEvent = (event) => {
    if (!event || !event.data) return;
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    const seq = Number(payload.seq ?? (event.lastEventId || NaN));
    if (Number.isFinite(seq)) {
      if (seq <= lastSeq) return;
      if (openedOnce && seq > lastSeq + 1) {
        try {
          onGap && onGap(lastSeq);
        } catch {
          /* ignore */
        }
      }
      lastSeq = seq;
    }
    try {
      onEvent && onEvent(payload);
    } catch (error) {
      console.error('处理运行事件失败', error);
    }
  };

  const scheduleReconnect = () => {
    if (closed || retryTimer) return;
    attempt += 1;
    const delay = Math.min(15_000, 1000 * attempt);
    retryTimer = setTimeout(() => {
      retryTimer = null;
      connect();
    }, delay);
  };

  const connect = () => {
    if (closed) return;
    if (source) {
      try {
        source.close();
      } catch {
        /* ignore */
      }
      source = null;
    }
    const url = `${baseUrl}?after_seq=${lastSeq}`;
    try {
      source = currentEventSourceFactory()(url);
    } catch (error) {
      report('error');
      scheduleReconnect();
      return;
    }
    source.onopen = () => {
      attempt = 0;
      const isReopen = openedOnce;
      openedOnce = true;
      report('open');
      if (isReopen) {
        try {
          onGap && onGap(lastSeq);
        } catch {
          /* ignore */
        }
      }
    };
    source.onmessage = handleEvent;
    source.onerror = () => {
      if (closed) {
        report('closed');
        return;
      }
      report('error');
      // EventSource.CLOSED === 2 -> the browser gave up; retry ourselves.
      if (source && source.readyState === 2) {
        scheduleReconnect();
      }
    };
  };

  connect();

  return {
    close() {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = null;
      if (source) {
        try {
          source.close();
        } catch {
          /* ignore */
        }
        source = null;
      }
      report('closed');
    },
    get lastSeq() {
      return lastSeq;
    },
  };
}

export default api;
