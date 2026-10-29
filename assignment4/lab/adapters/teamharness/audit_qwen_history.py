"""CPU-only rendering audit of pinned Qwen metadata and recorded wire requests.

Requires Jinja2 and tokenizers in the analysis environment, never in the solver.
Loads local metadata only; no model weights, GPU calls, credentials or network.
Original requests remain unchanged. Text/IDs are private diagnostic artifacts.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


def renderer(directory):
    from jinja2 import TemplateError
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    from tokenizers import Tokenizer
    template=(directory/'chat_template.jinja').read_text()
    embedded=json.loads((directory/'tokenizer_config.json').read_text())['chat_template']
    if template!=embedded:
        raise ValueError('Separate and embedded chat templates differ; select the actual server template explicitly')
    environment=ImmutableSandboxedEnvironment(trim_blocks=True,lstrip_blocks=True)
    def reject(message): raise TemplateError(message)
    environment.globals['raise_exception']=reject
    environment.filters['tojson']=lambda value,**kwargs:json.dumps(value,ensure_ascii=False,**kwargs)
    compiled=environment.from_string(template)
    tokenizer=Tokenizer.from_file(str(directory/'tokenizer.json'))
    def render(messages,tools=None,**kwargs):
        messages=copy.deepcopy(messages)
        # The pinned SGLang Jinja path parses historical function.arguments to
        # objects before rendering and normalizes null text content to empty.
        for message in messages:
            if message.get('content') is None: message['content']=''
            for call in message.get('tool_calls') or []:
                function=call['function']
                if isinstance(function.get('arguments'),str):
                    function['arguments']=json.loads(function['arguments'])
        text=compiled.render(messages=messages,tools=tools,add_generation_prompt=True,**kwargs)
        ids=tokenizer.encode(text,add_special_tokens=False).ids
        return text,ids
    return render


def audit(directory, requests, output):
    paths=sorted(requests.glob('request-*.json'))
    if not paths:
        raise ValueError('No recorded wire requests found')
    render=renderer(directory)
    output.mkdir(parents=True,exist_ok=False)
    marker='QWEN_RENDERED_REASONING_SENTINEL_2680'
    system={'role':'system','content':'Synthetic foundation.'}
    query={'role':'user','content':'Investigate the public behavior.'}
    assistant={'role':'assistant','content':'','reasoning_content':marker,
        'tool_calls':[{'id':'call-1','type':'function','function':{
            'name':'exec_command','arguments':'{"command":"printf observed"}'}}]}
    tool={'role':'tool','tool_call_id':'call-1','content':'Observed original command output.'}
    history=[system,query,assistant,tool]
    followup=history+[{'role':'user','content':'A new explicit question.'}]
    cases=[('same_user_tool_loop',history,{}),('new_user_default',followup,{}),
           ('new_user_preserve_thinking',followup,{'preserve_thinking':True}),
           ('late_system_observation',history+[{'role':'system','content':'Dated runtime fact.'}],{})]
    results=[]
    for name,messages,kwargs in cases:
        try:
            text,ids=render(messages,**kwargs)
            (output/(name+'.txt')).write_text(text)
            (output/(name+'.tokens.json')).write_text(json.dumps(ids))
            results.append({'case':name,'rendered':True,'reasoning_in_prompt':marker in text,
                'tokens':len(ids),'prompt_sha256':hashlib.sha256(text.encode()).hexdigest()})
        except Exception as error:
            results.append({'case':name,'rendered':False,'error':str(error),'error_type':type(error).__name__})
    wire=[]
    for path in paths:
        request=json.loads(path.read_text())
        entry={'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'temperature':request.get('temperature'),
            'system_message_indices':[i for i,m in enumerate(request['messages']) if m['role']=='system'],
            'assistant_reasoning_fields':sum(bool(m.get('reasoning_content')) for m in request['messages'] if m['role']=='assistant')}
        try:
            text,ids=render(request['messages'],request.get('tools'),**request.get('chat_template_kwargs',{}))
            entry.update(rendered=True,tokens=len(ids))
            (output/(path.stem+'.prompt.txt')).write_text(text)
        except Exception as error:
            entry.update(rendered=False,error=str(error),error_type=type(error).__name__)
        wire.append(entry)
    hashes={name:hashlib.sha256((directory/name).read_bytes()).hexdigest()
        for name in ('chat_template.jinja','tokenizer_config.json','tokenizer.json','generation_config.json')}
    record={'metadata_sha256':hashes,'cases':results,'wire_requests':wire,
        'scope':'Exact local checkpoint template and tokenizer; text-only SGLang-compatible preprocessing. No live server/GPU execution.'}
    expected={'same_user_tool_loop':True,'new_user_default':False,'new_user_preserve_thinking':True}
    assert all(row['rendered'] and row['reasoning_in_prompt']==expected[row['case']]
               for row in results if row['case'] in expected),results
    assert not next(row for row in results if row['case']=='late_system_observation')['rendered'],results
    (output/'result.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-directory',type=Path,required=True)
    parser.add_argument('--requests',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(audit(args.model_directory,args.requests,args.output),indent=2))
