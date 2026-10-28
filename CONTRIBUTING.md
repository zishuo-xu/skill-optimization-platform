# 开发与协作指南

适用于人类与 AI 开发者。AI 接手先读 [AGENTS.md](AGENTS.md)，产品边界见[设计决策索引](docs/decisions/README.md)，用户操作见[使用指南](docs/USER_GUIDE.md)。

## 1. 准备环境

需要 Python 3.11+；前端源码无需构建，Node 用于语法检查和前端测试。

```bash
bash scripts/setup.sh
.venv/bin/python -m pytest --version
node --version
```

日常启动使用 `./start.sh`，Windows 使用 `start.bat`。脚本可能停止本仓库已运行的服务，首次启动会初始化内置离线示例；具体行为见[部署指南](docs/DEPLOYMENT.md)。不要对已有工作库使用 `--reset` 或 `make clean`。

后端配置在导入 `app.config` 时读取环境变量。需要手工集成实验时，先在该进程设置独立 `SKILLOPT_DATA_DIR` 和 `SKILLOPT_DB_PATH`，再导入应用；写库前调用 `db.init_db()`。pytest 的 [conftest](tests/conftest.py) 已完成临时目录、数据库和 Worker 隔离，正常测试不会使用项目的 `data/app.db`。

## 2. 一次改动的流程

1. **界定问题**：写明触发条件、当前结果、期望结果、影响范围和完成标准。先核对实际代码，历史报告只能提供线索。
2. **确认工作区**：检查分支、已有修改和相关文档；保留他人未完成工作，不用重置工作区来获得干净状态。
3. **完成改动**：围绕最小完整行为修改。涉及接口、数据迁移、评分或隔离时，检查调用方、历史数据展示和失败分支。
4. **选择验证**：按照下表运行相关检查；修复真实问题，不通过弱化断言、放宽评分或修改测试答案掩盖失败。
5. **同步文档**：用户行为更新指南，数据/接口更新契约，设计变化更新决策索引；CHANGELOG 记录用户可见变化。历史记录保留时点。
6. **审查提交**：查看最终差异与暂存区，确认未混入凭据、本地数据库、无关修改；提交记录写出具体改动和证据。

不要求为简单改字新增测试，也不要求每次重新运行昂贵的真实模型流程。已经执行并通过的检查，只有新增改动或未解决风险需要时才重复。

## 3. 验证命令与选择

| 改动范围 | 适合的检查 |
| --- | --- |
| 文档与合成示例 | 本地 Markdown 链接/锚点、JSON、Skill 导入和数据预检、diff 空白检查 |
| Agent loop / 选择加载 | `tests/test_select_skill_autoload.py`、`tests/test_progressive_loop_boundaries.py`、`tests/test_progressive_loop_cache.py` |
| 评分 / 参数 / 失败展示 | `tests/test_semantic_scoring.py`、`tests/test_fixture_diagnostics.py`、`tests/test_case_actual_results.py`、`tests/case_results.mjs` |
| 优化 / 候选链 / 数据隔离 | `tests/test_rsi_lineage.py`、`tests/test_rsi_pipeline_feedback.py`、`tests/test_run_version_isolation.py` |
| 数据编辑 / 迁移 | `tests/test_dataset_editing.py`、`tests/test_dataset_editing_api.py`、对应迁移测试 |
| 页面交互 / 试跑 | 相关 Node 测试、JS 语法检查、真实浏览器检查触发路径和最终结果 |
| 跨模块运行或存储行为 | 相关测试后运行全量 Python 与 Node 回归 |

这些是入口，不是排他清单；根据变更补充实际受影响的测试。

```bash
# 一个执行层定向检查示例
.venv/bin/python -m pytest -q tests/test_progressive_loop_boundaries.py

# 全量后端与前端回归
.venv/bin/python -m pytest -q
node --test tests/*.mjs

# 语法与差异检查
for f in web/js/*.js web/js/views/*.js; do node --check "$f"; done
bash -n start.sh scripts/setup.sh run.sh
git diff --check
```

`make test` 当前只执行 pytest。现有 [CI](.github/workflows/ci.yml) 执行 Python 回归、前端/脚本语法检查、示例 seed 和 Windows 启动冒烟；**尚未执行 Node 测试套件，也不执行真实模型或人工浏览器验收**。不能用 CI 通过替代这些检查。

### 文档示例的离线校验

下面的命令从仓库根目录执行，只解析文档资产，不写数据库、不调用模型：

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from app import dataset, skill_package

root = Path('docs/examples/task-collaboration')
for name in ('skill-v1', 'skill-candidate'):
    folder = root / name
    files = {p.relative_to(folder).as_posix(): p.read_bytes()
             for p in folder.rglob('*') if p.is_file()}
    package = skill_package.import_package(skill_package.build_zip(files))
    assert not package['errors'], package['errors']

items = [json.loads(line) for line in (root / 'dataset.jsonl').read_text().splitlines()
         if line.strip()]
manifest = json.loads((root / 'skill-v1' / 'manifest.json').read_text())
report = dataset.validate_and_prepare(items, manifest=manifest,
    split_config={'optimize': 0.6, 'validation': 0.3, 'final_test': 0.1}, seed=42)
assert not report['errors'], report['errors']
assert len(report['cases']) == 42
for split, count in [('optimize', 24), ('validation', 12), ('final_test', 6)]:
    assert sum(c['split'] == split for c in report['cases']) == count
print('Skill 与 42 条示例数据校验通过；warnings:', report['warnings'])
PY
```

## 4. 真实模型与页面验证

真实 API 验证使用独立项目或任务已授权的项目。测试前冻结 Skill、环境和数据；失败后先判断业务能力、fixture、模型接入还是评分错误，不能直接修改 expected 追求通过。

记录这些证据：输入版本/hash、运行 ID、实际模型、模式/划分/重复次数、原版和候选成绩、基础设施错误、fixture_miss、优化器是否回退、应用状态。独立验收内容不得进入优化器输入。

页面走查观察实际用户路径，例如：创建/导入 → 案例试跑 → 整批评测 → 候选差异 → 独立验收 → 应用。按本次改动选择必要路径；通过接口读到结果不能独自证明页面显示正确。截图和日志放在被忽略的 `output/`，提交文档仅引用脱敏摘要。

参见[真实模型教程](docs/REAL_MODEL_WALKTHROUGH.md)。该教程的历史分数不是对下一次运行的保证；业务工具仍为模拟返回，模型网络请求与真实业务副作用需分开报告。

## 5. 提交、推送和交接

提交前运行 `git diff`、`git diff --cached`，确认暂存文件与任务范围一致。提交消息使用具体标题和正文，例如：

```text
docs: 补齐 AI 开发入口并统一 Skill 优化文档

问题：接手 AI 缺少项目约束入口，旧指南混合日常优化与独立验收。
改动：新增 AGENTS、贡献指南和设计决策索引；同步使用/API/评分说明。
验证：记录本次实际执行的链接、示例校验或回归结果。
边界：说明未运行的检查，不将历史真实模型成绩写成本次结果。
```

用户要求提交或推送时完成对应操作；不要把本地提交称为已推送。推送前核对远端目标及分叉状态，不默认强制推送；推送后检查远端分支提交与本地一致。若创建 PR，描述问题、最终行为、验证及限制，而非按对话顺序复述过程。

交接至少写明：改了什么、为什么、验证证据、当前提交/推送状态和剩余问题。未完成任务的临时状态放在交接信息中，不追加到 AGENTS 的长期规则里。
