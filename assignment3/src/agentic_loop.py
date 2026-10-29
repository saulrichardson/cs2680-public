"""The course-required Agent(dispatcher).run() entry point."""
from __future__ import annotations

import os
import http.client
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from .run_control import RunControl
from .course import solve
from .models import discover_profiles, AccountingError


# One-based leaderboard task numbers; dispatcher task indices are zero-based.
# An empty set disables this policy. Never use worker or completion order.
SKIP_TASK_NUMBERS = frozenset({2, 4, 9, 10})


class Agent:
    def __init__(self, dispatcher):
        self.dispatcher = dispatcher

    def run(self):
        workers = int(os.environ.get("CS2680_TASK_WORKERS", "5"))
        if not 1 <= workers <= 5:
            raise ValueError("CS2680_TASK_WORKERS must be between 1 and 5")
        control = RunControl(Path('madsOpt_logs/expressive') / f'run-{time.time_ns()}-budget.json',
                             skipped_task_numbers=SKIP_TASK_NUMBERS)
        # Discovery performs only read-only metadata requests. A temporary
        # network or service failure can be retried before any task is opened;
        # inference, grading and repository effects use their own owners.
        for attempt in range(3):
            try:
                profiles = discover_profiles(os.environ['CS2680_BASE_URL'], os.environ['CS2680_API_KEY'], artifact_dir='madsOpt_logs/expressive/provider')
            except urllib.error.HTTPError as error:
                if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                    raise
                error.close()
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead):
                if attempt == 2:
                    raise
            else:
                break
            time.sleep(2 ** attempt)
        active, exhausted, failure = set(), False, None

        def investigate(task):
            try:
                return solve(self.dispatcher, task, profiles, run_control=control)
            except BaseException as error:
                import dispatcher as course_dispatcher
                shutdown = getattr(course_dispatcher, 'DispatcherShutdown', ())
                if (not isinstance(error, Exception) or isinstance(error, AccountingError)
                        or isinstance(error, shutdown)):
                    control.stop(f'Task {task["task"]}: {type(error).__name__}: {error}')
                # Task-local unknown effects remain retained by solve/finalization.
                # The coordinator reports the error after healthy siblings finish.
                raise
            finally:
                control.task_completed(task["task"])

        # Only this coordinator opens tasks and initializes their course logger.
        # Workers own separate repositories, gateways, grading and cleanup.
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='task') as pool:
            try:
                while True:
                    done = {future for future in active if future.done()}
                    for future in done:
                        active.remove(future)
                        try:
                            future.result()
                        except BaseException as error:
                            if failure is None:
                                failure = error
                    while not exhausted and len(active) < workers and control.can_open:
                        try:
                            task = self.dispatcher.next_task()
                        except Exception as error:
                            from dispatcher import DispatcherError
                            if (isinstance(error, DispatcherError) and active
                                    and str(error).startswith('too many live tasks (')):
                                break
                            raise
                        if task is None:
                            exhausted = True
                            break
                        control.task_opened(task["task"])
                        if task["task"] + 1 in SKIP_TASK_NUMBERS:
                            receipt = self.dispatcher.done(task["task"], reason="policy_skip", iterations=0)
                            if not isinstance(receipt, dict) or receipt.get("ok") is not True:
                                raise RuntimeError(f"Task {task['task']}: policy skip finalization was not confirmed")
                            control.task_completed(task["task"])
                            continue
                        active.add(pool.submit(investigate, task))
                    if not active:
                        break
                    wait(active, timeout=.1, return_when=FIRST_COMPLETED)
            except BaseException as error:
                control.stop(f'Coordinator: {type(error).__name__}: {error}')
                failure = error
            finally:
                # Do not leave active task owners behind when Agent.run returns.
                for future in active:
                    try:
                        future.result()
                    except BaseException as error:
                        if failure is None:
                            failure = error
        if failure is not None:
            raise failure
