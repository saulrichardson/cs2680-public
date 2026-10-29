"""Prepare a reproducible cohort for the untouched course runner; never launch AWS."""
import argparse
import hashlib
import json
from pathlib import Path
import random


def prepare(manifest, output, count, seed, workers):
    raw = manifest.read_bytes()
    source = json.loads(raw)
    if not isinstance(source, dict) or not source:
        raise ValueError('Expected a nonempty course task manifest keyed by task identity')
    if not 1 <= count <= len(source):
        raise ValueError(f'Requested {count} distinct tasks; manifest contains {len(source)}')
    if not 1 <= workers <= 25:
        raise ValueError('Workers must be between 1 and 25')
    seen = set()
    for key, entry in source.items():
        if not isinstance(entry, dict):
            raise ValueError(f'Task {key} must be an object')
        for field in ('instance_id', 'base_commit', 'docker_image', 'problem_statement', 'requirements', 'interface'):
            if not isinstance(entry.get(field), str):
                raise ValueError(f'Task {key} is missing string field {field}')
        if entry['instance_id'] in seen:
            raise ValueError(f'Duplicate instance {entry["instance_id"]}')
        seen.add(entry['instance_id'])
    # Canonical input ordering makes the seed independent of JSON object ordering.
    keys = random.Random(seed).sample(sorted(source), count)
    selected = {str(index): source[key] for index, key in enumerate(keys)}
    encoded = (json.dumps(selected, indent=2) + '\n').encode()
    record = {
        'source_manifest': str(manifest.resolve()),
        'source_sha256': hashlib.sha256(raw).hexdigest(),
        'source_tasks': len(source), 'count': count, 'seed': seed,
        'selected_manifest_sha256': hashlib.sha256(encoded).hexdigest(),
        'positions': [{'dispatcher_id': i, 'display_position': i + 1,
                       'source_key': key, 'instance_id': source[key]['instance_id'],
                       'base_commit': source[key]['base_commit']} for i, key in enumerate(keys)],
        'environment': {'A4_WORKERS': str(workers), 'A3_MAX_LIVE': '0',
                        'A3_MAX_GRADING': '3', 'A3_RUN_LIMIT_S': '14400',
                        'A3_CLOCK_FROM': 'start'},
        'runner': ['python3', 'evaluation_scripts/run_all.py', '--limit', str(count)],
        'scope': 'Development cohort; not a claim about the private leaderboard order',
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / 'agent_task_input.json').write_bytes(encoded)
    (output / 'cohort.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, default=26)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--workers', type=int, default=5)
    args = parser.parse_args()
    try:
        result = prepare(args.manifest, args.output, args.count, args.seed, args.workers)
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps({'cohort': str(args.output.resolve()), 'count': result['count'],
                      'seed': result['seed'], 'workers': result['environment']['A4_WORKERS']}))
