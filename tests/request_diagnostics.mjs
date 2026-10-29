import test from 'node:test';
import assert from 'node:assert/strict';
import { requestDiagnosticTitle, requestDiagnosticSummary, requestDiagnosticCopy } from '../web/js/requestDiagnostics.js';
import { isOptimizerEvent } from '../web/js/eventTypes.js';

test('request logs distinguish optimizer, business and judge and show slow partial output', () => {
  assert.equal(requestDiagnosticTitle({phase:'first_content'}), '优化请求开始返回正文');
  assert.equal(requestDiagnosticTitle({phase:'first_content'}, 'model'), '业务模型请求开始返回正文');
  assert.equal(requestDiagnosticTitle({phase:'first_content',purpose:'judge'}, 'model'), '评分模型请求开始返回正文');
  assert.equal(isOptimizerEvent('optimizer_request_info'), true);
  assert.equal(isOptimizerEvent('model_request_info'), false);
  const text = requestDiagnosticSummary({attempt:1,max_attempts:2,response_status:200,
    elapsed_ms:60000,received_bytes:1234,content_chars:0,reasoning_chars:456,
    retry_planned:true,retry_in_s:1,error_code:'timeout'});
  assert.match(text, /HTTP 200/);
  assert.match(text, /正文 0 字符/);
  assert.match(text, /推理字段 456 字符（仅计数）/);
  assert.match(text, /计划 1 秒后重试/);
});

test('copied diagnostic includes correlation metadata without changing stored payload', () => {
  const event = {run_id:'run1',call_id:'call1',case_id:'case1',seq:23,
    type:'model_request_info',payload:{phase:'attempt_failed',attempt:2,elapsed_ms:1000}};
  const copied = requestDiagnosticCopy(event);
  assert.equal(copied.event_context.call_id,'call1');
  assert.equal(copied.event_context.run_id,'run1');
  assert.equal(copied.attempt,2);
  assert.equal(event.payload.event_context,undefined);
});
