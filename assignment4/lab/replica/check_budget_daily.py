"""Daily accounting and actual concurrent guarded launches; no cloud effects."""
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import budget

HERE=Path(__file__).resolve().parent
def timestamp(value):return datetime.fromisoformat(value).timestamp()
def state():return {'mode':'daily','timezone':'America/New_York','daily_limit_usd':100,
                   'budget_scope':'this autonomous agent-performance research program',
                   'scope_complete':True,'unbounded_unknowns':[],'exposures':[],'admissions':[]}
def rejected(function):
    try:function()
    except (ValueError,RuntimeError):return
    raise AssertionError('Unsafe admission accepted')
def row(ident,start,end,bound,settled=0,held=0,historical=0):
    return {'id':ident,'source':'fixture '+ident,'evidence':['local fixture'],
            'started_at':start,'status':'open' if end is None else 'closed',
            'closed_at':end,'conservative_cost_bound_usd':bound,'settled_usd':settled,
            'held_usd':held,'historical_commitment_usd':historical,
            'bound_through':start+10*86400 if end is None else end}

now=timestamp('2026-10-25T23:30:00-04:00')
assert budget.dates(timestamp('2026-03-08T00:00:00-05:00'),timestamp('2026-03-08T23:59:59-04:00'))==['2026-03-08']
assert budget.dates(timestamp('2026-11-01T00:00:00-04:00'),timestamp('2026-11-01T23:59:59-05:00'))==['2026-11-01']
s=state();before=deepcopy(s);result=budget.daily_assessment(s,20,4.5,now)
assert s==before and list(result['days'])==['2026-10-25','2026-10-26']
assert all(x['proposed_usd']==20 for x in result['days'].values())
assert result['proposed_cleanup_envelope_end']==now+20*3600/4.5

# Root-attested closed bounds do not convert historical holds into settlement.
s['exposures']=[row('closed',now-3000,now-2000,8,settled=10,historical=40),
                row('other-session',now-2000,None,30,held=35)]
result=budget.daily_assessment(s,20,4.5,now)
assert result['days']['2026-10-25']['existing_usd']==45
assert result['days']['2026-10-26']['existing_usd']==35
assert result['liabilities'][0]['historical_commitment_usd']==40
assert result['liabilities'][0]['settled_usd']==10 and s['exposures'][0]['conservative_cost_bound_usd']==8
s['exposures'][1]['conservative_cost_bound_usd']=90
assert not budget.daily_assessment(s,20,4.5,now)['allowed']
s['exposures'][1]['conservative_cost_bound_usd']=None
assert not budget.daily_assessment(s,20,4.5,now)['allowed']
s['exposures'][1]['conservative_cost_bound_usd']=30
s['exposures'][1]['bound_through']=now+60
assert not budget.daily_assessment(s,20,4.5,now)['allowed'],'Accrued-only external evidence cannot cover a future commitment'
for key,value in [('timezone','UTC'),('scope_complete',False),('budget_scope',''),('unbounded_unknowns',['unpriced program resource'])]:
    bad=state();bad[key]=value;rejected(lambda:budget.daily_assessment(bad,20,4.5,now))
rejected(lambda:budget.daily_assessment(state(),1,4.5,now))
rejected(lambda:budget.daily_assessment(state(),float('nan'),4.5,now))
s=state();s['exposures']=[row('future',now+3*86400,now+4*86400,90)]
assert not budget.daily_assessment(s,20,4.5,now)['allowed'],'Pending guard must cover already-known future liability dates'

with tempfile.TemporaryDirectory(prefix='daily-budget-check-') as folder:
    root=Path(folder);account=root/'daily.json';account.write_text(json.dumps(state()))
    launcher=root/'launcher.py'
    launcher.write_text('''import argparse,json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import budget
p=argparse.ArgumentParser();p.add_argument('tools');p.add_argument('--budget-state');p.add_argument('--account');p.add_argument('--exit',type=int,default=0);a=p.parse_args()
s=json.loads(Path(a.account).read_text());entry=next(x for x in s['admissions'] if x['campaign_ledger']==str(Path(a.budget_state).resolve()))
assert entry['state']=='pending' and entry['amount_usd']==60
time.sleep(.1)
value=budget.reserve(a.budget_state,Path(a.budget_state).with_suffix('.run'))
assert value['amount_usd']==60
assert value['soft_stop_at']<value['hard_stop_at']
sys.exit(a.exit)
''')
    processes=[]
    for i in range(8):
        leaf=root/f'leaf-{i}.json'
        argv=[sys.executable,str(HERE/'budget.py'),'guard','--account',str(account),
              '--campaign-ledger',str(leaf),'--amount','60','--source','concurrent-'+str(i),'--',
              sys.executable,str(launcher),str(HERE),'--budget-state',str(leaf),'--account',str(account)]
        processes.append(subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE))
    outcomes=[(p.returncode,out,err) for p in processes for out,err in [p.communicate(timeout=20)]]
    assert sum(code==0 for code,_,_ in outcomes)==1,outcomes
    recorded=json.loads(account.read_text());assert len(recorded['admissions'])==1
    entry=recorded['admissions'][0];leaf=Path(entry['campaign_ledger']);leaf_state=json.loads(leaf.read_text())
    assert entry['state']=='reserved' and len(leaf_state['commitments'])==1
    assert entry['commitment_id']==leaf_state['commitments'][0]['id']
    assert leaf_state['settled_usd']==0
    # The guard hold and its reservation are a single liability, not two charges.
    assessed=budget.daily_assessment(recorded,20,4.5)
    assert all(x['existing_usd']==60 for x in assessed['days'].values())
    rejected(lambda:budget.guarded_launch(account,leaf,60,4.5,'duplicate',entry['command']))

    # An error after admission is a durable held liability; no automatic refund.
    fail_account=root/'failed.json';fail_account.write_text(json.dumps(state()));failed=root/'failed-leaf.json'
    command=['recording-launcher','--budget-state',str(failed)]
    with patch('subprocess.run',side_effect=KeyboardInterrupt):
        try:budget.guarded_launch(fail_account,failed,60,4.5,'interrupted',command,now=now)
        except KeyboardInterrupt:pass
        else:raise AssertionError('Interruption disappeared')
    pending=json.loads(fail_account.read_text());assert pending['admissions'][0]['state']=='pending'
    assert json.loads(failed.read_text())['commitments']==[]
    assert not budget.daily_assessment(pending,60,4.5,now)['allowed']

    # A short proposal cannot erase the next day of an older longer commitment.
    short_now=timestamp('2026-10-25T20:00:00-04:00')
    pending['admissions'][0]['admitted_at']=short_now
    pending['exposures']=[row('tomorrow',short_now+4*3600,short_now+4*3600+60,45)]
    rollover=budget.daily_assessment(pending,2,4.5,short_now)
    assert not rollover['allowed'] and rollover['days']['2026-10-26']['existing_usd']==105
    pending['exposures']=[row('open-scope-resource',short_now-1,None,1)]
    pending['exposures'][0]['bound_through']=short_now+3600
    assert not budget.daily_assessment(pending,2,4.5,short_now)['allowed'],'All open rows must cover the shared prior-commitment horizon'
    pending['admissions'][0]['admitted_at']=now;pending['exposures']=[]

    # Reconciliation remains an explicit attributed root input; old leaf unchanged.
    original_leaf=failed.read_bytes()
    pending['admissions'][0]['closed_exposure']=row('ignored',now,now+1,2.0,settled=3.0)
    reconciled=budget.daily_assessment(pending,20,4.5,now)
    assert reconciled['days']['2026-10-25']['existing_usd']==3
    assert reconciled['days']['2026-10-26']['existing_usd']==0
    assert failed.read_bytes()==original_leaf

    # An expired infrastructure cutoff is not evidence of actual cleanup.
    expired=deepcopy(recorded);item=leaf_state['commitments'][0]
    assert not budget.daily_assessment(expired,20,4.5,item['hard_stop_at']+1)['allowed']
    item['cleanup_verified_at']=item['reserved_at']+10;leaf.write_text(json.dumps(leaf_state))
    closed=budget.daily_assessment(expired,20,4.5,item['reserved_at']+20)
    assert closed['days'][budget.dates(item['reserved_at'],item['reserved_at'])[0]]['existing_usd']==60
    assert json.loads(leaf.read_text())['commitments'][0]['amount_usd']==60

    malformed=root/'wrong.json';malformed.write_text(json.dumps(state()))
    with patch('subprocess.run') as launch:
        rejected(lambda:budget.guarded_launch(malformed,root/'new-leaf',20,4.5,'x',['x','--budget-state',str(root/'other')]))
        launch.assert_not_called()

print('PASS: New York/DST dates, cross-midnight full holds and cleanup headroom, external/closed/unknown exposure, pending failure retention, explicit reconciliation without leaf edits, and eight concurrent real guard processes admit exactly one real legacy reservation; no cloud effects')
