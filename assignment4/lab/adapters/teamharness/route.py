"""Extend a fresh course reroute copy for its advertised structured-output surface.

Only OpenRouter json_object/json_schema output is added. Model and provider
routing, stock grading and all previously accepted requests remain unchanged.
Never apply this to the source of an active or historical campaign.
"""
from pathlib import Path
import hashlib


VALIDATION = '''    for message in request['messages']:
        if 'refusal' in message and (spec['upstream'] != 'openrouter'
                or message['role'] != 'assistant'
                or (message['refusal'] is not None and not isinstance(message['refusal'], str))):
            raise ValueError('Native refusal continuation requires an OpenRouter assistant string or null')
    if 'response_format' in request:
        output = request['response_format']
        if spec['upstream'] != 'openrouter':
            raise ValueError('Structured output relay is implemented only for OpenRouter')
        if not isinstance(output, dict):
            raise ValueError('response_format must be an object')
        if output.get('type') == 'json_object':
            if set(output) != {'type'}:
                raise ValueError('Invalid json_object format')
        elif output.get('type') == 'json_schema':
            schema = output.get('json_schema')
            if (set(output) != {'type', 'json_schema'} or not isinstance(schema, dict)
                    or set(schema) - {'name', 'description', 'schema', 'strict'}
                    or not isinstance(schema.get('name'), str) or not schema['name']
                    or not isinstance(schema.get('schema'), dict)
                    or ('strict' in schema and type(schema['strict']) is not bool)):
                raise ValueError('Invalid json_schema format')
        else:
            raise ValueError('Unsupported structured output format')
'''


def patch_origin(path: Path) -> dict:
    path = Path(path)
    before = path.read_bytes()
    text = before.decode()
    old = "          'max_tokens', 'stop', 'temperature', 'top_p', 'reasoning_effort'}"
    message_fields = "                  'reasoning', 'reasoning_content', 'reasoning_details'}"
    anchor = "    spec = route_spec(config, alias)\n    translated = copy.deepcopy(request)"
    if text.count(old) != 1 or text.count(anchor) != 1 or text.count(message_fields) != 1:
        raise ValueError('Expected unmodified course reroute source; refuse uncertain patch')
    text = text.replace(old, old[:-1] + ", 'response_format'}")
    # OpenRouter includes refusal:null on successful assistant tool responses.
    # Preserve that native continuation field instead of rejecting the next turn.
    text = text.replace(message_fields, message_fields[:-1] + ", 'refusal'}")
    text = text.replace(anchor, "    spec = route_spec(config, alias)\n" + VALIDATION + "    translated = copy.deepcopy(request)")
    compile(text, str(path), 'exec')
    path.write_text(text)
    return {'before_sha256': hashlib.sha256(before).hexdigest(),
            'after_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'change': 'Forward validated OpenRouter json_object/json_schema response_format and native assistant refusal field unchanged'}
