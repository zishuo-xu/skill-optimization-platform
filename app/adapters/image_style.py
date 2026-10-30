"""Version-aware rules for the offline image demo, driven by its loaded contract.

This simulator never reads fixtures or expected answers. Only returned version
information selects a capability table; production execution still uses an LLM.
"""
import re
from typing import Any, Mapping


def style_arguments(contract: Mapping[str, Any], query: str, version: Any):
    if not isinstance(version, int) or isinstance(version, bool):
        return 'unknown', {}
    if version == contract.get('unsupported_version'):
        return 'unsupported', {}
    spec = (contract.get('versions') or {}).get(str(version))
    if not isinstance(spec, dict):
        return 'unknown', {}
    mappings = (contract.get('mappings') or {}).get('template') or {}
    explicit = next((value for phrase, value in sorted(mappings.items(), key=lambda p: -len(p[0]))
                     if phrase in query), None)
    if explicit is None and re.search(r'[\u4e00-\u9fff]{2,}风格', re.sub(r'质感风格|风格化|加个风格', '', query)):
        return 'unsupported', {}
    template = explicit if explicit is not None else (contract.get('defaults') or {}).get('template')
    if template not in spec.get('templates', []):
        return 'unsupported', {}
    args = {'template': template}
    used_default = explicit is None
    for field, label in (contract.get('parameter_labels') or {}).items():
        if label not in query:
            continue
        bounds = (spec.get('ranges') or {}).get(field)
        if not bounds:
            return 'unsupported', {}
        match = re.search(re.escape(label) + r'\s*(?:设为|设置为|调到|调成|增加|降低|为)?\s*([-+]?\d+(?:\.\d+)?)', query)
        if match:
            value = float(match.group(1))
        else:
            value = spec.get('defaults', {}).get(field)
            used_default = True
        if not isinstance(value, (int, float)):
            return 'unknown', {}
        value = max(bounds[0], min(bounds[1], value))
        args[field] = int(value) if float(value).is_integer() else value
    args['intent'] = 0 if used_default else 1
    return 'supported', args
