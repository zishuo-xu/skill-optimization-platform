/** Project raw execution events into the two user-facing business steps. */
const SKILL_EVENTS = new Set(['skill_selected', 'skill_read']);
const CALL_EVENTS = new Set(['tool_call_started', 'tool_call_finished', 'tool_result', 'fixture_miss']);

export function projectExecutionTrace(events = [], { complete = false } = {}) {
  const skills = [];
  const byPath = new Map();
  const calls = [];
  const open = new Map();
  const internal = [];
  const source = Array.isArray(events) ? events : [];
  const scopeOf = (event) => {
    const p = event?.payload || {};
    return [event?.variant || p.variant || '', event?.stage || p.stage || '', event?.repeat_index ?? p.repeat_index ?? ''].join('|');
  };
  const scopedKey = (scope, key) => scope === '||' ? key : `${scope}:${key}`;
  source.forEach((event, index) => {
    internal.push(event);
    const payload = event?.payload || {};
    const type = event?.type;
    if (payload.purpose === 'judge') return;
    if (SKILL_EVENTS.has(type)) {
      const skillId = payload.skill_id || '';
      const path = payload.path || payload.skill_path || '';
      const key = path || skillId || `skill-${index}`;
      const scoped = scopedKey(scopeOf(event), key);
      let item = byPath.get(scoped);
      if (!item) {
        item = { key: scoped, skillId, path, status: 'pending', cached: Boolean(payload.cached) };
        if (scopeOf(event) !== '||') item.scope = scopeOf(event);
        byPath.set(scoped, item); skills.push(item);
      }
      if (skillId) item.skillId = skillId;
      if (path) item.path = path;
      item.cached = item.cached || Boolean(payload.cached);
      if (type === 'skill_read') item.status = 'loaded';
      return;
    }
    if (CALL_EVENTS.has(type)) {
      const name = event?.tool || event?.name || payload.tool || payload.name || '业务工具';
      const callId = event?.call_id || payload.call_id || '';
      const openKey = `${scopeOf(event)}:${name}:${callId}`;
      if (type === 'tool_call_started') {
        const key = scopedKey(scopeOf(event), `${name}:${calls.length}`);
        const item = { key, name, arguments: event?.arguments ?? payload.arguments, response: undefined, status: 'running' };
        if (scopeOf(event) !== '||') item.scope = scopeOf(event);
        calls.push(item); open.set(openKey, item);
      } else {
        const item = open.get(openKey) || { key: scopedKey(scopeOf(event), `${name}:${calls.length}`), name, arguments: payload.arguments, response: undefined, status: 'unconfirmed' };
        if (!calls.includes(item)) { calls.push(item); open.set(openKey, item); }
        if (event?.arguments !== undefined || payload.arguments !== undefined) item.arguments = event?.arguments ?? payload.arguments;
        if (type === 'tool_result') { item.response = event?.response ?? payload.response; if (item.status !== 'failed') item.status = 'returned'; open.delete(openKey); }
        if (type === 'tool_call_finished' && payload.ok === false) item.status = 'failed';
        if (type === 'fixture_miss') { item.status = 'failed'; item.error = payload.issue?.message || payload.message || '工具返回失败'; }
        if (type === 'fixture_miss') open.delete(openKey);
      }
      return;
    }
  });
  if (complete) {
    skills.forEach((item) => { if (item.status === 'pending') item.status = 'unconfirmed'; });
    calls.forEach((item) => { if (item.status === 'running') item.status = 'unconfirmed'; });
  }
  return { skills, calls, internal, raw: source };
}

/** Attach variant-less audit events only when their execution scope is unambiguous. */
export function groupExecutionTrace(events = []) {
  const scopeOf = event => {
    const p = event.payload || {};
    return [event.variant ?? p.variant ?? '', event.stage ?? p.stage ?? '',
      String(event.repeat_index ?? p.repeat_index ?? '')];
  };
  const scopes = events.map(scopeOf);
  const groups = new Map();
  events.forEach((event, index) => {
    const scope = [...scopes[index]];
    if (!scope[0]) {
      const variants = new Set(scopes.filter(s => s[0] && s[1] === scope[1] && s[2] === scope[2]).map(s => s[0]));
      if (variants.size === 1) scope[0] = [...variants][0];
    }
    const key = JSON.stringify(scope);
    if (!groups.has(key)) groups.set(key, {scope, events: []});
    groups.get(key).events.push(event);
  });
  return [...groups.values()];
}
