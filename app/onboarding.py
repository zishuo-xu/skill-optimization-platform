"""Install the task-collaboration guide atomically without running models."""
from __future__ import annotations

import json
import shutil
from collections import Counter

from . import config, dataset, db, hashing, ids, persistence, skill_package
from .errors import ValidationError

KEY = "task-collaboration-v1"
TITLE = "任务协作入门"
NAME = "入门引导：任务协作"


def _assets():
    root = config.REPO_ROOT / "docs/examples/task-collaboration"
    folder = root / "skill-v1"
    files = {p.relative_to(folder).as_posix(): p.read_bytes()
             for p in sorted(folder.rglob('*')) if p.is_file()}
    package = skill_package.import_package(skill_package.build_zip(files))
    if package['errors']:
        raise ValidationError('引导 Skill 校验失败', details=package['errors'])
    items, _ = dataset.parse_payload((root / 'dataset.jsonl').read_bytes(), 'dataset.jsonl')
    splits = {'optimize': 0.6, 'validation': 0.3, 'final_test': 0.1}
    if Counter(item.get('split') for item in items if isinstance(item, dict)) != {'optimize': 24, 'validation': 12, 'final_test': 6}:
        raise ValidationError('引导数据集必须显式保留 24/12/6 划分')
    report = dataset.validate_and_prepare(items, manifest=package['manifest'], split_config=splits, seed=42)
    counts = Counter(c['split'] for c in report['cases'])
    if report['errors'] or counts != {'optimize': 24, 'validation': 12, 'final_test': 6}:
        raise ValidationError('引导数据集校验失败', details=report['errors'])
    env = json.loads((config.SAMPLES_DIR / 'environment.json').read_text(encoding='utf-8'))
    env['prompt'] = (root / 'prompt.md').read_text(encoding='utf-8')
    env['model_config'] = {'provider': 'openai_http', 'base_url': '', 'model': '', 'max_tokens': 2048, 'temperature': 0, 'timeout_s': 60}
    env['optimizer_config'] = {'provider': 'openai_http', 'base_url': '', 'model': '', 'max_tokens': 12000, 'temperature': 0, 'timeout_s': 120}
    env['scorer_config'] = {'version': 'score-v1', 'compare': {'version': 'compare-v2', 'mode': 'strict', 'allow_extra_keys': False, 'field_rules': [{'path': '$.description', 'tool': 'create_task', 'mode': 'semantic'}]}}
    env['reproducibility_note'] = '任务协作入门示例；配置业务、优化及评分模型后发布环境。业务工具使用 fixture。'
    model = env['model_config']
    env['adapter_hash'] = hashing.hash_json({'provider': model.get('provider'), 'model': model.get('model'), 'request_template': None, 'stream_parser': None})
    return package, report, splits, env


def ensure_guide():
    """Install once. SQLite lock serializes processes; all metadata commits together."""
    conn = db.get_connection()
    existing = conn.execute('SELECT project_id FROM onboarding_guides WHERE guide_key=?', (KEY,)).fetchone()
    if existing:
        return persistence.get_project(existing['project_id'])
    package, report, splits, env = _assets()
    project_id, skill_id, env_id, dataset_id = [ids.new_id(k) for k in ('project', 'skill_version', 'environment', 'dataset')]
    stamp = persistence.now_iso()
    dump = persistence._dumps
    target = persistence.skill_files_dir(skill_id)
    created_files = False
    try:
        with db.transaction() as conn:
            existing = conn.execute('SELECT project_id FROM onboarding_guides WHERE guide_key=?', (KEY,)).fetchone()
            if existing:
                project_id = existing['project_id']
            else:
                target.mkdir(parents=True, exist_ok=False)
                created_files = True
                for rel, content in package['files'].items():
                    path = target / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                conn.execute('INSERT INTO projects (id,name,description,current_skill_version_id,current_dataset_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?)', (project_id, NAME, '使用任务协作示例体验真实模型评测、优化与独立验收。', skill_id, dataset_id, stamp, stamp))
                conn.execute('INSERT INTO skill_versions (id,project_id,status,manifest_json,files_json,content_hash,message,created_at) VALUES (?,?,?,?,?,?,?,?)', (skill_id, project_id, 'published', dump(package['manifest']), dump(hashing.file_hashes(package['files'])), hashing.hash_files(package['files']), '任务协作引导初始 Skill', stamp))
                conn.execute('INSERT INTO environments (id,project_id,version_seq,status,prompt,model_config_json,optimizer_config_json,loader_config_json,run_config_json,scorer_config_json,acceptance_config_json,adapter_hash,hash,reproducibility_note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (env_id, project_id, 1, 'draft', env['prompt'], dump(env['model_config']), dump(env['optimizer_config']), dump(env['loader_config']), dump(env['run_config']), dump(env['scorer_config']), dump(env['acceptance_config']), env['adapter_hash'], persistence.compute_environment_hash(env), env['reproducibility_note'], stamp))
                cases = report['cases']
                conn.execute('INSERT INTO datasets (id,project_id,version_seq,status,hash,split_config_json,source_name,import_report_json,created_at,published_at) VALUES (?,?,?,?,?,?,?,?,?,?)', (dataset_id, project_id, 1, 'published', hashing.hash_json(cases), dump(splits), '任务协作引导数据集', dump({'errors': [], 'warnings': report.get('warnings') or [], 'stats': report.get('stats') or {}}), stamp, stamp))
                for case in cases:
                    conn.execute('INSERT INTO cases (dataset_id,case_id,query,context_json,expected_json,fixtures_json,split,group_id,tags_json,validation_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (dataset_id, case['case_id'], case['query'], dump(case.get('context') or {}), dump(case.get('expected') or {}), dump(case.get('fixtures') or []), case['split'], case.get('group_id') or '', dump(case.get('tags') or []), dump(case.get('validation') or {}), stamp))
                conn.execute('INSERT INTO onboarding_guides (guide_key,project_id,environment_draft_id) VALUES (?,?,?)', (KEY, project_id, env_id))
    except BaseException:
        if created_files:
            shutil.rmtree(target.parent, ignore_errors=True)
        raise
    return persistence.get_project(project_id)


def guide_for_project(project_id):
    row = db.get_connection().execute('SELECT environment_draft_id FROM onboarding_guides WHERE guide_key=? AND project_id=?', (KEY, project_id)).fetchone()
    if not row:
        return None
    guide = {'key': KEY, 'title': TITLE, 'environment_draft_id': row['environment_draft_id'], 'skill_count': 3, 'split_counts': {'optimize': 24, 'validation': 12, 'final_test': 6}}
    # Applied history only completes this guide while all current inputs match.
    completed = db.get_connection().execute('''
        SELECT r.id, d.applied_at FROM runs r
        JOIN decisions d ON d.run_id = r.id
        JOIN projects p ON p.id = r.project_id
        WHERE p.id = ? AND r.mode = 'validate' AND r.status = 'completed'
          AND r.decision = 'accept' AND d.decision = 'accept' AND d.applied_at IS NOT NULL
          AND p.current_skill_version_id = r.candidate_skill_version_id
          AND p.current_environment_id = r.environment_id AND p.current_dataset_id = r.dataset_id
        ORDER BY d.applied_at DESC LIMIT 1
    ''', (project_id,)).fetchone()
    if completed:
        guide['completed_run_id'] = completed['id']
        guide['completed_at'] = completed['applied_at']
    return guide
