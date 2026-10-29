"""Shared task allocation, exact accounting and budget admission."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN
import json
import os
from pathlib import Path
import threading
import time


class RunControlError(RuntimeError):
    pass


class RunHalted(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    """A task allowance or declared submission resource has been exhausted."""
    pass


def amount(value):
    if isinstance(value, bool):
        raise RunControlError('Invalid shared budget amount')
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise RunControlError('Invalid shared budget amount')
    return result


def parse_run_resources(raw=None):
    """Validate declared submission resources used by accounting and admission."""
    if raw is None:
        value = {'budget_usd': '10', 'time_limit_seconds': 14400,
                 'task_count': 26, 'basis': 'course_contract'}
    else:
        try:
            value = json.loads(raw, parse_float=Decimal) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            raise RunControlError('CS2680_RUN_RESOURCES must be a JSON object') from None
    fields = {'budget_usd', 'time_limit_seconds', 'task_count', 'basis'}
    if not isinstance(value, dict) or set(value) != fields:
        raise RunControlError('CS2680_RUN_RESOURCES requires exactly budget_usd, time_limit_seconds, task_count, and basis')
    if value['basis'] not in ('course_contract', 'development_launcher'):
        raise RunControlError('CS2680_RUN_RESOURCES basis must be course_contract or development_launcher')
    budget = value['budget_usd']
    if budget is not None:
        try:
            budget = str(amount(budget))
        except (InvalidOperation, TypeError, ValueError, RunControlError):
            raise RunControlError('CS2680_RUN_RESOURCES budget_usd must be a finite nonnegative decimal or null') from None
    seconds, tasks = value['time_limit_seconds'], value['task_count']
    if seconds is not None and (type(seconds) is not int or seconds <= 0):
        raise RunControlError('CS2680_RUN_RESOURCES time_limit_seconds must be a positive integer or null')
    if type(tasks) is not int or tasks < 0:
        raise RunControlError('CS2680_RUN_RESOURCES task_count must be a nonnegative integer')
    return {'budget_usd': budget, 'time_limit_seconds': seconds,
            'task_count': tasks, 'basis': value['basis']}


class AllocationExceeded(BudgetExceeded):
    pass


class RunControl:
    def __init__(self, path, *, resources=None, skipped_task_numbers=()):
        self.path = Path(path)
        self.resources = parse_run_resources(os.environ.get('CS2680_RUN_RESOURCES') if resources is None else resources)
        self.limit = None if self.resources["budget_usd"] is None else amount(self.resources["budget_usd"])
        # Use the full declared budget. Already-admitted responses can overrun;
        # their reservations record exposure without withholding spendable funds.
        self.admission_limit = self.limit
        self.skipped_task_ids = frozenset(str(k - 1) for k in skipped_task_numbers
                                         if 1 <= k <= self.resources['task_count'])
        self.baseline_allowance = (None if self.limit is None else
                                   min(Decimal('1.10'), self.admission_limit))
        self.tasks = {}
        self._started_at = None
        self._tasks_opened = 0
        self._tasks_completed = 0
        self.condition = threading.Condition(threading.RLock())
        self.stop_event = threading.Event()
        self.stop_reason = None
        self.admission_closed = False
        self.rows = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raise RunControlError('Refusing to overwrite an existing submission ledger')
        self._save()

    def _charged(self):
        """Settled costs plus the full reservation of every call whose cost is unknown.

        An uncertain call is charged at its reservation, the most it could cost, so
        spending limits stay conservative without halting the whole submission.
        """
        spent, _ = self._totals()
        return spent + sum((r['reservation'] for r in self.rows.values() if r['state'] == 'unknown'), Decimal(0))

    def _totals(self):
        spent = sum((r['cost'] for r in self.rows.values() if r['state'] == 'settled'), Decimal(0))
        reserved = sum((r['reservation'] for r in self.rows.values() if r['state'] in ('inflight', 'unknown')), Decimal(0))
        return spent, reserved

    def _allocation(self):
        """Total entitlement for unfinished tasks, not their remaining spending.

        Spending by an active task cannot increase its own ceiling. Only closed
        tasks and unattributed calls reduce this pool. Pending closed-task calls
        remain charged at their full quotes until a receipt establishes savings.
        The baseline is permission, not a reservation or a funding guarantee.
        """
        unfinished = self.resources['task_count'] - len(self.skipped_task_ids) - sum(
            task['completed'] for key, task in self.tasks.items() if key not in self.skipped_task_ids)
        if self.limit is None:
            return None, unfinished
        closed_cost = sum((row['cost'] if row['state'] == 'settled' else row['reservation']
                           for row in self.rows.values()
                           if row['state'] in ('settled', 'inflight', 'unknown')
                           and (row['task_id'] is None or self.tasks[row['task_id']]['completed'])), Decimal(0))
        return max(Decimal(0), self.admission_limit - closed_cost), unfinished

    def _raise_allowances(self):
        pool, unfinished = self._allocation()
        if pool is None or not unfinished:
            return
        # Round the fair share down so a raised ceiling never exceeds it and
        # the model-facing allowance reads as whole cents, like the baseline.
        ceiling = max(self.baseline_allowance, (pool / unfinished).quantize(Decimal('0.01'), rounding=ROUND_DOWN))
        for key, task in self.tasks.items():
            if not task['completed'] and key not in self.skipped_task_ids:
                task['allowance'] = max(task['allowance'], ceiling)

    def snapshot(self):
        with self.condition:
            spent, reserved = self._totals()
            budget = self.resources['budget_usd']
            # Platform timing is accounting metadata only, never local admission.
            seconds = self.resources['time_limit_seconds']
            elapsed = None if self._started_at is None else max(0., time.monotonic() - self._started_at)
            pool, unfinished = self._allocation()
            charged = self._charged()
            facts = {**self.resources, 'settled_usd': str(spent),
                     'headroom_usd': None if budget is None else str(max(Decimal(0), Decimal(budget) - spent)),
                     'admission_limit_usd': None if self.admission_limit is None else str(self.admission_limit),
                     'admission_headroom_usd': (None if self.admission_limit is None
                                                else str(max(Decimal(0), self.admission_limit - charged))),
                     'clock_basis': 'first_task_delivery_approximation',
                     'elapsed_seconds': elapsed,
                     'remaining_seconds': None if elapsed is None or seconds is None else max(0., seconds - elapsed),
                     'tasks_opened': self._tasks_opened, 'tasks_completed': self._tasks_completed}
            return {'limit': None if self.limit is None else float(self.limit),
                    'spent': float(spent), 'reserved': float(reserved),
                    'remaining': None if self.limit is None else float(max(Decimal(0), self.limit - spent)),
                    'inflight': sum(r['state'] == 'inflight' for r in self.rows.values()),
                    'unknown': sum(r['state'] == 'unknown' for r in self.rows.values()),
                    'admission_closed': self.admission_closed, 'stop_reason': self.stop_reason,
                    'resource_facts': facts, 'tasks': {k: {field: str(value) if isinstance(value, Decimal) else value
                        for field, value in task.items()} for k, task in self.tasks.items()},
                    'allocation_pool_usd': None if pool is None else str(pool),
                    'unfinished_task_count': unfinished,
                    'baseline_allowance_usd': None if self.baseline_allowance is None else str(self.baseline_allowance)}

    def task_opened(self, task_id=None):
        """Register an acquired task, even if admission closed during next_task().

        The coordinator checks can_open before acquisition. Once delivered, the
        worker must own cleanup; its inference still checks current admission.
        """
        with self.condition:
            task_id = str(self._tasks_opened if task_id is None else task_id)
            if task_id in self.tasks:
                raise RunControlError('Task already allocated')
            remaining = self.resources['task_count'] - self._tasks_opened
            if remaining <= 0:
                raise RunControlError('Task count exceeds declared submission')
            now = time.monotonic()
            if self._started_at is None:
                self._started_at = now
            allowance = Decimal(0) if task_id in self.skipped_task_ids else self.baseline_allowance
            self.tasks[task_id] = {'allowance': allowance, 'spent': Decimal(0),
                                   'started': now, 'completed': False}
            self._tasks_opened += 1
            self._raise_allowances()
            self._persist()
            return task_id

    def task_completed(self, task_id=None):
        with self.condition:
            if task_id is None:
                active = [k for k, v in self.tasks.items() if not v['completed']]
                if len(active) != 1:
                    raise RunControlError('Completion requires the task identity')
                task_id = active[0]
            task = self.tasks[str(task_id)]
            if task['completed']:
                raise RunControlError('Task already completed')
            task['completed'] = True
            self._tasks_completed += 1
            self._raise_allowances()
            self._persist()

    def task_state(self, task_id):
        with self.condition:
            task = self.tasks[str(task_id)]
            return {'allowance_usd': None if task['allowance'] is None else str(task['allowance']),
                    'spent_usd': str(task['spent']),
                    'remaining_usd': None if task['allowance'] is None else str(max(Decimal(0), task['allowance'] - task['spent'])),
                    'completed': task['completed']}

    def task_stop_reason(self, task_id):
        with self.condition:
            if self.stop_event.is_set():
                return self.stop_reason or 'Submission stopping'
            spent = self._charged()
            if self.admission_limit is not None and spent >= self.admission_limit:
                return 'Shared submission spending exhausted'
            task = self.tasks[str(task_id)]
            if task['completed']:
                return 'Task already finalized'
            if task['allowance'] is not None and task['spent'] >= task['allowance']:
                return 'Task spending allowance exhausted'
            return None

    def require_task(self, task_id):
        reason = self.task_stop_reason(task_id)
        if reason:
            raise AllocationExceeded(reason)

    def _persist(self):
        try:
            self._save()
        except Exception as error:
            self._failed(error)

    def _save(self):
        record = {**self.snapshot(), 'attempts': self.rows}
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(record, indent=2, default=str, allow_nan=False) + '\n')
        os.replace(temporary, self.path)

    def _failed(self, error):
        self.stop_reason = f'Shared accounting persistence failed: {type(error).__name__}: {error}'
        self.stop_event.set()
        self.admission_closed = True
        self.condition.notify_all()
        raise RunControlError(self.stop_reason) from error

    def require_open(self):
        with self.condition:
            if self.stop_event.is_set():
                raise RunHalted(self.stop_reason or 'Submission stopping')

    @property
    def can_open(self):
        with self.condition:
            spent = self._charged()
            pool, _ = self._allocation()
            return (not self.stop_event.is_set() and not self.admission_closed
                    and self._tasks_opened < self.resources['task_count']
                    and (self.limit is None or spent < self.admission_limit and pool > 0))

    def reserve(self, key, quote, *, task_id=None):
        """Record a finite quote(None), independently of spending or other holds."""
        with self.condition:
            if key in self.rows:
                raise RunControlError('Duplicate shared reservation identity')
            self.require_open()
            if task_id is not None:
                self.require_task(task_id)
            elif self.admission_limit is not None and self._charged() >= self.admission_limit:
                raise AllocationExceeded('Shared submission spending exhausted')
            proposed = quote(None)
            if proposed is None:
                raise RunControlError('Missing accounting quote; no inference started')
            cost, payload = proposed
            cost = amount(cost)
            self.rows[key] = {'state': 'inflight', 'reservation': cost, 'created': time.time(),
                              'task_id': None if task_id is None else str(task_id)}
            try:
                self._save()
            except Exception as error:
                self.rows[key]['state'] = 'not_started'
                self.rows[key]['error'] = str(error)
                self._failed(error)
            return key, payload

    def settle(self, token, cost):
        with self.condition:
            row = self.rows[token]
            if row['state'] != 'inflight':
                raise RunControlError('Shared reservation is already terminal')
            if cost is None:
                row['state'] = 'unknown'
                if row.get('task_id') is not None:
                    self.tasks[row['task_id']]['spent'] += row['reservation']
            else:
                cost = amount(cost)
                row.update(state='settled', cost=cost)
                if row.get('task_id') is not None:
                    task = self.tasks[row['task_id']]
                    task['spent'] += cost
                if cost > row['reservation'] + Decimal('0.000000001'):
                    row['reservation_variance'] = cost - row['reservation']
            self._raise_allowances()
            try:
                self._save()
            except Exception as error:
                self._failed(error)
            finally:
                self.condition.notify_all()

    def stop(self, reason):
        with self.condition:
            if not self.stop_event.is_set():
                self.stop_reason = str(reason)
            self.stop_event.set()
            self.admission_closed = True
            try:
                self._save()
            except Exception:
                pass  # memory still prevents new effects; earlier receipts remain
            self.condition.notify_all()
