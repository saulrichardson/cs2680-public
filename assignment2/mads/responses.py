"""Lossless native Responses history and a separate function-execution view."""
import copy


def native_turn(response):
    """The full HTTP envelope is already archived; retain every native output field."""
    return {'protocol': 'responses', **{key: copy.deepcopy(response.get(key)) for key in
            ('id', 'status', 'output', 'incomplete_details', 'error')}}


def execution_view(native):
    """Validate the whole native envelope before exposing any calls for execution."""
    if native.get('status') != 'completed':
        raise ValueError(f"Unfinished Responses envelope: status={native.get('status')}, "
                         f"incomplete_details={native.get('incomplete_details')}; no calls executed")
    if native.get('error') is not None:
        raise ValueError('Responses envelope contains an error; no calls executed')
    output = native.get('output')
    if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
        raise ValueError('Responses output must be a list of item objects; no calls executed')
    calls, ids, native_ids = [], set(), set()
    for item in output:
        if item.get('id'):
            if not isinstance(item['id'], str) or item['id'] in native_ids:
                raise ValueError('Responses output items require unique supplied native item IDs')
            native_ids.add(item['id'])
        if not isinstance(item.get('type'), str) or not item['type'].strip():
            raise ValueError('Responses output items require a type; no calls executed')
        if item.get('status') in ('in_progress', 'incomplete', 'failed'):
            raise ValueError('Incomplete Responses output item; no calls executed')
        if item['type'] == 'message':
            if item.get('role') != 'assistant' or not isinstance(item.get('content'), list) or any(
                    not isinstance(block, dict) or not isinstance(block.get('type'), str)
                    for block in item['content']):
                raise ValueError('Malformed Responses assistant message; no calls executed')
            if item.get('phase') is not None and not isinstance(item['phase'], str):
                raise ValueError('Responses assistant phase must be text when supplied')
            for block in item['content']:
                field = {'output_text':'text', 'refusal':'refusal'}.get(block['type'])
                if field and not isinstance(block.get(field), str):
                    raise ValueError('Malformed Responses assistant content; no calls executed')
        if item['type'] == 'reasoning':
            if item.get('encrypted_content') is not None and not isinstance(item['encrypted_content'], str):
                raise ValueError('Responses encrypted reasoning must remain an opaque string')
        if item['type'] != 'function_call':
            continue
        identifier, name, arguments = item.get('call_id'), item.get('name'), item.get('arguments')
        if not isinstance(identifier, str) or not identifier.strip() or identifier in ids:
            raise ValueError('Responses function calls require unique nonempty call_id values')
        if not isinstance(item.get('id'), str) or not item['id'].strip():
            raise ValueError('Responses function calls require a native item id')
        if not isinstance(name, str) or not name.strip() or not isinstance(arguments, str):
            raise ValueError('Responses function calls require a name and argument string')
        ids.add(identifier)
        calls.append({'id': identifier, 'type': 'function', 'native_item_id': item['id'],
                      'function': {'name': name, 'arguments': arguments}})
    return {'role': 'assistant', 'responses_output': copy.deepcopy(output), 'tool_calls': calls}


def request_body(logical):
    """Convert inside the context compiler, before capacity measurement and persistence."""
    body = copy.deepcopy(logical)
    sequence = []
    for message in body.pop('messages'):
        if 'responses_output' in message:
            sequence.extend(message['responses_output'])
        elif message['role'] == 'tool':
            sequence.append({'type': 'function_call_output', 'call_id': message['tool_call_id'],
                             'output': message['content']})
        else:
            sequence.append(message)
    body['input'] = sequence
    body['tools'] = [{'type': 'function', **tool['function'], 'strict': False}
                     for tool in body['tools']]
    body['max_output_tokens'] = body.pop('max_tokens')
    return body
