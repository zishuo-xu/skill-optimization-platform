export function requestDiagnosticTitle(payload = {}, target = 'optimizer') {
  const title = ({
    attempt_started: '优化请求开始尝试',
    response_received: '优化请求收到 HTTP 响应',
    connection_opened: '优化请求 WebSocket 已连接',
    first_byte: '优化请求收到首个数据块',
    first_content: '优化请求开始返回正文',
    first_tool: '优化请求开始返回工具调用',
    stream_progress: '优化请求接收进度',
    attempt_failed: '优化请求尝试结束：失败',
    attempt_completed: '优化请求尝试结束：完成',
  })[payload.phase] || '优化请求诊断';
  return title.replace('优化请求', target === 'optimizer' ? '优化请求' : payload.purpose === 'judge' ? '评分模型请求' : '业务模型请求');
}

export function requestDiagnosticSummary(p = {}) {
  return [
    p.attempt != null ? `第 ${p.attempt}/${p.max_attempts ?? '?'} 次尝试` : null,
    p.response_status != null ? `HTTP ${p.response_status}` : null,
    p.elapsed_ms != null ? `${p.elapsed_ms} ms` : null,
    p.first_byte_ms != null ? `首数据 ${p.first_byte_ms} ms` : null,
    p.first_content_ms != null ? `首正文 ${p.first_content_ms} ms` : null,
    p.received_bytes != null ? `收到 ${p.received_bytes} 字节` : null,
    p.content_chars != null ? `正文 ${p.content_chars} 字符` : null,
    p.tool_delta_count > 0 ? `工具参数 ${p.tool_argument_chars ?? 0} 字符` : null,
    p.reasoning_chars > 0 ? `推理字段 ${p.reasoning_chars} 字符（仅计数）` : null,
    p.error_code || null,
    p.retry_planned ? `计划 ${p.retry_in_s ?? 0} 秒后重试` : null,
  ].filter(Boolean).join(' · ');
}

export function requestDiagnosticCopy(event) {
  return { ...event.payload, event_context: Object.fromEntries(
    ['run_id', 'seq', 'type', 'stage', 'case_id', 'call_id', 'attempt', 'ts']
      .map(key => [key, event[key] ?? null])) };
}
