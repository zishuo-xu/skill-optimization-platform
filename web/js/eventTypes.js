/**
 * Front-end mirror of ``app/event_types.py``.
 *
 * The registry drives log icons, Chinese step titles and the optimizer filter.
 * Keep the event names identical to the backend: they are the contract.
 */

export const STAGES = [
  { id: 'preflight', label: '预检', description: '检查文件、环境、数据与预算' },
  { id: 'baseline_eval', label: '基线评测', description: '在测试集上执行基线 Skill' },
  { id: 'error_analysis', label: '错误分析', description: '聚合失败证据与归因' },
  { id: 'candidate_generation', label: '生成候选', description: '请求优化器生成本地补丁' },
  { id: 'static_check', label: '静态检查', description: '检查路径、引用与禁止修改项' },
  { id: 'retest', label: '复测验证', description: '重跑候选或对基线和候选进行配对验收' },
  { id: 'decision', label: '评估结论', description: '按冻结规则判定是否达标' },
  { id: 'final_test', label: '最终测试', description: '在最终测试集上验收' },
];

export const STAGE_LABELS = Object.fromEntries(STAGES.map((s) => [s.id, s.label]));
export const STAGE_ORDER = STAGES.map((s) => s.id);

export function stageLabel(stage) {
  if (!stage) return '整轮';
  return STAGE_LABELS[stage] || stage;
}

export const LEVELS = ['debug', 'info', 'warning', 'error'];

export const LEVEL_META = {
  debug: { label: '调试', tone: 'neutral', icon: 'dot' },
  info: { label: '信息', tone: 'info', icon: 'dot' },
  warning: { label: '警告', tone: 'warning', icon: 'alert' },
  error: { label: '错误', tone: 'danger', icon: 'x' },
};

export const EVENT_CATEGORIES = {
  lifecycle: '运行',
  preflight: '预检',
  case: '案例执行',
  skill: 'Skill 读取',
  model: '模型调用',
  tool: '工具调用',
  score: '评分',
  optimizer: '优化器',
  patch: '补丁',
  static: '静态检查',
  retest: '复测',
  decision: '结论',
  config: '配置',
};

/** type -> {label, icon, category, defaultLevel} */
export const EVENT_META = {
  // lifecycle
  run_created: { label: '运行已创建', icon: 'play', category: 'lifecycle' },
  run_started: { label: '开始运行', icon: 'play', category: 'lifecycle' },
  run_completed: { label: '运行完成', icon: 'check', category: 'lifecycle' },
  run_failed: { label: '运行失败', icon: 'x', category: 'lifecycle', defaultLevel: 'error' },
  run_cancelled: { label: '运行已取消', icon: 'cancel', category: 'lifecycle' },
  run_interrupted: { label: '运行中断', icon: 'pause', category: 'lifecycle', defaultLevel: 'warning' },
  stage_started: { label: '开始阶段', icon: 'chevronRight', category: 'lifecycle' },
  stage_completed: { label: '阶段完成', icon: 'check', category: 'lifecycle' },
  stage_failed: { label: '阶段失败', icon: 'x', category: 'lifecycle', defaultLevel: 'error' },
  progress_updated: { label: '进度更新', icon: 'activity', category: 'lifecycle' },
  budget_exceeded: { label: '预算已用尽', icon: 'alert', category: 'lifecycle', defaultLevel: 'warning' },

  // preflight
  preflight_item: { label: '预检项', icon: 'shield', category: 'preflight' },
  preflight_failed: { label: '预检未通过', icon: 'alert', category: 'preflight', defaultLevel: 'error' },

  // case execution
  case_started: { label: '开始执行案例', icon: 'play', category: 'case' },
  case_completed: { label: '案例执行完成', icon: 'check', category: 'case' },
  case_failed: { label: '案例执行失败', icon: 'x', category: 'case', defaultLevel: 'error' },
  case_cancelled: { label: '案例已取消', icon: 'cancel', category: 'case', defaultLevel: 'warning' },
  skill_selected: { label: '选择子技能', icon: 'skill', category: 'skill' },
  skill_read: { label: '读取 Skill 文件', icon: 'file', category: 'skill' },
  model_call_started: { label: '调用模型', icon: 'spark', category: 'model' },
  model_call_finished: { label: '模型返回完成', icon: 'check', category: 'model' },
  model_call_failed: { label: '模型调用失败', icon: 'x', category: 'model', defaultLevel: 'error' },
  model_delta: { label: '模型增量输出', icon: 'spark', category: 'model' },
  tool_call_started: { label: '调用业务工具', icon: 'terminal', category: 'tool' },
  tool_call_finished: { label: '业务工具调用完成', icon: 'check', category: 'tool' },
  tool_result: { label: '模拟工具返回', icon: 'terminal', category: 'tool' },
  fixture_miss: { label: '模拟返回缺失', icon: 'alert', category: 'tool', defaultLevel: 'warning' },
  score_completed: { label: '评分完成', icon: 'check', category: 'score' },
  score_failed: { label: '评分失败', icon: 'x', category: 'score', defaultLevel: 'error' },
  judge_completed: { label: '评分模型完成判定', icon: 'shield', category: 'score' },
  judge_failed: { label: '评分模型调用失败', icon: 'x', category: 'score', defaultLevel: 'error' },
  judge_not_configured: { label: '未配置评分模型', icon: 'alert', category: 'score', defaultLevel: 'warning' },

  // optimizer
  optimizer_input_built: { label: '已构建优化器输入', icon: 'info', category: 'optimizer' },
  optimizer_call_started: { label: '已提交优化请求', icon: 'spark', category: 'optimizer' },
  optimizer_call_finished: { label: '优化器返回内容', icon: 'check', category: 'optimizer' },
  optimizer_call_failed: { label: '优化器调用失败', icon: 'x', category: 'optimizer', defaultLevel: 'error' },
  optimizer_empty_response: { label: '优化器返回空内容', icon: 'alert', category: 'optimizer', defaultLevel: 'error' },
  optimizer_parse_failed: { label: '优化器返回内容无法解析', icon: 'alert', category: 'optimizer', defaultLevel: 'error' },
  optimizer_not_configured: { label: '未配置优化模型', icon: 'alert', category: 'optimizer', defaultLevel: 'error' },
  optimizer_no_change: { label: '优化器主动无修改', icon: 'info', category: 'optimizer' },
  optimizer_output_parsed: { label: '优化结果解析完成', icon: 'check', category: 'optimizer' },
  fallback_started: { label: '已触发启发式回退', icon: 'refresh', category: 'optimizer' },
  fallback_no_change: { label: '回退未生成修改', icon: 'alert', category: 'optimizer', defaultLevel: 'error' },

  // patch / static / retest
  patch_created: { label: '已生成候选补丁', icon: 'edit', category: 'patch' },
  patch_applied: { label: '已应用补丁到隔离副本', icon: 'check', category: 'patch' },
  static_check_started: { label: '开始静态检查', icon: 'shield', category: 'static' },
  static_check_item: { label: '静态检查项', icon: 'shield', category: 'static' },
  static_check_passed: { label: '静态检查通过', icon: 'check', category: 'static' },
  static_check_failed: { label: '静态检查未通过', icon: 'x', category: 'static', defaultLevel: 'error' },
  retest_started: { label: '开始复测', icon: 'play', category: 'retest' },
  retest_completed: { label: '复测完成', icon: 'check', category: 'retest' },

  // decision
  decision_made: { label: '得出评测结论', icon: 'shield', category: 'decision' },
  rule_evaluated: { label: '接受规则判定', icon: 'info', category: 'decision' },
  regression_detected: { label: '检测到回归', icon: 'alert', category: 'decision', defaultLevel: 'warning' },

  // config
  config_error: { label: '配置错误', icon: 'x', category: 'config', defaultLevel: 'error' },
};

export function eventMeta(type) {
  return (
    EVENT_META[type] || {
      label: type || '未知事件',
      icon: 'dot',
      category: 'lifecycle',
      defaultLevel: 'info',
    }
  );
}

export function eventLabel(type) {
  return eventMeta(type).label;
}

export function isOptimizerEvent(type) {
  return eventMeta(type).category === 'optimizer';
}

export function isIssueEvent(event) {
  return Boolean(event) && (event.level === 'warning' || event.level === 'error');
}

export const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
export const ACTIVE_STATUSES = new Set(['queued', 'running', 'cancel_requested']);

export const RUN_STATUS_META = {
  queued: { label: '排队中', icon: 'clock', tone: 'neutral' },
  running: { label: '运行中', icon: 'run', tone: 'info', pulse: true },
  cancel_requested: { label: '取消中', icon: 'cancel', tone: 'warning', pulse: true },
  completed: { label: '已完成', icon: 'check', tone: 'success' },
  failed: { label: '运行失败', icon: 'x', tone: 'danger' },
  cancelled: { label: '已取消', icon: 'cancel', tone: 'neutral' },
  interrupted: { label: '已中断', icon: 'pause', tone: 'warning' },
};

export const DECISION_META = {
  accept: { label: '评测有改善', short: '有改善', icon: 'check', tone: 'success' },
  reject: { label: '未达标', short: '未达标', icon: 'x', tone: 'danger' },
  inconclusive: { label: '证据不足', short: '证据不足', icon: 'info', tone: 'warning' },
  // Display-only: an inconclusive run whose optimize set had nothing to fix.
  no_failures: { label: '无需优化', short: '无需优化', icon: 'check', tone: 'neutral' },
};

/** Key into DECISION_META for a run, refining inconclusive runs with nothing to fix. */
export function decisionKey(run) {
  if (!run || !run.decision) return null;
  if (run.decision === 'inconclusive' && run.decision_code === 'no_failures') return 'no_failures';
  return run.decision;
}

export const MODE_META = {
  evaluate: { label: '普通评测', icon: 'play' },
  baseline: { label: '基线评测', icon: 'play' },
  optimize: { label: '单轮优化', icon: 'spark' },
  validate: { label: '正式验收', icon: 'shield' },
  final_test: { label: '最终测试', icon: 'shield' },
};

export const SPLIT_META = {
  optimize: { label: '测试集', tone: 'accent' },
  validation: { label: '独立验收集', tone: 'info' },
  final_test: { label: '最终测试集', tone: 'warning' },
};

export const ACTION_LABELS = {
  undetermined: '无法判定',
  tool_call: '调用工具',
  clarify: '请求澄清',
  no_action: '无需操作',
};

export const CASE_STATUS_META = {
  pass: { label: '通过', icon: 'check', tone: 'success' },
  fail: { label: '未通过', icon: 'x', tone: 'danger' },
  infra: { label: '基础设施错误', icon: 'alert', tone: 'warning' },
  invalid: { label: '无效', icon: 'cancel', tone: 'neutral' },
  error: { label: '执行失败', icon: 'x', tone: 'danger' },
  pending: { label: '待评测', icon: 'clock', tone: 'neutral' },
};

export const CHANGE_TYPE_META = {
  fixed: { label: '修好了', icon: 'check', tone: 'success', rank: 2 },
  regressed: { label: '变差了', icon: 'alert', tone: 'danger', rank: 0 },
  unchanged_pass: { label: '一直通过', icon: 'check', tone: 'success', rank: 3 },
  unchanged_fail: { label: '仍然失败', icon: 'x', tone: 'danger', rank: 1 },
  invalid: { label: '无效', icon: 'cancel', tone: 'neutral', rank: 4 },
  pending: { label: '进行中', icon: 'clock', tone: 'neutral', rank: 5 },
};

export const VERSION_STATUS_META = {
  published: { label: '已发布', tone: 'success', icon: 'check' },
  draft: { label: '草稿', tone: 'warning', icon: 'edit' },
  candidate: { label: '候选（未应用）', tone: 'accent', icon: 'spark' },
  archived: { label: '已归档', tone: 'neutral', icon: 'archive' },
};

export const RULE_LABELS = {
  R_no_decrease: '分发准确率 R 不下降',
  A_no_decrease: '工具入参准确率 A 不下降',
  J_no_decrease: '联合通过率 J 不下降',
  metrics_evaluable: '核心指标可评估',
  strict_improvement: 'R 或 A 至少一项严格提高',
  critical_no_regression: '关键案例不得从通过变失败',
  repeats_sufficient: '配对重复次数足够',
  routing_not_decreased: '分发准确率 R 不下降',
  tool_args_not_decreased: '工具入参准确率 A 不下降',
  at_least_one_improved: 'R 或 A 至少一项严格提高',
  joint_not_decreased: '联合通过率 J 不下降',
  critical_cases_no_regression: '关键案例不得从通过变失败',
  no_infra_errors: '不得存在基础设施错误',
  package_integrity: '候选包完整性检查通过',
  validation_complete: '验证运行完整',
  baseline_paired: '基线与候选使用同一验证协议',
};

export function ruleLabel(rule) {
  return RULE_LABELS[rule] || rule;
}
