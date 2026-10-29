/** AI diagnosis is an optional read-only report, never an acceptance decision. */
import { api } from '../api_v3.js';
import { h, mount } from '../dom.js';
import { button, copyButton, sectionCard, loadingState, inlineError } from '../components.js';
import { TERMINAL_STATUSES } from '../eventTypes.js';

export function createRunAnalysis(run) {
  const element = h('div.run-analysis');
  let currentRun = run;
  let data = null;
  let busy = false;
  let error = '';
  let question = '';
  let destroyed = false;

  function downloadText(text, suffix) {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
    const anchor = h('a', { href: url, download: `${run.id}-${suffix}.md` });
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function load() {
    try {
      data = await api.getRunAnalysis(run.id);
      error = '';
    } catch (err) {
      error = err.message;
    }
    render();
  }

  async function generate() {
    if (busy) return;
    busy = true;
    error = '';
    render();
    try {
      data.latest = await api.analyzeRun(run.id, question);
    } catch (err) {
      error = `${err.message}。可复制诊断材料；若请求中断，稍后刷新查看是否已保存。`;
    } finally {
      busy = false;
      render();
    }
  }

  function render() {
    if (destroyed) return;
    if (!data) {
      mount(element, error ? h('div.stack', null, inlineError(error),
        button({ label: '重试加载 AI 分析', onClick: load })) : loadingState('正在加载 AI 分析入口…'));
      return;
    }
    const report = data.latest;
    const terminal = TERMINAL_STATUSES.has(currentRun.status);
    const canAnalyze = terminal && data.configured && !busy;
    const model = data.model?.model || data.model?.model_name || data.model?.provider || '未配置';
    const description = data.configured
      ? `使用项目当前发布的优化模型：${model}。发送经过凭据脱敏的运行证据，生成排查结论，可复制给他人继续分析。`
      : '请在「Prompt 与模型」中配置并发布优化模型。也可直接复制诊断材料，发给其他 AI 或开发者分析。';
    const input = h('textarea.input', {
      rows: 2, maxlength: 2000, value: question, disabled: busy,
      placeholder: '可选：描述你遇到的现象，例如“业务模型正常，优化时报 404”',
      'aria-label': '补充问题描述',
      oninput: (event) => { question = event.target.value; },
    });
    const actions = [
      button({ label: busy ? '正在分析…' : report ? '重新分析' : '生成 AI 分析',
        variant: 'primary', disabled: !canAnalyze, onClick: generate }),
      button({ label: '刷新分析', disabled: busy, onClick: load }),
    ];
    const reportActions = report ? h('div.actions', null,
      copyButton(report.markdown, { label: '复制完整报告' }),
      button({ label: '下载报告', onClick: () => downloadText(report.markdown, 'analysis') })) : null;
    mount(element, sectionCard({ title: 'AI 分析', description, actions,
      body: h('div.stack.stack--sm', null,
        input,
        !terminal ? h('p.muted', { text: '运行结束后可生成分析；当前可导出已有诊断材料。' }) : null,
        busy ? h('p.muted', { role: 'status', text: '正在请求分析模型，请稍候。报告完成后会保存，可刷新查看。' }) : null,
        error ? inlineError(error) : null,
        report ? h('div.stack.stack--sm', null,
          h('p.muted', { text: `报告生成于 ${report.metadata?.generated_at || '未知时间'} · 模型 ${report.metadata?.model || report.metadata?.configured_model?.model || '未知'} · 证据截至事件 #${report.metadata?.through_event_seq ?? '—'}` }),
          report.status === 'failed' ? inlineError(`分析调用失败：${report.error?.message || '未知错误'}。诊断材料已保留。`) : null,
          report.analysis ? h('pre.run-analysis__text', { text: report.analysis }) : null,
          reportActions,
          h('details', null, h('summary', { text: '查看完整报告与证据（分享前可检查）' }),
            h('pre.run-analysis__text', { text: report.markdown }))) : null,
        h('div.actions', null,
          copyButton(() => data.evidence_markdown, { label: '复制诊断材料' }),
          button({ label: '下载诊断材料', onClick: () => downloadText(data.evidence_markdown, 'evidence') })),
        h('p.muted', { text: `诊断材料截至事件 #${data.through_event_seq ?? '—'}；需要最新材料时点击「刷新分析」。` }),
        h('p.muted', { text: '诊断材料采用采样并标注截断；独立验收与最终测试只提供汇总。分享前请检查业务敏感信息。AI 分析不改变评分或验收结论。' })
      ),
    }));
  }

  render();
  load();
  return { element, destroy() { destroyed = true; },
    updateRun(next) { const changed = currentRun.status !== next.status; currentRun = next; if (changed) render(); } };
}
