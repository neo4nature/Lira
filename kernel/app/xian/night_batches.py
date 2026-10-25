from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from xian import night_core as nc, social_core as sc
from xian import decision_registry as dr
WORK_RE = re.compile('\\bPA[-‑ ]?\\d{2,3}\\b|\\b(?:commit|commity?|commicie|pytest|test\\w*|plik\\w*|regresj\\w*|bramk\\w*|API|Flask|repo\\w*|gałęz\\w*|kursor\\w*|inbox|modelfile|wdroż\\w*|deploy\\w*|embedding\\w*|indeks\\w*)\\b|(?:[\\w./-]+\\.(?:py|md|json|sh|kt)\\b)', re.IGNORECASE)
SOCIAL_MODEL = 'xian-pogaduchy'
WORK_MODEL = 'xian-night'

def window_for_day(day: date) -> tuple[datetime, datetime]:
    return (datetime.combine(day, time.min, tzinfo=nc.TZ), datetime.combine(day + timedelta(days=1), time.min, tzinfo=nc.TZ))

def initial_packets(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    if len({m['source_id'] for m in messages}) != len(messages):
        raise ValueError('duplicate capsule ID in night input')
    social, work = ([], [])
    for message in messages:
        (work if WORK_RE.search(message['text']) else social).append(message)
    return (social, work)

def move_originals(social: list[dict], work: list[dict], transferred: list[str], all_messages: list[dict]) -> tuple[list[dict], list[dict]]:
    requested = set(transferred)
    if len(requested) != len(transferred) or not requested <= {m['source_id'] for m in social}:
        raise ValueError('transfer references a duplicate or non-social capsule')
    social_after = [m for m in social if m['source_id'] not in requested]
    work_ids = {m['source_id'] for m in work} | requested
    work_after = [m for m in all_messages if m['source_id'] in work_ids]
    if len(social_after) + len(work_after) != len(all_messages) or {m['source_id'] for m in social_after} & {m['source_id'] for m in work_after}:
        raise AssertionError('night partition lost or duplicated sources')
    return (social_after, work_after)

def _remove_transferred_social_evidence(core: dict, moved_ids: list[str]) -> int:
    moved = set(moved_ids)
    if not moved:
        return 0
    removed = 0
    changed = False
    for field in sc.LISTS:
        kept = []
        for item in core['data'][field]:
            ids = [ident for ident in item['source_ids'] if ident not in moved]
            if len(ids) != len(item['source_ids']):
                changed = True
            if ids:
                kept.append({**item, 'source_ids': ids})
            else:
                removed += 1
        core['data'][field] = kept
    if changed:
        statements = [item['text'] for field in sc.LISTS for item in core['data'][field]]
        words = ' '.join(statements).split()
        core['data']['summary'] = ' '.join(words[:nc.MAX_SUMMARY_WORDS])[:600].rstrip()
    print(f'[xian-night] moved_social_items_removed={removed}')
    return removed

def longest_gap_split(messages: list[dict], timeline: list[dict] | None=None) -> tuple[list[dict], list[dict]]:
    if len(messages) < 2:
        raise nc.NightDeferred('cannot split a single oversized message')
    timeline = timeline if timeline is not None else messages
    positions = {m['source_id']: i for i, m in enumerate(timeline)}
    if len(positions) != len(timeline) or any((m['source_id'] not in positions for m in messages)):
        raise ValueError('invalid timeline for conversation split')
    midpoint = len(messages) / 2
    minimum = math.ceil(len(messages) / 4)
    candidates = []
    for i in range(1, len(timeline)):
        cut = sum((positions[m['source_id']] < i for m in messages))
        if cut < minimum or len(messages) - cut < minimum:
            continue
        before = nc._parse_time(timeline[i - 1]['created_at'])
        after = nc._parse_time(timeline[i]['created_at'])
        candidates.append(((after - before).total_seconds(), -abs(cut - midpoint), cut))
    if not candidates:
        raise ValueError('no safe conversation boundary for split')
    cut = max(candidates)[2]
    return (messages[:cut], messages[cut:])

def _defer(state_dir: Path, day: date, bucket: str, part: list[dict], input_bytes: int, reason: str) -> None:
    report = {'day': day.isoformat(), 'bucket': bucket, 'reason': reason, 'sources': len(part), 'input_bytes': input_bytes, 'limit_bytes': nc.INPUT_BYTES_BUDGET, 'first_source_id': part[0]['source_id'] if part else None, 'last_source_id': part[-1]['source_id'] if part else None}
    nc._atomic_text(state_dir / 'xian_night_deferred.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')

def _save_rejected_raw(state_dir: Path, day: date, bucket: str, part: list[dict], raw: str, reason: str, token_count: object) -> None:
    name = f'xian_night_rejected_{bucket}'
    nc._atomic_text(state_dir / f'{name}.txt', raw)
    info = {'day': day.isoformat(), 'bucket': bucket, 'reason': reason, 'prompt_eval_count': token_count, 'first_source_id': part[0]['source_id'], 'last_source_id': part[-1]['source_id'], 'sources': len(part)}
    nc._atomic_text(state_dir / f'{name}.json', json.dumps(info, ensure_ascii=False, indent=2) + '\n')
    print(f'[xian-night] rejected {bucket} output saved to {name}.txt')

def _raw_stem(day: date, bucket: str, part: list[dict]) -> str:
    return f'xian_night_raw_{day.isoformat()}_{bucket}_{part[0]['source_id'][:12]}'

def _retry_seed(state_dir: Path, day: date, bucket: str, part: list[dict], prompt: str) -> int | None:
    sha = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    asked = []
    for path in state_dir.glob(f'{_raw_stem(day, bucket, part)}_p*.json'):
        number = path.stem.rsplit('_p', 1)[1]
        try:
            info = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if number.isdigit() and isinstance(info, dict) and (info.get('prompt_sha256') == sha):
            asked.append((int(number), info))
    if not asked:
        return None
    latest = {}
    for number, info in sorted(asked, key=lambda item: item[0]):
        seed = info.get('seed')
        if seed is None or type(seed) is int:
            latest[seed] = (number, info.get('accepted') is True)
    accepted = [(number, seed) for seed, (number, ok) in latest.items() if ok]
    if accepted:
        return max(accepted, key=lambda item: item[0])[1]
    return max([nc.RETRY_SEED_BASE + len(asked)] + [seed + 1 for seed in latest if seed is not None])

def _mark_raw(meta_path: Path, accepted: bool) -> None:
    info = json.loads(meta_path.read_text(encoding='utf-8'))
    info['accepted'] = accepted
    nc._atomic_text(meta_path, json.dumps(info, ensure_ascii=False, indent=2) + '\n')

def _save_raw(state_dir: Path, day: date, bucket: str, part: list[dict], raw: str, aliases: dict, reason: object, token_count: object, prompt: str | None=None, seed: int | None=None) -> Path:
    stem = _raw_stem(day, bucket, part)
    attempt = 1
    while any(((state_dir / f'{stem}_p{attempt}{ext}').exists() for ext in ('.txt', '.json'))):
        attempt += 1
    name = f'{stem}_p{attempt}'
    nc._atomic_text(state_dir / f'{name}.txt', raw)
    info = {'day': day.isoformat(), 'bucket': bucket, 'done_reason': reason, 'prompt_eval_count': token_count, 'sources': len(part), 'first_source_id': part[0]['source_id'], 'last_source_id': part[-1]['source_id'], 'aliases': aliases}
    if prompt is not None:
        encoded = prompt.encode('utf-8')
        info['prompt_sha256'] = hashlib.sha256(encoded).hexdigest()
        info['prompt_bytes'] = len(encoded)
    if seed is not None:
        info['seed'] = seed
    nc._atomic_text(state_dir / f'{name}.json', json.dumps(info, ensure_ascii=False, indent=2) + '\n')
    return state_dir / f'{name}.json'

def _identity(core: dict, bucket: str) -> str:
    value = {'bucket': bucket, 'window_end': core['window_end'], 'day_message_ids': core['day_message_ids'], 'data': core['data']}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def _work_prompt(messages: list[dict], previous: dict | None, start: datetime):
    prompt, aliases = nc._aliased_input(messages, previous, start, closing={'format_odpowiedzi': nc.WORK_LINE_FORMAT})
    return (prompt, aliases, set(aliases))

def _social_prompt(messages: list[dict], previous: dict | None, start: datetime, carry=None):
    return sc.build_prompt(messages, previous, start, extra_sources=carry.sources() if carry else ())

def _run_segments(messages: list[dict], previous: dict | None, start: datetime, end: datetime, state_dir: Path, bucket: str, model: str, request_fn, timeline: list[dict] | None=None, source_times: dict[str, str] | None=None) -> tuple[dict, list[str]]:
    queued = [messages[:]] if messages else []
    segments = []
    transfers = []
    current = previous
    carry = None
    if bucket == 'social':
        carry = sc.UserCarry((previous or {}).get('data', {}).get('neo_self', []), {**(source_times or {}), **{m['source_id']: nc._parse_time(m['created_at']).astimezone(timezone.utc).isoformat() for m in messages}})
    while queued:
        part = queued.pop(0)
        if bucket == 'social':
            prompt, aliases, today = _social_prompt(part, current, start, carry)
        else:
            prompt, aliases, today = _work_prompt(part, current, start)
        size = len(prompt.encode('utf-8'))
        if size > nc.INPUT_BYTES_BUDGET:
            if len(part) == 1:
                _defer(state_dir, start.date(), bucket, part, size, 'single_message_exceeds_budget')
                raise nc.NightDeferred(f'{bucket} single message: {size} bytes')
            left, right = longest_gap_split(part, timeline)
            print(f'[xian-night] split {bucket}: {len(left)}+{len(right)} sources, gap={left[-1]['created_at']}..{right[0]['created_at']}')
            queued[:0] = [left, right]
            continue
        print(f'[xian-night] {bucket} sources={len(part)}, bytes={size}, estimated_tokens={math.ceil(size / 2.5)}')
        seed = _retry_seed(state_dir, start.date(), bucket, part, prompt)
        if seed is None:
            raw, reason, token_count = request_fn(model, prompt)
        else:
            print(f'[xian-night] {bucket} retry_seed={seed}')
            raw, reason, token_count = request_fn(model, prompt, seed=seed)
        raw_meta = _save_raw(state_dir, start.date(), bucket, part, raw, aliases, reason, token_count, prompt, seed)
        if reason != 'stop':
            _mark_raw(raw_meta, False)
            _save_rejected_raw(state_dir, start.date(), bucket, part, raw, f'model_incomplete:{reason}', token_count)
            _defer(state_dir, start.date(), bucket, part, size, f'model_incomplete:{reason}')
            raise nc.NightDeferred(f'{bucket} model incomplete: {reason}')
        if isinstance(token_count, bool) or not isinstance(token_count, int) or token_count < 0 or (token_count >= nc.MODEL_CTX - nc.OUTPUT_TOKENS):
            _mark_raw(raw_meta, False)
            _save_rejected_raw(state_dir, start.date(), bucket, part, raw, 'unsafe_prompt_eval_count', token_count)
            _defer(state_dir, start.date(), bucket, part, size, 'unsafe_prompt_eval_count')
            raise nc.NightDeferred(f'{bucket} prompt_eval_count unsafe: {token_count!r}')
        try:
            if bucket == 'social':
                inherited_neo = {src for item in (current or {}).get('data', {}).get('neo_self', []) for src in item['source_ids']}
                current_neo = {m['source_id'] for m in part if m['sender'] == 'Operator'}
                neo_aliases = {a for a, ident in aliases.items() if ident in inherited_neo | current_neo}
                data = sc.parse_lines(raw, aliases, today, neo_ids=neo_aliases, done_reason=reason, neo_quotes=sc.neo_quote_sources(part, current, aliases), carry=carry)
                transfers.extend(data['transfer_ids'])
                core = sc.build_core(data, aliases, raw, start, end, model, [m['source_id'] for m in messages], [])
            else:
                tail_key = hashlib.sha256(part[0]['source_id'].encode()).hexdigest()[:12]
                data = nc.parse_lines(raw, aliases, done_reason=reason, ignored_tail_path=state_dir / f'xian_night_ignored_work_tail_{tail_key}.txt')
                core = {'schema': nc.SCHEMA, 'core_id': hashlib.sha256((end.isoformat() + '\n' + raw).encode()).hexdigest(), 'window_start': start.isoformat(), 'window_end': end.isoformat(), 'generated_at': datetime.now(timezone.utc).isoformat(), 'model': model, 'allowed_source_ids': sorted(set(aliases.values())), 'data': data}
        except ValueError as exc:
            _mark_raw(raw_meta, False)
            _save_rejected_raw(state_dir, start.date(), bucket, part, raw, f'validation:{exc}', token_count)
            raise
        _mark_raw(raw_meta, True)
        segments.append({'first_id': part[0]['source_id'], 'last_id': part[-1]['source_id'], 'first_at': part[0]['created_at'], 'last_at': part[-1]['created_at'], 'source_ids': [m['source_id'] for m in part], 'sources': len(part), 'input_bytes': size, 'prompt_eval_count': token_count})
        current = core
    if not segments:
        if bucket == 'social':
            data = {key: '' if key == 'summary' else [] for key in sc.FIELDS}
            current = sc.build_core(data, {}, 'empty', start, end, model, [], [])
        else:
            data = {key: '' if key == 'summary' else [] for key in nc.FIELDS}
            current = {'schema': nc.SCHEMA, 'core_id': hashlib.sha256((end.isoformat() + '\nempty work').encode()).hexdigest(), 'window_start': start.isoformat(), 'window_end': end.isoformat(), 'generated_at': datetime.now(timezone.utc).isoformat(), 'model': model, 'allowed_source_ids': [], 'data': data}
    current['segments'] = segments
    current['day_message_ids'] = [m['source_id'] for m in messages]
    if bucket == 'social':
        current['data']['transfer_ids'] = list(dict.fromkeys(transfers))
        current['allowed_source_ids'] = sorted(set(current['allowed_source_ids']) | set(current['day_message_ids']))
        sc.validate_data(current['data'], set(current['allowed_source_ids']), enforce_density=False)
    else:
        current['allowed_source_ids'] = sorted(set(current['allowed_source_ids']) | set(current['day_message_ids']))
        nc._strict_data(current['data'], set(current['allowed_source_ids']))
    current['core_id'] = _identity(current, bucket)
    print(f'[xian-night] {bucket} segments={len(segments)}, transferred={len(transfers)}')
    return (current, list(dict.fromkeys(transfers)))

def run_day(day: date, db_path: Path, state_dir: Path, request_fn, send_fn=None) -> dict:
    if send_fn is not None:
        raise ValueError('Delivery adapters are not included in the memory package')
    start, end = window_for_day(day)
    social_path = state_dir / 'xian_social_core.json'
    work_path = state_dir / 'xian_night_core.json'
    with nc._writer_lock(state_dir / 'xian_night_pair.run.lock'):
        work = nc.read_core(work_path)
        registry = dr.registry_from_env(work)
        messages = nc.load_day_messages(db_path, start, end)
        if not messages:
            return {'messages': 0, 'status': 'empty'}
        social_msgs, work_msgs = initial_packets(messages)
        social = sc.read_core(social_path)
        if social and social['window_end'] > end.isoformat() or (work and work['window_end'] > end.isoformat()):
            raise ValueError('newer core exists; refusing historical day')
        partial_pair = social and (not work) and (social['window_end'] != end.isoformat()) or (social and work and (social['window_end'] != work['window_end']) and (social['window_end'] != end.isoformat()))
        old_ends = [nc._parse_time(core['window_end']) for core in (social, work) if core and nc._parse_time(core['window_end']) < start.astimezone(timezone.utc)]
        missing_chat = bool(old_ends and nc.load_day_messages(db_path, min(old_ends), start))
        if partial_pair or missing_chat:
            _defer(state_dir, day, 'pair', messages, 0, 'unfinished_previous_day')
            raise nc.NightDeferred('previous Model day unfinished; backfill before advancing')
        if old_ends:
            print(f'[xian-night] no chat between {min(old_ends).isoformat()} and {start.isoformat()}; carrying prior cores')
        social_done = bool(social and social['window_end'] == end.isoformat())
        work_done = bool(work and work['window_end'] == end.isoformat())
        if registry is not None and work_done and (work.get('memory_mode') != 'daily+registry'):
            raise ValueError('existing work note uses legacy mode; rehearse migration in a copy')
        if work_done and (not social_done):
            raise ValueError('work core exists without corresponding social core')
        if not social_done:
            carried_ids = {src for item in (social or {}).get('data', {}).get('neo_self', []) for src in item['source_ids']}
            social, transferred = _run_segments(social_msgs, social, start, end, state_dir, 'social', os.getenv('LIRA_MEMORY_SOCIAL_MODEL', SOCIAL_MODEL), request_fn, messages, source_times=nc.source_times(db_path, carried_ids))
        else:
            transferred = social['data']['transfer_ids']
        social_after, work_after = move_originals(social_msgs, work_msgs, transferred, messages)
        if not social_done:
            _remove_transferred_social_evidence(social, transferred)
            social['classified_input_ids'] = social['day_message_ids']
            social['day_message_ids'] = [m['source_id'] for m in social_after]
            sc.validate_data(social['data'], set(social['allowed_source_ids']))
            social['core_id'] = _identity(social, 'social')
            sc.write_core(social_path, social)
        print(f'[xian-night] initial_social={len(social_msgs)}, initial_work={len(work_msgs)}, transferred={len(transferred)}, final_social={len(social_after)}, final_work={len(work_after)}')
        if not work_done:
            previous_work = None if registry is not None else work
            work, _ = _run_segments(work_after, previous_work, start, end, state_dir, 'work', os.getenv('LIRA_MEMORY_WORK_MODEL', WORK_MODEL), request_fn, messages)
            if registry is not None:
                work['memory_mode'] = 'daily+registry'
                work['decision_registry_revision'] = registry['revision']
            nc.write_core(work_path, work)
        report = {'messages': len(messages), 'social': len(social_after), 'work': len(work_after), 'transferred': len(transferred), 'social_segments': len(social['segments']), 'work_segments': len(work['segments']), 'social_core_id': social['core_id'], 'work_core_id': work['core_id']}
        report['capsules'] = 0
        return report

def next_pending_day(db_path: Path, state_dir: Path, yesterday: date) -> date:
    social = sc.read_core(state_dir / 'xian_social_core.json')
    work = nc.read_core(state_dir / 'xian_night_core.json')
    if social is None and work is None:
        return yesterday
    if work is not None and social is None:
        raise nc.NightDeferred('work core exists without social core; repair the pair first')
    if work is None:
        candidate = nc._parse_time(social['window_start']).astimezone(nc.TZ).date()
    else:
        candidate = min((nc._parse_time(core['window_end']).astimezone(nc.TZ).date() for core in (social, work)))
    while candidate < yesterday:
        start, end = window_for_day(candidate)
        if nc.load_day_messages(db_path, start, end):
            return candidate
        candidate += timedelta(days=1)
    return yesterday
