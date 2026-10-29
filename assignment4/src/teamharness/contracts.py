"""Validate the exact advertised JSON Schema without rewriting arguments.

Schemas, including their examples, are the contract. Compiled validators are a
cache only; original descriptors remain in session/configuration evidence.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import json

import fastjsonschema


VERSION = '2.21.2'


class ArgumentError(ValueError):
    def __init__(self, message, *, path='arguments', rule=None):
        super().__init__(message)
        self.path, self.rule = path, rule


def parse_arguments(text):
    """Reject ambiguous JSON rather than silently selecting a duplicate field."""
    def object_fields(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError('Duplicate tool argument field: ' + name)
            result[name] = value
        return result

    def nonfinite(value):
        raise ValueError('Tool arguments require finite JSON numbers: ' + value)

    return json.loads(text, object_pairs_hook=object_fields, parse_constant=nonfinite)


def _local_references(schema):
    if isinstance(schema, dict):
        ref = schema.get('$ref')
        if ref is not None and (not isinstance(ref, str) or not ref.startswith('#')):
            raise ValueError('Tool schemas require local $ref values; validation does not fetch external resources')
        dialect = schema.get('$schema')
        if dialect is not None and dialect not in (
                'http://json-schema.org/draft-04/schema#',
                'http://json-schema.org/draft-06/schema#',
                'http://json-schema.org/draft-07/schema#'):
            raise ValueError('Tool schemas support JSON Schema draft-04, draft-06 and draft-07')
        # Walk schema positions, not example/default/const data containing $ref.
        for key in ('properties', 'patternProperties', 'definitions', '$defs', 'dependencies'):
            for child in schema.get(key, {}).values():
                _local_references(child)
        for key in ('items', 'additionalItems', 'additionalProperties', 'propertyNames', 'contains', 'not', 'if', 'then', 'else'):
            child = schema.get(key)
            for item in child if isinstance(child, list) else [child]:
                _local_references(item)
        for key in ('allOf', 'anyOf', 'oneOf'):
            for child in schema.get(key, []):
                _local_references(child)


@lru_cache(maxsize=256)
def _compiled(serialized):
    schema = json.loads(serialized)
    _local_references(schema)
    try:
        # Defaults are annotations, never permission to modify model arguments.
        def no_external_reference(uri):
            raise ValueError('Tool schema validation cannot fetch external resources: ' + uri)
        return fastjsonschema.compile(schema, use_default=False,
            handlers={scheme: no_external_reference for scheme in ('http', 'https', 'file')})
    except fastjsonschema.JsonSchemaException as error:
        raise ValueError('Invalid tool schema: ' + str(error)) from error


def check_schema(schema):
    if not isinstance(schema, dict):
        raise ValueError('Tool parameters must be a JSON Schema object')
    _compiled(json.dumps(schema, sort_keys=True, allow_nan=False))


def validate(value, schema, path='arguments'):
    validator = _compiled(json.dumps(schema, sort_keys=True, allow_nan=False))
    try:
        # Tool arguments are JSON, not Python NaN/Infinity extensions.
        json.dumps(value, allow_nan=False)
        validator(value)
    except fastjsonschema.JsonSchemaValueException as error:
        location = path + ''.join('[' + str(p) + ']' if isinstance(p, int) else '.' + str(p)
                                  for p in error.path[1:])
        detail = str(error).replace(error.name, location, 1)
        raise ArgumentError(detail, path=location, rule=error.rule) from error
    except (ValueError, TypeError) as error:
        raise ArgumentError(str(error), path=path, rule='json') from error


def examples(schema):
    """Only show examples that also satisfy the current configured schema."""
    result = []
    for example in schema.get('examples', []):
        try:
            validate(example, schema)
        except (ValueError, TypeError):
            continue
        result.append(copy.deepcopy(example))
    return result


def presented_tools(tools):
    """Project annotations against configured constraints without changing them."""
    result = copy.deepcopy(tools)
    for tool in result:
        schema = tool['function']['parameters']
        if 'examples' in schema:
            schema['examples'] = examples(schema)
    return result


# Literal examples describe argument shape; referenced files/IDs are illustrative.
# They become schema annotations, so request and recovery use the same examples.
EXAMPLES = {
    'exec_command': [{'command': 'pwd'}],
    'job': [{'id': 'existing-job-id', 'action': 'poll'}],
    'list_jobs': [{}],
    'read_file': [{'path': 'src/example.py', 'start_line': 1, 'line_count': 80},
                  {'path': 'src/example.py', 'offset': 0, 'max_output_bytes': 4096}],
    'search_files': [{'path': '.', 'glob': '*.py', 'query': 'function_name'}],
    'edit_file': [{'path': 'src/example.py', 'replacements': [{'old': 'return old_value', 'new': 'return new_value'}]}],
    'apply_patch': [{'patch': '*** Begin Patch\n*** Add File: example.txt\n+example\n*** End Patch\n'}],
    'parallel': [{'calls': [{'name': 'exec_command', 'arguments': {'command': 'pwd'}},
                            {'name': 'exec_command', 'arguments': {'command': 'git status --short'}}]}],
    'inspect_parallel': [{'group_id': 'existing-group-id'}],
    'cancel_parallel': [{'group_id': 'existing-group-id'}],
    'read_archive': [{'path': '/absolute/session.sqlite3', 'ref': 'obj:123'}],
    'search_history': [{'query': 'observed failure'}],
    'spawn_agent': [{'role': 'investigator', 'task': 'Trace the caller and consumer of the changed function.', 'workspace': 'private'}],
    'inspect_agent': [{'agent_id': 'existing-agent-id'}],
    'list_agents': [{}],
    'wait_agent': [{'agent_id': 'existing-agent-id', 'timeout_seconds': 10}],
    'wait_agents': [{'agent_ids': ['existing-agent-id'], 'mode': 'any', 'timeout_seconds': 10}],
    'send_message': [{'agent_id': 'existing-agent-id', 'message': 'Check the actual caller against the observed failure.'}],
    'stop_agent': [{'agent_id': 'existing-agent-id'}],
    'apply_agent_patch': [{'agent_id': 'existing-agent-id'}],
    'checkpoint': [{'label': 'Current implementation, not yet verified'}],
    'repair_plan': [{'action': 'set', 'plan': 'Trace the required operation, change its implementation and verify the real consumer.',
                     'checks': [{'name': 'public operation', 'argv': ['python3', '-m', 'unittest', 'test_public_operation'], 'cwd': '.'}]},
                    {'action': 'run', 'target': 'candidate'}],
    'task_status': [{}],
    'evaluate_candidate': [{}],
    'compact': [{}],
    'finish_role': [{'outcome': 'complete', 'report': 'Describe the actual result and remaining verification limits.'}],
    'configure_session': [{'reason': 'Use independent calls concurrently.', 'tool_execution': 'parallel'}],
}


def describe(name, schema):
    result = copy.deepcopy(schema)
    result['examples'] = copy.deepcopy(EXAMPLES[name])
    if name == 'read_file':
        result['allOf'] = [{'not': {'required': ['offset', field]}}
                           for field in ('start_line', 'line_count')]
    elif name == 'spawn_agent':
        result['allOf'] = [
            {'if': {'properties': {'source': {'enum': ['starting', 'checkpoint']}}, 'required': ['source']},
             'then': {'properties': {'workspace': {'const': 'private'}}}},
            {'if': {'properties': {'source': {'const': 'checkpoint'}}, 'required': ['source']},
             'then': {'required': ['checkpoint_id']}}]
    elif name == 'repair_plan':
        result['allOf'] = [
            {'if': {'properties': {'action': {'enum': ['set', 'continue']}}, 'required': ['action']},
             'then': {'required': ['plan'], 'properties': {'plan': {'pattern': r'\S'}}}},
            {'if': {'properties': {'action': {'const': 'skip'}}, 'required': ['action']},
             'then': {'required': ['reason'], 'properties': {'reason': {'pattern': r'\S'}}}}]
        result['properties']['checks']['items']['properties']['cwd']['description'] = (
            'Directory relative to the captured repository root, for example . or tests. Never an absolute workspace path.')
    elif name == 'read_archive':
        result['allOf'] = [{'if': {'properties': {'include_exchange': {'const': True}},
                                 'required': ['include_exchange']},
                            'then': {'required': ['ref', 'json_pointer'],
                                     'properties': {'json_pointer': {'pattern': r'^/body/messages/(0|[1-9][0-9]*)$'}}}}]
    elif name == 'finish_role':
        result['properties']['report']['pattern'] = r'\S'
    elif name == 'configure_session':
        result['properties']['reason']['pattern'] = r'\S'
    return result
