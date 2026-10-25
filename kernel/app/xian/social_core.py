from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from xian import night_core as nc
SCHEMA = 'xian.social-core.v1'
FIELDS = ('summary', 'topics', 'neo_self', 'mood', 'recurring_jokes', 'personal_open_threads', 'transfer_ids')
LISTS = FIELDS[1:-1]
MODEL_FIELDS = frozenset(('neo_self', 'personal_open_threads', 'transfer_ids'))
MAX_TRANSFER_IDS = 3
_ALIAS_LIST = re.compile('S[1-9][0-9]*(?:(?:\\s*,\\s*|\\s+)S[1-9][0-9]*)*,?')
MAX_CONTEXT_CHARS = 1200
FIELD_LIMITS = {'topics': 5, 'neo_self': 8, 'mood': 3, 'recurring_jokes': 5, 'personal_open_threads': 5}
MAX_TOTAL_ITEMS = 20
PRIORITY = ('neo_self', 'personal_open_threads', 'topics', 'recurring_jokes', 'mood')
OLD_NEO_RESERVED = 4
_BARE_ALIAS = re.compile('\\|?\\s*(S[1-9][0-9]*)\\s*\\|?')

def _norm(text: str) -> str:
    return ' '.join(text.split())

def _item_key(item: dict) -> tuple:
    return (_norm(item['text']), tuple(sorted(item['source_ids'])))

class UserCarry:

    def __init__(self, old: list[dict] | None, times: dict[str, str] | None=None):
        self.old = [{'text': i['text'], 'source_ids': list(i['source_ids'])} for i in old or []]
        self.new: list[dict] = []
        self.votes: set[tuple] = set()
        self.times = dict(times or {})

    def _time(self, item: dict) -> str:
        return max((self.times.get(src, '') for src in item['source_ids']), default='')

    def known(self) -> list[dict]:
        return self.old + self.new

    def sources(self) -> list[str]:
        return list(dict.fromkeys((src for item in self.known() for src in item['source_ids'])))

    def add_new(self, item: dict) -> None:
        known = {_item_key(i) for i in self.known()}
        if _item_key(item) not in known:
            self.new.append({'text': item['text'], 'source_ids': list(item['source_ids'])})

    def select(self) -> tuple[list[dict], list[dict]]:
        limit = FIELD_LIMITS['neo_self']
        new_take = min(len(self.new), limit - min(OLD_NEO_RESERVED, len(self.old)))
        old_take = min(len(self.old), limit - new_take)
        old_ranked = sorted(self.old, key=lambda i: (_item_key(i) in self.votes, self._time(i)), reverse=True)
        new_ranked = sorted(self.new, key=self._time, reverse=True)
        keep_old = {_item_key(i) for i in old_ranked[:old_take]}
        keep_new = {_item_key(i) for i in new_ranked[:new_take]}
        kept = [i for i in self.old if _item_key(i) in keep_old] + sorted((i for i in self.new if _item_key(i) in keep_new), key=self._time)
        dropped = [i for i in self.old if _item_key(i) not in keep_old] + [i for i in self.new if _item_key(i) not in keep_new]
        return (kept, dropped)

def build_prompt(messages: list[dict], previous: dict | None, start: datetime, extra_sources: list[str] | tuple[str, ...]=()) -> tuple[str, dict[str, str], set[str]]:
    ids = list(dict.fromkeys((m['source_id'] for m in messages)))
    prior = (previous or {}).get('data', {})
    ids.extend(sorted({src for field in ('neo_self', 'personal_open_threads') for item in prior.get(field, []) for src in item['source_ids']} - set(ids)))
    ids.extend((src for src in dict.fromkeys(extra_sources) if src not in set(ids)))
    aliases = {f'S{i}': ident for i, ident in enumerate(ids, 1)}
    reverse = {ident: alias for alias, ident in aliases.items()}
    today = {reverse[m['source_id']] for m in messages}
    previous_data = None
    if previous:
        previous_data = {'summary': nc.prompt_quote_text(prior['summary'])}
        for field in ('neo_self', 'personal_open_threads'):
            previous_data[field] = [{**item, 'text': nc.prompt_quote_text(item['text']), 'source_ids': [reverse[src] for src in item['source_ids']]} for item in prior[field]]
    day = [{'source_id': reverse[m['source_id']], 'time': nc._parse_time(m['created_at']).astimezone(nc.TZ).strftime('%H:%M'), 'sender': m['sender'], 'text': nc.prompt_quote_text(m['text'])} for m in messages]
    prompt = json.dumps({'day': start.astimezone(nc.TZ).date().isoformat(), 'previous_core': previous_data, 'messages': day}, ensure_ascii=False, separators=(',', ':'))
    return (prompt, aliases, today)

def validate_data(value: object, allowed: set[str], *, today: set[str] | None=None, neo_ids: set[str] | None=None, enforce_density: bool=True) -> dict:
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError('wrong social core JSON keys')
    summary = value['summary']
    if not isinstance(summary, str) or (enforce_density and (len(summary.split()) > nc.MAX_SUMMARY_WORDS or len(summary) > 600)):
        raise ValueError('invalid social summary')
    total = 0
    for field in LISTS:
        entries = value[field]
        if not isinstance(entries, list):
            raise ValueError(f'{field} is not a list')
        total += len(entries)
        if enforce_density and (len(entries) > FIELD_LIMITS[field] or total > MAX_TOTAL_ITEMS):
            raise ValueError('social item budget exceeded')
        for item in entries:
            if not isinstance(item, dict) or set(item) != {'text', 'source_ids'}:
                raise ValueError(f'bad {field} item')
            text, ids = (item['text'], item['source_ids'])
            if not isinstance(text, str) or not text.strip() or (enforce_density and (len(text.split()) > nc.MAX_ITEM_WORDS or len(text) > nc.MAX_ITEM_CHARS)):
                raise ValueError(f'bad {field} text')
            if not isinstance(ids, list) or not ids or any((not isinstance(i, str) or i not in allowed for i in ids)) or (len(ids) != len(set(ids))):
                raise ValueError(f'bad {field} source_ids')
            if field == 'neo_self' and neo_ids is not None and (not set(ids) & neo_ids):
                raise ValueError("neo_self without Operator's own words")
    transfers = value['transfer_ids']
    if not isinstance(transfers, list) or len(transfers) != len(set(map(str, transfers))) or any((not isinstance(i, str) or i not in allowed or (today is not None and i not in today) for i in transfers)):
        raise ValueError('invalid transfer_ids')
    if summary.strip() and (not any((value[field] for field in LISTS))):
        raise ValueError('social summary without sourced items')
    return value

def parse_response(raw: str, aliases: dict[str, str], today: set[str], *, neo_ids: set[str], done_reason: str | None) -> dict:
    if done_reason != 'stop':
        raise ValueError(f'social model response incomplete: {done_reason}')
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid/truncated social JSON') from exc
    value = validate_data(value, set(aliases), today=today, neo_ids=neo_ids)
    for field in LISTS:
        for item in value[field]:
            if re.search('\\bS[1-9][0-9]*\\b', item['text']):
                raise ValueError('social alias repeated in text')
    if re.search('\\bS[1-9][0-9]*\\b', value['summary']):
        raise ValueError('social alias in summary')
    restored = {**value}
    for field in LISTS:
        restored[field] = [{**item, 'source_ids': [aliases[x] for x in item['source_ids']]} for item in value[field]]
    restored['transfer_ids'] = [aliases[x] for x in value['transfer_ids']]
    return restored

def _select_diverse(items: list[dict], limit: int) -> list[dict]:
    if len(items) <= limit:
        return items
    selected: list[int] = []
    covered: set[str] = set()
    used_words: set[str] = set()
    while len(selected) < limit:

        def score(index: int):
            item = items[index]
            sources = set(item['source_ids'])
            words = set(re.findall('\\w+', item['text'].casefold()))
            return (min(len(sources - covered), 3), min(len(words - used_words), 15), min((abs(index - other) for other in selected), default=len(items)), -index)
        index = max((i for i in range(len(items)) if i not in selected), key=score)
        selected.append(index)
        covered.update(items[index]['source_ids'])
        used_words.update(re.findall('\\w+', items[index]['text'].casefold()))
    return [items[i] for i in sorted(selected)]

def _bound_model_items(data: dict) -> None:
    removed: dict[str, int] = {}
    dropped: list[dict] = []
    for field in LISTS:
        old = data[field]
        data[field] = [item for item in old if len(item['text'].split()) <= nc.MAX_ITEM_WORDS and len(item['text']) <= nc.MAX_ITEM_CHARS]
        dropped += [{'field': field, **item} for item in old if item not in data[field]]
        too_long = len(old) - len(data[field])
        old = data[field]
        data[field] = _select_diverse(old, FIELD_LIMITS[field])
        removed[field] = too_long + len(old) - len(data[field])
    total = sum((len(data[field]) for field in LISTS))
    if total > MAX_TOTAL_ITEMS:
        reserved = {field: 1 if data[field] else 0 for field in LISTS}
        remaining = MAX_TOTAL_ITEMS - sum(reserved.values())
        for field in PRIORITY:
            extra = min(remaining, len(data[field]) - reserved[field])
            reserved[field] += extra
            remaining -= extra
        for field in LISTS:
            old = data[field]
            data[field] = _select_diverse(old, reserved[field])
            removed[field] += len(old) - len(data[field])
    if dropped:
        print(f'[xian-night] dropped_long_social_items={json.dumps(dropped, ensure_ascii=False)}')
        if not any((data[field] for field in LISTS)):
            data['summary'] = ''
    if any(removed.values()):
        print(f'[xian-night] trimmed_social_items={json.dumps(removed, sort_keys=True)}')

def neo_quote_sources(messages: list[dict], previous: dict | None, aliases: dict[str, str]) -> dict[str, list[str]]:
    reverse = {ident: alias for alias, ident in aliases.items()}
    quotes: dict[str, list[str]] = {}
    for message in messages:
        if message['sender'] == 'Operator':
            quotes.setdefault(reverse[message['source_id']], []).append(message['text'])
    for item in (previous or {}).get('data', {}).get('neo_self', []):
        for ident in item['source_ids']:
            quotes.setdefault(reverse[ident], []).append(item['text'])
    return quotes

def _glued_neo_quotes(line: str, quote_sources: dict[str, set[str]]) -> list[tuple[str, str]] | None:
    cells = [cell.strip() for cell in line.split('|')[1:]]
    if not cells or not cells[0]:
        return None
    found: dict[str, str] = {}
    for cell in filter(None, cells):
        matches = quote_sources.get(' '.join(cell.split()), set())
        if len(matches) != 1:
            return None
        found.setdefault(cell, next(iter(matches)))
    return [(alias, text) for text, alias in found.items()] if len(found) > 1 else None

def parse_lines(raw: str, aliases: dict[str, str], today: set[str], *, neo_ids: set[str], done_reason: str | None, neo_quotes: dict[str, list[str]] | None=None, carry: UserCarry | None=None) -> dict:
    if done_reason != 'stop':
        raise ValueError(f'social model response incomplete: {done_reason}')
    today_neo = neo_ids & today
    reverse = {ident: alias for alias, ident in aliases.items()}
    if carry is not None:
        missing = [src for src in carry.sources() if src not in reverse]
        if missing:
            last = max((int(alias[1:]) for alias in aliases if re.fullmatch('S[1-9][0-9]*', alias)), default=0)
            aliases = {**aliases, **{f'S{last + n}': src for n, src in enumerate(missing, 1)}}
            reverse = {ident: alias for alias, ident in aliases.items()}
        neo_ids = neo_ids | {reverse[src] for src in carry.sources()}
    old_by_text: dict[str, list[dict]] = {}
    old_by_alias: dict[str, list[dict]] = {}
    for item in carry.known() if carry else []:
        old_by_text.setdefault(_norm(item['text']), []).append(item)
        for src in item['source_ids']:
            old_by_alias.setdefault(reverse[src], []).append(item)
    votes: list[dict] = []
    new_neo: list[dict] = []
    vote_rows = 0
    last_field = ''

    def _resolve(cell: str):
        olds = old_by_text.get(_norm(cell), [])
        todays = {a for a in quote_sources.get(_norm(cell), set()) if a in today_neo}
        if len(olds) == 1 and (not todays):
            return ('vote', olds[0])
        if not olds and len(todays) == 1:
            return ('new', {'text': cell.strip(), 'source_ids': [aliases[next(iter(todays))]]})
        return None
    quote_sources: dict[str, set[str]] = {}
    for alias, texts in (neo_quotes or {}).items():
        if alias in neo_ids and alias in aliases:
            for text in texts:
                normalized = ' '.join(text.split())
                if normalized:
                    quote_sources.setdefault(normalized, set()).add(alias)
    data = {key: '' if key == 'summary' else [] for key in FIELDS}
    rows = []
    for line in (line.strip() for line in raw.splitlines() if line.strip()):
        if rows and _ALIAS_LIST.fullmatch(line) and ('|' in rows[-1]) and (rows[-1].split('|', 1)[0].strip() == 'transfer_ids'):
            head, _, tail = rows[-1].partition('|')
            parts = (tail.strip().rstrip(','), line.rstrip(','))
            rows[-1] = f'{head}| {', '.join((x for x in parts if x))}'
            continue
        rows.append(line)
    if not rows:
        raise ValueError('empty social response')
    rejected = 0
    foreign_neo: list[str] = []
    recovered_neo = 0
    summary_seen = False
    for line in rows:
        parts = line.split('|', 2)
        field = parts[0].strip()
        if carry is not None:
            bare = _BARE_ALIAS.fullmatch(line)
            if bare:
                targets = old_by_alias.get(bare.group(1), [])
                if last_field == 'neo_self' and len(targets) == 1:
                    votes.append(targets[0])
                    vote_rows += 1
                else:
                    rejected += 1
                    last_field = ''
                continue
            last_field = field
            if field == 'neo_self':
                cells = [cell.strip() for cell in line.split('|')[1:]]
                explicit = len(parts) == 3 and _ALIAS_LIST.fullmatch(parts[1].strip())
                if explicit:
                    ids = [src.strip() for src in parts[1].split(',')]
                    text = parts[2].strip()
                    if any((src not in aliases for src in ids)) or len(ids) != len(set(ids)) or (not text):
                        rejected += 1
                    elif set(ids) & today_neo:
                        new_neo.append({'text': text, 'source_ids': [aliases[src] for src in ids]})
                    else:
                        olds = [i for i in old_by_text.get(_norm(text), []) if {aliases[src] for src in ids} <= set(i['source_ids'])]
                        if len(olds) == 1:
                            votes.append(olds[0])
                            vote_rows += 1
                        else:
                            rejected += 1
                            if not set(ids) & neo_ids:
                                foreign_neo.append(','.join(ids))
                    continue
                resolved = [_resolve(cell) for cell in cells if cell] if cells and cells[0] else []
                if not resolved or any((r is None for r in resolved)):
                    rejected += 1
                    continue
                votes.extend((item for kind, item in resolved if kind == 'vote'))
                fresh = [item for kind, item in resolved if kind == 'new']
                new_neo.extend(fresh)
                recovered_neo += len(fresh)
                vote_rows += not fresh
                continue
        missing_neo_source = field == 'neo_self' and len(parts) >= 2 and (not _ALIAS_LIST.fullmatch(parts[1].strip())) and (len(parts) == 2 or all((not cell.strip() for cell in parts[2].split('|'))))
        if missing_neo_source:
            matches = quote_sources.get(' '.join(parts[1].split()), set())
            if len(matches) == 1:
                parts = [field, next(iter(matches)), parts[1]]
                recovered_neo += 1
        glued = _glued_neo_quotes(line, quote_sources) if field == 'neo_self' else None
        if glued:
            data[field].extend(({'text': text, 'source_ids': [alias]} for alias, text in glued))
            recovered_neo += len(glued)
            continue
        if field == 'summary':
            if len(parts) < 2 or summary_seen:
                raise ValueError('invalid or duplicate social summary')
            data['summary'] = (parts[2] if len(parts) == 3 and (not parts[1].strip()) else '|'.join(parts[1:])).strip()
            summary_seen = True
            continue
        if field not in MODEL_FIELDS or len(parts) < 2 or (field != 'transfer_ids' and len(parts) != 3):
            rejected += 1
            continue
        if field == 'transfer_ids' and len(parts) == 2 and (not parts[1].strip()):
            continue
        if field == 'transfer_ids':
            if not _ALIAS_LIST.fullmatch(parts[1].strip()):
                rejected += 1
                continue
            ids = re.split('\\s*,\\s*|\\s+', parts[1].strip().rstrip(','))
        else:
            ids = [src.strip() for src in parts[1].split(',')]
        if not ids or any((src not in aliases for src in ids)) or len(ids) != len(set(ids)):
            rejected += 1
            continue
        if field == 'transfer_ids':
            if len(parts) not in (2, 3) or (len(parts) == 3 and parts[2].strip()) or any((src not in today for src in ids)):
                rejected += 1
                continue
            data[field].extend(ids)
        elif field == 'neo_self' and (not set(ids) & neo_ids):
            rejected += 1
            foreign_neo.append(','.join(ids))
        elif parts[2].strip():
            data[field].append({'text': parts[2].strip(), 'source_ids': ids})
        else:
            rejected += 1
    transfer_count = len(set(data['transfer_ids']))
    if transfer_count > MAX_TRANSFER_IDS or transfer_count > len(today) / 2:
        print(f'[xian-night] ignored_bulk_transfer={transfer_count}/{len(today)}, max={MAX_TRANSFER_IDS}')
        data['transfer_ids'] = []
    if foreign_neo:
        print(f'[xian-night] rejected_neo_self_foreign_source={len(foreign_neo)} ({'; '.join(foreign_neo)})')
    counted = len(rows) - vote_rows
    if not summary_seen or (rejected > 1 and rejected / counted > 0.2):
        raise ValueError(f'social lines missing summary or rejected {rejected}/{counted}')
    print(f'[xian-night] rejected_social_lines={rejected}/{len(rows)}')
    if recovered_neo:
        print(f'[xian-night] recovered_neo_self_sources={recovered_neo}')
    if carry is not None:
        too_long = [i for i in new_neo if len(i['text'].split()) > nc.MAX_ITEM_WORDS or len(i['text']) > nc.MAX_ITEM_CHARS]
        if too_long:
            print('[xian-night] dropped_long_social_items=' + json.dumps([{'field': 'neo_self', **i} for i in too_long], ensure_ascii=False))
        for item in new_neo:
            if item not in too_long:
                carry.add_new(item)
        carry.votes.update((_item_key(item) for item in votes))
        kept, dropped = carry.select()
        data['neo_self'] = [{'text': i['text'], 'source_ids': [reverse[src] for src in i['source_ids']]} for i in kept]
        old_keys = {_item_key(i) for i in carry.old}
        print(f'[xian-night] neo_self_votes={len(votes)} carried={sum((_item_key(i) in old_keys for i in kept))} new={sum((_item_key(i) not in old_keys for i in kept))}')
        if dropped:
            print('[xian-night] dropped_neo_self=' + json.dumps(dropped, ensure_ascii=False))
    validate_data(data, set(aliases), today=today, neo_ids=neo_ids, enforce_density=False)
    if re.search('\\bS[1-9][0-9]*\\b', data['summary']) or any((re.search('\\bS[1-9][0-9]*\\b', item['text']) for field in LISTS for item in data[field])):
        raise ValueError('social alias repeated in text')
    _bound_model_items(data)
    return parse_response(json.dumps(data, ensure_ascii=False), aliases, today, neo_ids=neo_ids, done_reason='stop')

def read_core(path: Path) -> dict | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        raise ValueError('unknown social core schema')
    ids = value.get('allowed_source_ids')
    if not isinstance(ids, list) or any((not isinstance(i, str) for i in ids)):
        raise ValueError('bad social source registry')
    validate_data(value.get('data'), set(ids), enforce_density=False)
    if not all((isinstance(value.get(k), str) for k in ('core_id', 'window_start', 'window_end'))):
        raise ValueError('social core without provenance')
    return value

def short_view(core: dict) -> str:
    lines = [f'Pogaduchy Model, dzień do {core['window_end']}:', core['data']['summary'].strip()]
    for field in ('neo_self', 'topics', 'personal_open_threads', 'recurring_jokes', 'mood'):
        for item in core['data'][field]:
            line = f'{field}: {item['text']}'
            if len('\n'.join(lines + [line, 'Dalsze pozycje: xian_social_core.json'])) > MAX_CONTEXT_CHARS:
                lines.append('Dalsze pozycje: xian_social_core.json')
                return '\n'.join(lines)
            lines.append(line)
    lines.append('Pełny rdzeń ze źródłami: xian_social_core.json')
    result = '\n'.join(lines)
    if len(result) > MAX_CONTEXT_CHARS:
        raise ValueError('social handoff too long')
    return result

def write_core(path: Path, value: dict) -> None:
    previous = read_core(path)
    if previous and previous['window_end'] >= value['window_end']:
        raise ValueError('refusing to replace existing social day')
    display = short_view(value)
    if previous:
        nc._atomic_text(path.with_name('xian_social_core.previous.json'), json.dumps(previous, ensure_ascii=False, indent=2) + '\n')
    nc._atomic_text(path.with_suffix('.txt'), display + '\n')
    nc._atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def build_core(data: dict, aliases: dict[str, str], raw: str, start: datetime, end: datetime, model: str, source_ids: list[str], segments: list[dict]) -> dict:
    return {'schema': SCHEMA, 'core_id': hashlib.sha256((end.isoformat() + '\n' + raw).encode()).hexdigest(), 'window_start': start.isoformat(), 'window_end': end.isoformat(), 'generated_at': datetime.now(timezone.utc).isoformat(), 'model': model, 'allowed_source_ids': sorted(set(aliases.values())), 'day_message_ids': source_ids, 'segments': segments, 'data': data}
