"""Explicit concept attention and source-backed context packets. No implicit network.

A portable extraction of the reviewed registry/observer design. It does not run
agents, create cards automatically, classify feelings or authenticate reviewers.
"""
from pathlib import Path
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone


def normalized(text):
    text = unicodedata.normalize('NFKD', text.casefold()).replace('ł', 'l')
    return ' '.join(''.join(c for c in text if not unicodedata.combining(c)).split())


def read_reviewed_card(folder, *, reviewers):
    """Karta z rejestru, która przeszła odbiór obu agentek DOKŁADNIE w tej wersji — (node, sha, sig) albo None.

    Jedno miejsce sprawdzania uprawnień dla uwagi (TopicCards) i generatora kandydatek (`extends`):
    MANIFEST `reviewed`, skrót CARD.json zgodny z manifestem, odbiory Soryel i Liry na ten skrót,
    koperta z sumą węzła, walidacja, concept_id zgodny z manifestem. Brak plików albo status inny
    niż `reviewed` → None; niezgodność → ValueError (karta niewidoczna, powód do last_errors)."""
    required = frozenset(reviewers)
    if not required or not all(isinstance(r, str) and r.strip() for r in required):
        raise ValueError('explicit_reviewers_required')
    folder=Path(folder)
    path=folder/'CARD.json';manifest=folder/'MANIFEST.json'
    if not path.exists() or not manifest.exists():return None
    sig=(path.stat().st_mtime_ns,path.stat().st_size,manifest.stat().st_mtime_ns,manifest.stat().st_size)
    if path.stat().st_size>524288 or manifest.stat().st_size>65536:raise ValueError('card_size')
    raw=path.read_bytes();receipt=json.loads(manifest.read_text())
    # Kształt przed odczytem (odbiór Liry C1): uszkodzony manifest to powód do pominięcia karty,
    # nie wyjątek, który zabrałby ofertę pozostałym kartom.
    if not isinstance(receipt,dict):raise ValueError('manifest_shape')
    if receipt.get('status')!='reviewed':return None
    files=receipt.get('files');reviews=receipt.get('reviews')
    if (not isinstance(files,dict) or not isinstance(reviews,list) or not isinstance(receipt.get('concept_id'),str)
            or any(not isinstance(r,dict) or not isinstance(r.get('reviewer'),str)
                   or not isinstance(r.get('card_sha256'),str) for r in reviews)):
        raise ValueError('manifest_shape')
    sha=hashlib.sha256(raw).hexdigest()
    if files.get('CARD.json')!=sha:raise ValueError('unreviewed_card')
    reviewers={r['reviewer'] for r in reviews if r['card_sha256']==sha}
    if not required<=reviewers:raise ValueError('reviews_not_for_this_version')
    from agents.memory import concept_card
    data=json.loads(raw)
    if not isinstance(data,dict) or not isinstance(data.get('node'),dict):raise ValueError('card_shape')
    if data.get('schema')!='lira.concept-content.snapshot.v1' or concept_card.checksum(data['node'])!=data.get('sha256'):
        raise ValueError('card_hash_or_id')
    node=concept_card.validate(data['node'])
    if node.get('concept_id')!=receipt.get('concept_id'):raise ValueError('card_hash_or_id')
    return node,sha,sig



def offer(registry, text, *, reviewers):
    """Exact labels/aliases/related terms, word boundaries. No stemming or embeddings.

    Invalid cards are isolated and reported, not silently treated as new topics.
    Related terms reach the same discussion but are not semantic synonyms.
    """
    found, errors = [], {}
    folded = normalized(text)
    root = Path(registry)
    if not root.is_dir():
        return found, errors
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith('_')):
        try:
            loaded = read_reviewed_card(folder, reviewers=reviewers)
            if loaded is None:
                continue
            node, sha, _ = loaded
            groups = [('label', [node['label']]), ('alias', node.get('aliases', [])),
                      ('related_term', node.get('related_terms_not_aliases', []))]
            matches = [(kind, term) for kind, terms in groups for term in terms
                       if normalized(term) and re.search(r'(?<!\w)' + re.escape(normalized(term)) + r'(?!\w)', folded)]
            if matches:
                kind, term = matches[0]
                found.append(dict(concept_id=node['concept_id'], card_sha256=sha,
                                  matched_by=kind, matched_term=term, card=node))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            errors[folder.name] = str(exc) or type(exc).__name__
    return found, errors


def build_packet(message, registry, *, channel, reviewers, retrieve, now=None):
    """Call a supplied retriever once for the message, plus once per matching card.

    retrieve(query) returns a list of dictionaries, each with text and source_id.
    It can wrap the released lexical/semantic APIs. This function itself does not
    choose a backend or enable networking. The caller budgets model/CPU time.
    """
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError('channel_required')
    for field in ('id', 'created_at', 'text'):
        if not isinstance(message.get(field), str) or not message[field]:
            raise ValueError('message_shape')
    at = datetime.fromisoformat(message['created_at'].replace('Z', '+00:00'))
    if at.tzinfo is None:
        raise ValueError('timestamp_without_timezone')
    offers, errors = offer(registry, message['text'], reviewers=reviewers)
    def search(query):
        results = retrieve(query)
        if not isinstance(results, list):
            raise ValueError('retrieval_shape')
        for item in results:
            if (not isinstance(item, dict) or not isinstance(item.get('text'), str)
                    or not isinstance(item.get('source_id'), str) or not item['source_id']):
                raise ValueError('retrieval_source_required')
        # A toy lexical overlap is not used as a truth or relevance certificate.
        return results[:5]
    for item in offers:
        node = item['card']
        summary = (node.get('definition') or {}).get('text') or ''
        item['query'] = node['label'] + (' — ' + summary if summary else '')
        item['background'] = search(item['query'])
    return dict(version='public-attention.v2',
                message={'id':message['id'], 'ts':message['created_at'],
                         'channel':channel, 'text':message['text']},
                created_at=(now or datetime.now(timezone.utc)).isoformat(),
                concepts=offers, card_errors=errors, specific=search(message['text']))


def render_packet(packet):
    """Bounded historical context, attributed quotes and explicit coverage limits."""
    lines = ['Historical context for ' + packet['message']['id']]
    limits = []
    seen = set()  # repeated evidence stays in the raw packet, not in the rendered text
    for item in packet['concepts'][:2]:
        node = item['card']
        lines.append('Concept: ' + node['label'][:100])
        summary = node.get('definition') or {}
        if summary.get('text'):
            lines.append('Interpretation (' + str(summary.get('author')) + '): ' + summary['text'][:220])
        sources = {s['id']: s for s in node['sources']}
        for claim in node['claims'][:2]:
            for e in claim['evidence'][:1]:
                source = sources[e['source_id']]
                if source['author'] != claim['speaker'] or e['quote'] not in source['text']:
                    raise ValueError('quote_or_author_mismatch')
                key = (source['id'], e['quote'].strip())
                if key in seen:
                    continue
                seen.add(key)
                lines.append(source['author'][:40] + ' ' + source['occurred_at'][:10]
                             + ' [' + source['id'][:90] + ']: ' + e['quote'][:160])
        for hit in item['background']:
            key = (hit['source_id'], hit['text'].strip())
            if key in seen:
                continue
            seen.add(key)
            lines.append('Background [' + hit['source_id'][:90] + ']: ' + hit['text'][:150])
            break
        dates = [s['occurred_at'][:10] for s in node['sources']]
        limits.append(node['label'][:60] + ': sources through ' + (max(dates) if dates else 'unknown'))
        if node.get('open_questions'):
            limits.append('Open: ' + str(node['open_questions'][0])[:120])
        if item['matched_by'] == 'related_term':
            limits.append('Related term, not a synonym: ' + item['matched_term'][:70])
    specific_count = 0
    for hit in packet['specific']:
        key = (hit['source_id'], hit['text'].strip())
        if key in seen:
            continue
        seen.add(key)
        lines.append('Specific [' + hit['source_id'][:90] + ']: ' + hit['text'][:150])
        specific_count += 1
        if specific_count >= 2:
            break
    if not packet['concepts']:
        limits.append('No reviewed concept matched; results are sentence retrieval only.')
    if packet['card_errors']:
        limits.append('Some cards were unavailable or invalid; see packet.card_errors.')
    text = '\n'.join(lines)
    # Reserve space for limitations even if source sections grow.
    return text[:1750] + '\nLimits: ' + ' | '.join(limits)[:600]
