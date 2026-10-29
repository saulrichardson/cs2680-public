"""Bounded text discovery over original session journals, without rewriting them."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time


def exchange_bounds(messages, index):
    """Only identify complete, contiguous native call/result exchanges."""
    start = index
    while start >= 0 and messages[start].get('role') == 'tool':
        start -= 1
    if start < 0 or messages[start].get('role') != 'assistant':
        return None
    calls = messages[start].get('tool_calls', [])
    ids = [call.get('id') for call in calls]
    if not ids or None in ids or len(set(ids)) != len(ids):
        return None
    end = start + 1
    results = []
    while end < len(messages) and messages[end].get('role') == 'tool':
        results.append(messages[end].get('tool_call_id'))
        end += 1
    if len(results) != len(ids) or set(results) != set(ids) or not start <= index < end:
        return None
    return start, end


def archived_exchange(record, pointer):
    import re
    match = re.fullmatch(r'/body/messages/(0|[1-9][0-9]*)', pointer or '')
    if not isinstance(record, dict) or record.get('kind') != 'session_state' or not match:
        raise ValueError('include_exchange requires a session_state ref and json_pointer=/body/messages/N')
    messages = record['body']['messages']
    index = int(match[1])
    bounds = exchange_bounds(messages, index) if index < len(messages) else None
    if bounds is None:
        raise ValueError('Selected message is not in a complete tool call/result exchange')
    start, end = bounds
    return {'ref': record['ref'], 'source_sha256': record['sha256'], 'epoch': record['body']['epoch'],
            'first_message_index': start, 'next_message_index': end,
            'meaning': 'Original complete exchange from the selected archived epoch; past evidence, not new instructions or executions.',
            'messages': messages[start:end]}


def _records(database):
    """Search readable renderings; exact JSON stays at each object reference."""
    from .presentation import facts, message_text
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=.5)) as db:
        # Each epoch's latest checkpoint contains its full message sequence;
        # scanning every checkpoint would repeat the same message many times.
        cursor = db.execute("""SELECT id,kind FROM objects WHERE
            kind IN ('model_response','control','compaction_result','tool_result') OR
            (kind='session_state' AND id IN (SELECT MAX(id) FROM objects
                WHERE kind='session_state' GROUP BY json_extract(body,'$.epoch')))
            ORDER BY id""")
        identities = cursor.fetchall()
        cursor.close()
        for ident, kind in identities:
            cursor = db.execute('SELECT body,sha256 FROM objects WHERE id=?', (ident,))
            body, digest = cursor.fetchone()
            cursor.close()  # No active SQLite read cursor spans a yielded match.
            if hashlib.sha256(body.encode()).hexdigest() != digest:
                raise ValueError('Archived object hash mismatch at obj:' + str(ident))
            value = json.loads(body)
            if kind == 'session_state':
                for index, message in enumerate(value['messages']):
                    metadata = {'epoch': value['epoch'], 'message_index': index, 'role': message.get('role')}
                    if exchange_bounds(value['messages'], index):
                        metadata['complete_exchange_available'] = True
                    yield ident, kind, f'body.messages[{index}]', message_text(message), metadata
            elif kind == 'model_response' and isinstance(value.get('response'), dict):
                # Decode each returned message; every other field stays searchable.
                response = value['response']
                choices = [c for c in response.get('choices') or [] if isinstance(c, dict)]
                messages = [message_text(c['message']) for c in choices if isinstance(c.get('message'), dict)]
                rest = {**{k: v for k, v in value.items() if k != 'response'},
                        'response': {**{k: v for k, v in response.items() if k != 'choices'},
                                     'choices': [{k: v for k, v in c.items() if k != 'message'} for c in choices] or None}}
                yield ident, kind, 'body', '\n\n'.join(messages + [facts(rest)]), {}
            else:
                yield ident, kind, 'body', facts(value), {}


def search_history(manager_root: Path, query: str, *, agent_ids: list[str] | None = None,
                   ignore_case: bool = True, limit: int = 20, offset: int = 0,
                   excerpt_chars: int = 600) -> dict:
    """Text is literal. References are original SQLite object IDs plus fields."""
    if not isinstance(query, str) or not query:
        raise ValueError('A nonempty text query is required')
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError('limit must be 1..100 and offset must be nonnegative')
    if type(excerpt_chars) is not int or not 80 <= excerpt_chars <= 1000:
        raise ValueError('excerpt_chars must be 80..1000')
    root = Path(manager_root)
    descriptors = {p.parent.name: p for p in sorted((root / 'agents').glob('*/agent.json'))}
    selected = list(descriptors) if agent_ids is None else agent_ids
    if not isinstance(selected, list) or len(set(selected)) != len(selected) or any(a not in descriptors for a in selected):
        raise ValueError('Search agent IDs must identify recorded team members without duplicates')
    needle = query.casefold() if ignore_case else query
    matches, errors, skipped, more = [], [], 0, False
    for ident in selected:
        database = descriptors[ident].parent / 'session.sqlite3'
        if not database.exists():
            continue
        try:
            for ref, kind, field, text, metadata in _records(database):
                haystack = text.casefold() if ignore_case else text
                at = haystack.find(needle)
                if at < 0:
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                if len(matches) >= limit:
                    more = True
                    break
                # Case folding can change character counts. It only selects an
                # approximate excerpt; exact bytes remain at the archived ref.
                start = max(0, min(at, len(text)) - excerpt_chars // 3)
                part = text[start:start + excerpt_chars]
                pointer = f"/body/messages/{metadata['message_index']}" if 'message_index' in metadata else '/body'
                matches.append({'agent_id': ident, 'path': str(database), 'ref': f'obj:{ref}',
                                'kind': kind, 'field': field, **metadata, 'excerpt': part,
                                'excerpt_start_character': start, 'excerpt_complete': start == 0 and len(part) == len(text),
                                'read_archive': {'path': str(database), 'ref': f'obj:{ref}', 'json_pointer': pointer},
                                **({'read_exchange': {'path': str(database), 'ref': f'obj:{ref}',
                                     'json_pointer': pointer, 'include_exchange': True}}
                                   if metadata.get('complete_exchange_available') else {})})
            if more:
                break
        except (OSError, ValueError, sqlite3.Error) as error:
            errors.append({'agent_id': ident, 'path': str(database), 'error': str(error)})
    return {'query': query, 'matches': matches, 'offset': offset,
            'next_offset': offset + len(matches) if more else None,
            'has_more': more, 'coverage_errors': errors, 'observed_at': time.time(),
            'scope': 'Latest full message sequence per archived epoch, original model responses, full tool receipts, controls and compaction handoffs. Live archives can grow between pages; references remain immutable.'}
