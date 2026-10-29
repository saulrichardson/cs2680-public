"""Render a supplied official Qwen template locally; never infer hosted rendering."""
import hashlib,json,sys
from pathlib import Path
from jinja2.sandbox import ImmutableSandboxedEnvironment

if __name__ == '__main__':
    source=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True)
    env=ImmutableSandboxedEnvironment(trim_blocks=True,lstrip_blocks=True)
    def reject(message):raise ValueError(message)
    env.globals['raise_exception']=reject
    template=env.from_string(source.read_text())
    base=[{'role':'system','content':'Harness.'},{'role':'user','content':'Original task.'},
          {'role':'assistant','content':'Observed.','reasoning_content':'HISTORICAL-REASON-719'}]
    checks={}
    for name,suffix,options,expected in [
     ('tool-result',[{'role':'tool','content':'Result.'}],{},True),
     ('wrapped-notice',[{'role':'user','content':'<tool_response>Generated notice.</tool_response>'}],{},True),
     ('plain-notice',[{'role':'user','content':'Generated notice.'}],{},False),
     ('plain-preserved',[{'role':'user','content':'Generated notice.'}],{'preserve_thinking':True},True),
     ('changed-account-before-history',[],{},True),
     ('restart-retained-native',[],{},True),
    ]:
     messages=base+suffix
     if name=='changed-account-before-history':messages=base[:2]+[{'role':'user','content':'<tool_response>Revised account.</tool_response>'}]+base[2:]
     rendered=template.render(messages=messages,add_generation_prompt=True,**options)
     (out/(name+'.txt')).write_text(rendered)
     checks[name]={'historical_reason_visible':'HISTORICAL-REASON-719' in rendered,'expected':expected}
     assert checks[name]['historical_reason_visible']==expected
    try:template.render(messages=base+[{'role':'system','content':'Late.'}])
    except ValueError as exc:checks['late-system']={'rejected':str(exc)}
    else:raise AssertionError('Late system accepted')
    alias=[*base[:2],{'role':'assistant','content':'Observed.','reasoning':'ALIAS-REASON-538'}]
    rendered=template.render(messages=alias)
    checks['reasoning-alias']={'rendered_directly':'ALIAS-REASON-538' in rendered,'interpretation':'Published template only reads reasoning_content; OpenRouter normalization is a separate, unobserved server layer.'}
    assert not checks['reasoning-alias']['rendered_directly']
    (out/'results.json').write_text(json.dumps({'template_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'checks':checks,'scope':'Local template rendering only, not proof of Course/OpenRouter server configuration.'},indent=2))
    print(json.dumps(checks))
