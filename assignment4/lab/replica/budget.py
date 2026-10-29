"""Rental commitments and opt-in daily admission; estimates are not invoices."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import fcntl
import json
import math
import time
import uuid
from zoneinfo import ZoneInfo

NEW_YORK=ZoneInfo('America/New_York')

def save(path,data):
    path=Path(path);temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temp.open('w') as out:
        json.dump(data,out,indent=2);out.write('\n');out.flush()
        import os
        os.fsync(out.fileno())
    temp.replace(path)

@contextmanager
def ledger(path):
    path=Path(path)
    with path.with_suffix(path.suffix+'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        data=json.loads(path.read_text())
        yield data
        save(path,data)

def positive(value):
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value<=0:raise ValueError('Positive finite monetary value required')
    return float(value)

def reserve(path,run,amount=None,hourly_ceiling=4.5,now=None,scope=None):
    if scope is not None and (not isinstance(scope,str) or not scope.strip()):raise ValueError('Nonempty descriptive spending scope required')
    rate=positive(hourly_ceiling);now=time.time() if now is None else now
    with ledger(path) as state:
        amount=positive(state.get('campaign_commitment_usd',4.5) if amount is None else amount)
        cap=positive(state['total_additional_budget_usd']);settled=state['settled_usd']
        if not isinstance(settled,(int,float)) or not math.isfinite(settled) or settled<0:raise ValueError('Invalid settled spending')
        committed=sum(positive(x['amount_usd']) for x in state['commitments'])
        if any(x['run']==str(Path(run).resolve()) for x in state['commitments']):raise ValueError('Campaign already has a commitment')
        # Unknown exposure stays inside its original commitment; never refund on an estimate.
        if settled+committed+amount>cap+1e-9:raise ValueError('Cumulative paid budget cannot admit this commitment')
        if amount<=1:raise ValueError('Commitment must leave collection and termination headroom')
        value={'id':uuid.uuid4().hex,'run':str(Path(run).resolve()),'amount_usd':amount,'hourly_ceiling_usd':rate,'reserved_at':now,
               'soft_stop_at':now+(amount-1)*3600/rate,'hard_stop_at':now+(amount-.5)*3600/rate,
               'scope':scope or 'EC2 pair plus storage/IOPS/throughput and ordinary small evidence transfer; no paid external API',
               'billing_state':'unreconciled','estimated_exposure_usd':0.0}
        state['commitments'].append(value);state['campaigns'].append(str(Path(run).resolve()));state['phase']='Budget reserved; independent watchdog must be armed before allocation'
    return value

def observe(path,ident,now=None,cleaned=False):
    now=time.time() if now is None else now
    with ledger(path) as state:
        item=next(x for x in state['commitments'] if x['id']==ident)
        if 'cleanup_verified_at' not in item:
            item['estimated_exposure_usd']=max(0,now-item['reserved_at'])*item['hourly_ceiling_usd']/3600
            if cleaned:item['cleanup_verified_at']=now
        state['estimated_accrued_usd']=sum(x['estimated_exposure_usd'] for x in state['commitments'])
        if cleaned:state['phase']='Owned cleanup verified; cost remains an estimate and commitment retained pending billing'
    return item


def nonnegative(value):
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value<0:
        raise ValueError('Nonnegative finite value required')
    return float(value)

def dates(start,end):
    """Inclusive civil dates: even an exact midnight cleanup touches that date."""
    start=nonnegative(start);end=nonnegative(end)
    if end<start:raise ValueError('Reversed exposure interval')
    day=datetime.fromtimestamp(start,NEW_YORK).date();last=datetime.fromtimestamp(end,NEW_YORK).date()
    result=[]
    while day<=last:
        result.append(day.isoformat());day+=timedelta(days=1)
    return result

def exposure(row,horizon):
    """One attributable liability; overlapping cost views are maxima, not sums."""
    if not row.get('id') or not row.get('source') or not row.get('evidence'):
        raise ValueError('Daily exposure requires identity, source and evidence')
    if row.get('status') not in ('open','closed'):raise ValueError('Exposure status must be open or closed')
    start=nonnegative(row['started_at'])
    end=nonnegative(row['closed_at']) if row['status']=='closed' else max(start,horizon)
    # A null bound is deliberate uncertainty, never zero or a reason to use an estimate.
    bound=row.get('conservative_cost_bound_usd')
    coverage=row.get('bound_through')
    if row['status']=='open' and (coverage is None or nonnegative(coverage)<max(start,horizon)):
        bound=None # Accrued spend alone cannot bound continuing external resources.
    settled=nonnegative(row['settled_usd']);held=nonnegative(row.get('held_usd',0))
    historical=nonnegative(row.get('historical_commitment_usd',0))
    if row['status']=='closed' and held:raise ValueError('Closed exposure must separate historical holds from pending liability')
    total=None if bound is None else max(nonnegative(bound),settled,held)
    return dict(row,dates=dates(start,end),admission_charge_usd=total,
                historical_commitment_usd=historical)

def leaf_commitment(path,amount,rate,allow_empty=False):
    """Read the one frozen-launcher reservation without making another reservation."""
    state=json.loads(Path(path).read_text())
    if state.get('settled_usd')!=0 or state.get('total_additional_budget_usd')!=amount or state.get('campaign_commitment_usd')!=amount:
        raise ValueError('Daily guard requires an unchanged dedicated legacy campaign ledger')
    values=state.get('commitments');campaigns=state.get('campaigns')
    if values==[] and campaigns==[] and allow_empty:return None
    if not isinstance(values,list) or len(values)!=1:raise ValueError('Expected exactly one frozen-launcher commitment')
    item=values[0]
    if item['amount_usd']!=amount or item['hourly_ceiling_usd']!=rate or campaigns!=[item['run']]:
        raise ValueError('Frozen-launcher commitment does not match daily admission')
    start=nonnegative(item['reserved_at'])
    if abs(item['soft_stop_at']-(start+(amount-1)*3600/rate))>1e-6 or abs(item['hard_stop_at']-(start+(amount-.5)*3600/rate))>1e-6:
        raise ValueError('Frozen-launcher collection/termination headroom changed')
    return item

def daily_assessment(state,amount,hourly_ceiling,now=None):
    """Pure accounting apart from read-only leaf receipts; never settles old holds.

    Root supplies complete bounds for the explicitly authorized budget scope.
    Whole liabilities are charged on each touched day, without estimated refunds.
    """
    now=time.time() if now is None else nonnegative(now)
    amount=positive(amount);rate=positive(hourly_ceiling)
    if amount<=1:raise ValueError('Commitment must leave collection and termination headroom')
    if state.get('mode')!='daily' or state.get('timezone')!='America/New_York':
        raise ValueError('Explicit daily America/New_York ledger required')
    cap=positive(state['daily_limit_usd'])
    if not state.get('budget_scope') or state.get('scope_complete') is not True or state.get('unbounded_unknowns')!=[]:
        raise ValueError('Declared budget-scope exposure is incomplete or unbounded')
    proposed_end=now+amount*3600/rate # Includes the full commitment, including cleanup headroom.
    horizon=proposed_end
    for row in state['exposures']:
        horizon=max(horizon,nonnegative(row['started_at']))
        if row.get('status')=='closed':horizon=max(horizon,nonnegative(row['closed_at']))
    prior=[]
    for entry in state['admissions']:
        item=leaf_commitment(entry['campaign_ledger'],entry['amount_usd'],entry['hourly_ceiling_usd'],allow_empty=True)
        prior.append((entry,item))
        if entry.get('closed_exposure'):
            horizon=max(horizon,nonnegative(entry['closed_exposure']['closed_at']))
        else:
            started=entry['admitted_at'] if item is None else item['reserved_at']
            end=None if item is None else item.get('cleanup_verified_at')
            horizon=max(horizon,nonnegative(end) if end is not None else
                        nonnegative(started)+positive(entry['amount_usd'])*3600/positive(entry['hourly_ceiling_usd']))
    liabilities=[]
    for row in state['exposures']:liabilities.append(exposure(row,horizon))
    for entry,item in prior:
        closed=entry.get('closed_exposure')
        if closed:
            if closed.get('status')!='closed':raise ValueError('Admission reconciliation must be a closed exposure')
            row=exposure(dict(closed,id=entry['id'],source=entry['source'],historical_commitment_usd=entry['amount_usd']),horizon)
        else:
            started=entry['admitted_at'] if item is None else item['reserved_at']
            cleaned=None if item is None else item.get('cleanup_verified_at')
            # Cleanup bounds dates, but it does not refund the original hold.
            bound=max(entry['amount_usd'],0 if item is None else nonnegative(item.get('estimated_exposure_usd',0)))
            if item is not None and cleaned is None:
                bound=max(bound,max(0,now-started)*entry['hourly_ceiling_usd']/3600)
                if now>item['hard_stop_at']:bound=None # A cutoff is not proof of actual cleanup.
            row=exposure({'id':entry['id'],'source':entry['source'],'evidence':[entry['campaign_ledger']],
                'status':'open','started_at':started,'held_usd':entry['amount_usd'],
                'conservative_cost_bound_usd':bound,'settled_usd':0,'bound_through':horizon},horizon)
            if cleaned is not None:row['dates']=dates(started,cleaned)
        row['campaign_ledger']=entry['campaign_ledger'];liabilities.append(row)
    ids=[x['id'] for x in liabilities]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate daily liability identity')
    # The durable pending hold has no automatic expiry. Include every already
    # known future exposure date, even if launcher dispatch is delayed.
    days=dates(now,horizon)
    totals={};allowed=True
    for day in days:
        relevant=[x for x in liabilities if day in x['dates']]
        unknown=[x['id'] for x in relevant if x['admission_charge_usd'] is None]
        before=sum(x['admission_charge_usd'] or 0 for x in relevant)
        totals[day]={'existing_usd':before,'proposed_usd':amount,'total_usd':before+amount,
                     'limit_usd':cap,'liabilities':[x['id'] for x in relevant],'unknown_liabilities':unknown}
        allowed=allowed and not unknown and before+amount<=cap+1e-9
    return {'allowed':allowed,'timezone':'America/New_York','budget_scope':state['budget_scope'],'assessed_at':now,
            'proposed_cleanup_envelope_end':proposed_end,'assessment_horizon':horizon,'days':totals,'liabilities':liabilities,
            'policy':'Full commitment on every touched date; max overlapping settled/bound/held views per liability; no estimated refunds'}

def guarded_launch(account,campaign_ledger,amount,hourly_ceiling,source,command,now=None):
    """Serialize admission and one exact frozen launch; retain uncertainty on error."""
    import subprocess
    account=Path(account).resolve();leaf=Path(campaign_ledger).resolve()
    amount=positive(amount);rate=positive(hourly_ceiling)
    if leaf==account:raise ValueError('Account ledger and campaign leaf must differ')
    if command.count('--budget-state')!=1:raise ValueError('Exact launcher must have one --budget-state argument')
    index=command.index('--budget-state')
    if index+1>=len(command) or Path(command[index+1]).resolve()!=leaf:
        raise ValueError('Launcher budget argument must name the guarded leaf')
    if any(x.startswith('--budget-state=') for x in command):raise ValueError('Duplicate budget argument')
    if not source:raise ValueError('Attributable launch source required')
    with ledger(account) as state:
        if any(Path(x['campaign_ledger']).resolve()==leaf for x in state['admissions']):
            raise ValueError('Campaign leaf already admitted; reconcile instead of launching twice')
        check=daily_assessment(state,amount,rate,now)
        if not check['allowed']:raise ValueError('Daily AWS envelope cannot admit this commitment: '+json.dumps(check['days']))
        if leaf.exists():leaf_commitment(leaf,amount,rate,allow_empty=True)
        else:
            leaf.parent.mkdir(parents=True,exist_ok=True)
            with leaf.open('x') as out:
                json.dump({'total_additional_budget_usd':amount,'campaign_commitment_usd':amount,
                           'settled_usd':0,'commitments':[],'campaigns':[]},out,indent=2);out.write('\n')
        if leaf_commitment(leaf,amount,rate,allow_empty=True) is not None:
            raise ValueError('Guard cannot launch from an already reserved campaign ledger')
        entry={'id':uuid.uuid4().hex,'source':source,'campaign_ledger':str(leaf),'amount_usd':amount,
               'hourly_ceiling_usd':rate,'admitted_at':check['assessed_at'],'state':'pending',
               'command':command,'assessment':check}
        state['admissions'].append(entry);save(account,state) # Durable before launching, including abrupt guard death.
        try:
            result=subprocess.run(command);entry['launcher_exit_code']=result.returncode
            item=leaf_commitment(leaf,amount,rate,allow_empty=True)
            if item is not None:
                entry['state']='reserved';entry['commitment_id']=item['id'];entry['run']=item['run']
            elif result.returncode==0:raise RuntimeError('Launcher returned success without the guarded reservation')
            return result.returncode
        except BaseException as error:
            entry['launch_error']=type(error).__name__+': '+str(error)
            raise
        finally:
            # No automatic release on failure, interruption, estimate, or missing receipt.
            save(account,state)

if __name__=='__main__':
    import argparse,sys
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    guard=sub.add_parser('guard');guard.add_argument('--account',type=Path,required=True)
    guard.add_argument('--campaign-ledger',type=Path,required=True);guard.add_argument('--amount',type=float,required=True)
    guard.add_argument('--hourly-ceiling',type=float,default=4.5);guard.add_argument('--source',required=True)
    guard.add_argument('command',nargs=argparse.REMAINDER);args=parser.parse_args()
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    if not command:parser.error('An exact frozen launcher command is required after --')
    sys.exit(guarded_launch(args.account,args.campaign_ledger,args.amount,args.hourly_ceiling,args.source,command))
