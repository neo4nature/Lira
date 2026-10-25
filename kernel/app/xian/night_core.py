from __future__ import annotations
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
FIELDS = ('summary', 'decisions', 'open_questions', 'contradictions', 'facts', 'proposals', 'trajectory', 'relations')
SCORED = frozenset(('facts', 'decisions', 'proposals', 'relations', 'trajectory'))
SCHEMA = 'xian.night-core.v2'
MAX_ITEMS_PER_FIELD = 5
MAX_TOTAL_ITEMS = 16
MAX_ITEM_WORDS = 30
MAX_ITEM_CHARS = 300
MAX_SUMMARY_WORDS = 80
THREAD = os.getenv('LIRA_MEMORY_THREAD', 'memory-main')
MAX_CONTEXT_CHARS = 1600
MODEL_CTX = 32768
INPUT_BYTES_BUDGET = 64000
OUTPUT_TOKENS = 4096
TZ = ZoneInfo('UTC')

class NightDeferred(RuntimeError):
    pass

def prompt_quote_text(value: str) -> str:
    opening = {'„', '«', '‚'}
    closing = {'”', '»', '‟', '’'}
    quoted = False
    converted = []
    for char in value:
        if char in {'"', '“', '‘'}:
            converted.append('»' if quoted else '«')
            quoted = not quoted
        elif char in opening:
            converted.append('«')
            quoted = True
        elif char in closing:
            converted.append('»')
            quoted = False
        else:
            converted.append(char)
    return ''.join(converted)
WORK_LINE_FORMAT = 'Odpowiedz wyłącznie wierszami tekstu. Słowo ALIAS w drugiej komórce zastąp aliasem źródła z danych (pole source_id wiadomości; kilka aliasów oddziel przecinkiem), słowo AUTOR imieniem nadawcy, który to zaproponował, a tekst stoi ZAWSZE na końcu wiersza. Wzory pokazują tylko układ komórek:\nsummary | | tekst streszczenia\ndecisions | ALIAS,ALIAS | 0.8 | tekst\nfacts | ALIAS | 0.7 | tekst\nproposals | ALIAS | 0.6 | AUTOR | tekst\ntrajectory | ALIAS | 0.5 | tekst\nrelations | ALIAS,ALIAS | 0.6 | tekst\nopen_questions | ALIAS | tekst\ncontradictions | ALIAS,ALIAS | tekst\nNie dopisuj innych komórek ani aliasu na końcu wiersza; w samym tekście nie używaj aliasów. Pole, na które źródła nie dają podstaw, pomiń: wzory nie każą wypełnić każdego pola. Sprzeczność to dwa niezgodne twierdzenia o tej samej sprawie, a nie dwa różne tematy ani późniejsza wersja wyniku. Zachowaj dokładny etap: co już działa lub zostało zrobione, a co dopiero ma nastąpić i pod jakim warunkem.'

def _aliased_input(messages: list[dict], previous: dict | None, start: datetime, *, closing: dict | None=None) -> tuple[str, dict[str, str]]:
    ids = list(dict.fromkeys((m['source_id'] for m in messages)))
    prior = (previous or {}).get('data', {})
    ids.extend(sorted({src for field in FIELDS[1:] for item in prior.get(field, []) for src in item['source_ids']} - set(ids)))
    aliases = {f'S{i}': ident for i, ident in enumerate(ids, 1)}
    reverse = {ident: alias for alias, ident in aliases.items()}
    day = [{'source_id': reverse[m['source_id']], 'time': _parse_time(m['created_at']).astimezone(TZ).strftime('%H:%M'), 'sender': m['sender'], 'text': prompt_quote_text(m['text'])} for m in messages]
    prior_data = None
    if previous:
        prior_data = {**prior, 'summary': prompt_quote_text(prior['summary'])}
        for field in FIELDS[1:]:
            prior_data[field] = [{**item, 'text': prompt_quote_text(item['text']), 'source_ids': [reverse[x] for x in item['source_ids']]} for item in prior[field]]
    prompt = json.dumps({'day': start.astimezone(TZ).date().isoformat(), 'previous_core_day': previous['window_start'][:10] if previous else None, 'previous_core': prior_data, 'messages': day, **(closing or {})}, ensure_ascii=False, separators=(',', ':'))
    return (prompt, aliases)

def _restore_source_ids(data: dict, aliases: dict[str, str]) -> dict:
    checked = _strict_data(data, set(aliases))
    restored = {**checked}
    for field in FIELDS[1:]:
        restored[field] = [{**item, 'source_ids': [aliases[x] for x in item['source_ids']]} for item in checked[field]]
    return restored

def previous_day_window(now: datetime | None=None) -> tuple[datetime, datetime]:
    moment = now or datetime.now(TZ)
    if moment.tzinfo is None:
        raise ValueError('night window needs a timezone-aware timestamp')
    local = moment.astimezone(TZ)
    end = datetime.combine(local.date(), time.min, tzinfo=TZ)
    start = datetime.combine(local.date() - timedelta(days=1), time.min, tzinfo=TZ)
    return (start, end)

def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed

def source_times(db_path: Path, ids) -> dict[str, str]:
    wanted = sorted(set(ids))
    found: dict[str, str] = {}
    if not wanted or not db_path.is_file():
        return found
    with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        for start_index in range(0, len(wanted), 400):
            chunk = wanted[start_index:start_index + 400]
            rows = conn.execute(f'SELECT id, created_at FROM capsules WHERE id IN ({','.join('?' * len(chunk))})', chunk).fetchall()
            for ident, created_at in rows:
                try:
                    found[ident] = _parse_time(created_at).astimezone(timezone.utc).isoformat()
                except (ValueError, TypeError):
                    continue
    return found

def load_day_messages(db_path: Path, start: datetime, end: datetime) -> list[dict]:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    low = (start_utc - timedelta(days=1)).date().isoformat()
    high = (end_utc + timedelta(days=1)).date().isoformat()
    with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, created_at, sender, payload_json FROM capsules\n               WHERE thread_id=? AND type='chat_message' AND recipient IN ('shared', 'duet')\n                 AND created_at >= ? AND created_at < ? ORDER BY rowid ASC", (THREAD, low, high)).fetchall()
    messages = []
    for row in rows:
        if row['sender'] in {'Model', 'System', 'System'}:
            continue
        ts = _parse_time(row['created_at'])
        if not start_utc <= ts < end_utc:
            continue
        try:
            payload = json.loads(row['payload_json'])
        except (ValueError, TypeError) as exc:
            raise ValueError(f'malformed night message {row['id']}') from exc
        if not isinstance(payload, dict):
            raise ValueError(f'non-object night message {row['id']}')
        if payload.get('kind') in {'ecosystem_status', 'system_status', 'status_report'}:
            continue
        text = payload.get('text') or payload.get('body')
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f'missing text in night message {row['id']}')
        messages.append({'source_id': row['id'], 'created_at': ts.isoformat(), 'sender': row['sender'], 'text': text})
    return messages

def _strict_data(value: object, allowed_ids: set[str], *, enforce_density: bool=True) -> dict:
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError('wrong core JSON keys')
    if not isinstance(value['summary'], str) or (enforce_density and len(value['summary'].split()) > MAX_SUMMARY_WORDS):
        raise ValueError('invalid summary')
    total = 0
    for field in FIELDS[1:]:
        entries = value[field]
        if not isinstance(entries, list):
            raise ValueError(f'{field} is not a list')
        total += len(entries)
        if enforce_density and (len(entries) > MAX_ITEMS_PER_FIELD or total > MAX_TOTAL_ITEMS):
            raise ValueError(f'night core exceeds item budget in {field}')
        for entry in entries:
            expected = {'text', 'source_ids', 'confidence'} if field in SCORED else {'text', 'source_ids'}
            if field == 'proposals':
                expected.add('author')
            if not isinstance(entry, dict) or set(entry) != expected:
                raise ValueError(f'invalid {field} entry')
            if field == 'proposals' and (not isinstance(entry['author'], str) or not entry['author'].strip()):
                raise ValueError('missing proposal author')
            if not isinstance(entry['text'], str) or not entry['text'].strip():
                raise ValueError(f'empty {field} entry')
            if enforce_density and (len(entry['text'].split()) > MAX_ITEM_WORDS or len(entry['text']) > MAX_ITEM_CHARS):
                raise ValueError(f'night core item too long in {field}')
            ids = entry['source_ids']
            if not isinstance(ids, list) or not ids or any((not isinstance(i, str) or i not in allowed_ids for i in ids)) or (len(ids) != len(set(ids))):
                raise ValueError(f'unknown/duplicate source_id in {field}')
            if field in SCORED:
                confidence = entry['confidence']
                if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or (not math.isfinite(confidence)) or (not 0 <= confidence <= 1):
                    raise ValueError('invalid confidence')
    if value['summary'].strip() and (not any((value[f] for f in FIELDS[1:]))):
        raise ValueError('summary without sourced items')
    return value

def _normalise_model_noise(value: object, aliases: dict[str, str] | None) -> object:
    if not isinstance(value, dict):
        return value
    removed_scores = 0
    removed_aliases = 0

    def remove_aux_score(item: dict) -> None:
        nonlocal removed_scores
        if 'confidence' not in item:
            return
        score = item['confidence']
        if isinstance(score, bool) or not isinstance(score, (int, float)) or (not math.isfinite(score)) or (not 0 <= score <= 1):
            raise ValueError('invalid auxiliary confidence')
        item.pop('confidence')
        removed_scores += 1
    if 'confidence' in value:
        remove_aux_score(value)
    for field in ('open_questions', 'contradictions'):
        entries = value.get(field)
        if isinstance(entries, list):
            for item in entries:
                if isinstance(item, dict):
                    remove_aux_score(item)
    if aliases is not None and isinstance(value.get('summary'), str):
        summary = value['summary']
        found = re.findall('\\bS[1-9][0-9]*\\b', summary)
        if any((alias not in aliases for alias in found)):
            raise ValueError('unknown source alias in summary')
        if found:
            ref = 'S[1-9][0-9]*(?:\\s*(?:,|i|oraz)\\s*S[1-9][0-9]*)*'
            summary = re.sub('\\s*[\\[(]\\s*' + ref + '\\s*[\\])]', '', summary)
            summary = re.sub('\\b' + ref + '\\b', '', summary)
            summary = re.sub('\\s+([,.;:])', '\\1', summary)
            summary = re.sub('([,;:]\\s*){2,}', ' ', summary)
            summary = re.sub('\\s{2,}', ' ', summary).strip(' ,;:. ')
            if not summary:
                raise ValueError('summary contains only source aliases')
            value['summary'] = summary
            removed_aliases = len(found)
    removed_item_aliases = 0
    if aliases is not None:
        marker = re.compile('[\\[(]\\s*(S[1-9][0-9]*)\\s*[\\])]')
        for field in FIELDS[1:]:
            entries = value.get(field)
            for item in entries if isinstance(entries, list) else []:
                if not isinstance(item, dict) or not isinstance(item.get('text'), str):
                    continue
                item_text = item['text']
                found = re.findall('\\bS[1-9][0-9]*\\b', item_text)
                if any((alias not in aliases for alias in found)):
                    raise ValueError(f'unknown source alias in {field} text')
                item_text, count = marker.subn(lambda match: '' if match.group(1) in aliases else match.group(0), item_text)
                if count:
                    item['text'] = re.sub('\\s+([,.;:])', '\\1', re.sub('\\s{2,}', ' ', item_text)).strip()
                    removed_item_aliases += count
    if removed_scores or removed_aliases or removed_item_aliases:
        print(f'[xian-night] ignored_aux_confidence={removed_scores}, removed_summary_aliases={removed_aliases}, removed_item_aliases={removed_item_aliases}')
    return value

def parse_response(raw: str, allowed_ids: set[str], *, done_reason: str | None=None, aliases: dict[str, str] | None=None) -> dict:
    if done_reason not in (None, 'stop'):
        raise ValueError(f'model response incomplete: {done_reason}')
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('model returned invalid/truncated JSON') from exc
    parsed = _normalise_model_noise(parsed, aliases)
    if aliases is not None:
        if set(aliases.values()) != allowed_ids:
            raise ValueError('alias registry does not cover allowed sources')
        if isinstance(parsed, dict):
            for field in FIELDS[1:]:
                entries = parsed.get(field)
                for item in entries if isinstance(entries, list) else []:
                    if isinstance(item, dict) and isinstance(item.get('text'), str) and re.search('\\bS[1-9][0-9]*\\b', item['text']):
                        raise ValueError(f'alias repeated in {field} text')
        return _strict_data(_restore_source_ids(parsed, aliases), allowed_ids)
    return _strict_data(parsed, allowed_ids)

def _line_text(value: str, *, auxiliary_confidence: bool=False) -> str:
    while '|' in value:
        head, _, tail = value.partition('|')
        if not head.strip():
            value = tail
            continue
        if auxiliary_confidence:
            try:
                confidence = float(head.strip())
            except ValueError:
                break
            if math.isfinite(confidence) and 0 <= confidence <= 1:
                value = tail
                auxiliary_confidence = False
                continue
        break
    return value.strip()

def parse_lines(raw: str, aliases: dict[str, str], *, done_reason: str | None, ignored_tail_path: Path | None=None) -> dict:
    if done_reason != 'stop':
        raise ValueError(f'model response incomplete: {done_reason}')
    data = {key: '' if key == 'summary' else [] for key in FIELDS}
    lines = raw.splitlines(keepends=True)
    cutoff = next((i for i, line in enumerate(lines) if line.lstrip().startswith(('```', '{'))), len(lines))
    if cutoff < len(lines):
        tail = ''.join(lines[cutoff:])
        if ignored_tail_path is not None:
            _atomic_text(ignored_tail_path, tail)
        print(f'[xian-night] ignored_work_tail_lines={len(lines) - cutoff}')
    rows = [line.strip() for line in lines[:cutoff] if line.strip()]
    if not rows:
        raise ValueError('empty night response')
    rejected = 0
    ignored_placeholders = 0
    inferred_proposal_authors = 0
    summary_seen = False
    for line in rows:
        field = line.split('|', 1)[0].strip()
        if field == 'summary':
            parts = line.split('|', 2)
            text = _line_text(line.split('|', 1)[1] if len(parts) > 1 else '', auxiliary_confidence=True)
            text = re.sub('\\s*\\|\\s*$', '', text)
            if len(parts) < 2 or summary_seen or (not text) or re.fullmatch('\\s*S[1-9][0-9]*(\\s*,\\s*S[1-9][0-9]*)*\\s*', parts[1]):
                raise ValueError('invalid or duplicate night summary')
            data['summary'] = text
            summary_seen = True
            continue
        if field not in FIELDS[1:]:
            rejected += 1
            continue
        cells = line.split('|')
        empty_section_text = {'open_questions': 'Brak otwartych pytań.', 'contradictions': 'Brak sprzeczności.'}
        if len(cells) >= 3 and field in empty_section_text and (cells[-1].strip() == empty_section_text[field]) and (not any((cell.strip() for cell in cells[1:-1]))):
            ignored_placeholders += 1
            continue
        parts = line.split('|', 4 if field == 'proposals' else 3 if field in SCORED else 2)
        expected = 5 if field == 'proposals' else 4 if field in SCORED else 3
        if len(parts) != expected and (not (field == 'proposals' and len(parts) == 4)):
            rejected += 1
            continue
        ids = [src.strip() for src in parts[1].split(',')]
        if not ids or any((src not in aliases for src in ids)) or len(ids) != len(set(ids)):
            rejected += 1
            continue
        text = _line_text(parts[-1], auxiliary_confidence=field not in SCORED)
        if not text:
            rejected += 1
            continue
        inferred_author = None
        author = parts[3].strip() if field == 'proposals' and len(parts) == 5 else ''
        if field == 'proposals' and (not author):
            match = re.match('^(Operator|Lira|Reviewer|Model|Participant)\\b', text)
            if match:
                inferred_author = match.group(1)
                author = inferred_author
            else:
                rejected += 1
                continue
        if any((alias not in aliases for alias in re.findall('\\bS[1-9][0-9]*\\b', text))):
            raise ValueError(f'unknown source alias in {field} text')
        if re.search('\\bS[1-9][0-9]*\\b', re.sub('[\\[(]\\s*S[1-9][0-9]*\\s*[\\])]', '', text)):
            rejected += 1
            continue
        item = {'text': text, 'source_ids': ids}
        if field in SCORED:
            try:
                item['confidence'] = float(parts[2].strip())
            except ValueError:
                rejected += 1
                continue
        if field == 'proposals':
            item['author'] = author
            if inferred_author is not None:
                inferred_proposal_authors += 1
        data[field].append(item)
    substantive_rows = len(rows) - ignored_placeholders
    if not summary_seen or rejected / substantive_rows > 0.2:
        raise ValueError(f'night lines missing summary or rejected {rejected}/{substantive_rows}')
    print(f'[xian-night] rejected_work_lines={rejected}/{substantive_rows} inferred_proposal_authors={inferred_proposal_authors} ignored_work_placeholders={ignored_placeholders}')
    _strict_data(data, set(aliases), enforce_density=False)
    dropped = []
    for field in FIELDS[1:]:
        kept = [item for item in data[field] if len(item['text'].split()) <= MAX_ITEM_WORDS and len(item['text']) <= MAX_ITEM_CHARS]
        dropped += [{'field': field, **item} for item in data[field] if item not in kept]
        data[field] = kept
    if dropped:
        print(f'[xian-night] dropped_long_work_items={len(dropped)} {json.dumps(dropped, ensure_ascii=False)}')
        if not any((data[field] for field in FIELDS[1:])):
            data['summary'] = ''
    trimmed = 0
    room = MAX_TOTAL_ITEMS
    for field in FIELDS[1:]:
        keep = min(len(data[field]), MAX_ITEMS_PER_FIELD, room)
        trimmed += len(data[field]) - keep
        data[field] = data[field][:keep]
        room -= keep
    if trimmed:
        print(f'[xian-night] trimmed_work_items={trimmed}')
    return parse_response(json.dumps(data, ensure_ascii=False), set(aliases.values()), done_reason='stop', aliases=aliases)

def read_core(path: Path) -> dict | None:
    if not path.exists():
        return None
    core = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(core, dict) or core.get('schema') not in (SCHEMA, 'xian.night-core.v1'):
        raise ValueError('existing Model core has unknown schema')
    if core['schema'] == 'xian.night-core.v1':
        old = core.get('data')
        if not isinstance(old, dict) or set(old) != set(FIELDS) - {'proposals'}:
            raise ValueError('existing Model core has invalid v1 data')
        core = {**core, 'schema': SCHEMA, 'data': {**old, 'proposals': []}}
    ids = core.get('allowed_source_ids')
    if not isinstance(ids, list) or any((not isinstance(i, str) for i in ids)):
        raise ValueError('existing Model core has invalid source registry')
    _strict_data(core.get('data'), set(ids), enforce_density=False)
    if not all((isinstance(core.get(k), str) for k in ('core_id', 'window_start', 'window_end'))):
        raise ValueError('existing Model core lacks provenance')
    return core

def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

@contextmanager
def _writer_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

def short_view(core: dict) -> str:
    lines = [f'Model Night, dzień do {core['window_end']} (rdzeń {core['core_id'][:10]}):', core['data']['summary'].strip()]
    for field in ('decisions', 'proposals', 'facts', 'contradictions', 'open_questions'):
        for item in core['data'][field]:
            author = f' ({item['author']})' if field == 'proposals' else ''
            line = f'{field}{author}: {item['text']}'
            marker = 'Pozostałe pozycje: xian_night_core.json'
            if len('\n'.join(lines + [line, marker])) > MAX_CONTEXT_CHARS:
                lines.append(marker)
                result = '\n'.join(lines)
                if len(result) > MAX_CONTEXT_CHARS:
                    raise ValueError('night core summary too large for Hermes')
                return result
            lines.append(line)
    lines.append('Źródła każdej pozycji: xian_night_core.json')
    result = '\n'.join(lines)
    if len(result) > MAX_CONTEXT_CHARS:
        raise ValueError('night core summary too large for Hermes')
    return result

def write_core(path: Path, value: dict) -> None:
    previous = read_core(path)
    if previous and previous['window_end'] >= value['window_end']:
        raise ValueError('refusing to replace the same/newer Model core')
    display = short_view(value)
    if previous:
        _atomic_text(path.with_name('xian_night_core.previous.json'), json.dumps(previous, ensure_ascii=False, indent=2) + '\n')
    _atomic_text(path.with_suffix('.txt'), display + '\n')
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def consolidate(db_path: Path, core_path: Path, start: datetime, end: datetime, request_fn, *, model: str='xian-night', input_bytes_budget: int=INPUT_BYTES_BUDGET, model_ctx: int=MODEL_CTX) -> dict | None:
    with _writer_lock(core_path.with_suffix('.lock')):
        previous = read_core(core_path)
        if previous and previous['window_end'] == end.isoformat():
            return previous
        if previous and previous['window_end'] > end.isoformat():
            raise ValueError('newer core exists; refusing an old night')
        messages = load_day_messages(db_path, start, end)
        if not messages:
            return None
        prompt, aliases = _aliased_input(messages, previous, start)
        allowed = set(aliases.values())
        input_bytes = len(prompt.encode('utf-8'))
        print(f'[xian-night] sources={len(messages)}, input_chars={len(prompt)}, input_bytes={input_bytes}, max_input_bytes={input_bytes_budget}, estimated_tokens={math.ceil(input_bytes / 2.5)}')
        if input_bytes_budget <= 0 or input_bytes > input_bytes_budget:
            deferred = {'window_start': start.isoformat(), 'window_end': end.isoformat(), 'reason': 'input_exceeds_budget', 'sources': len(messages), 'input_chars': len(prompt), 'input_bytes': input_bytes, 'max_input_bytes': input_bytes_budget, 'estimated_tokens': math.ceil(input_bytes / 2.5), 'model_ctx': model_ctx, 'output_reserve': OUTPUT_TOKENS}
            _atomic_text(core_path.with_name('xian_night_deferred.json'), json.dumps(deferred, ensure_ascii=False, indent=2) + '\n')
            raise NightDeferred(f'input exceeds budget: {input_bytes} > {input_bytes_budget} bytes; {len(messages)} sources')
        raw, reason, prompt_eval_count = request_fn(model, prompt)
        if isinstance(prompt_eval_count, bool) or not isinstance(prompt_eval_count, int) or prompt_eval_count < 0 or (prompt_eval_count >= model_ctx - OUTPUT_TOKENS):
            raise ValueError(f'unsafe prompt_eval_count: {prompt_eval_count!r} / {model_ctx - OUTPUT_TOKENS} input cap')
        data = parse_response(raw, allowed, done_reason=reason, aliases=aliases)
        core_id = hashlib.sha256((end.isoformat() + '\n' + raw).encode('utf-8')).hexdigest()
        core = {'schema': SCHEMA, 'core_id': core_id, 'window_start': start.isoformat(), 'window_end': end.isoformat(), 'generated_at': datetime.now(timezone.utc).isoformat(), 'model': model, 'allowed_source_ids': sorted(allowed), 'data': data}
        write_core(core_path, core)
        return core
RETRY_SEED_BASE = 42
