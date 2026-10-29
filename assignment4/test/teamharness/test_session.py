import copy
import json
from receipt_fixture import original_receipt
import sqlite3
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from src.teamharness.session import Session, SessionBusy
from src.teamharness.provider import HTTPProvider, parse_completion
from src.teamharness.types import CompletionKind


def answer(text, reason="stop"):
    return {"choices": [{"finish_reason": reason, "message": {"role": "assistant", "content": text}}]}


def tools(*calls, reason="tool_calls"):
    return {"choices": [{"finish_reason": reason, "message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_" + str(i), "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        for i, (name, args) in enumerate(calls)]}}]}


class Script:
    model = "fixture"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(copy.deepcopy(request))
        if not self.responses:
            raise AssertionError("Unexpected model request")
        response = self.responses.pop(0)
        return response(request) if callable(response) else response


SHELL = {"type": "function", "function": {"name": "exec_command", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}}


class Sessions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.effects = []

    def tearDown(self):
        self.temp.cleanup()

    def executor(self, name, arguments, ident):
        self.effects.append(ident)
        result = subprocess.run(["/bin/bash", "-c", arguments["command"]], cwd=self.work, capture_output=True, text=True)
        return {"exit_code": result.returncode, "output": result.stdout, "stderr": result.stderr}

    def session(self, provider, **kwargs):
        return Session(self.root / "session", "implementer", "stable system", "task", provider, [SHELL], self.executor, self.work, **kwargs)

    def test_provider_failure_retains_actual_sqlite_extended_diagnostics(self):
        def fail(_request):
            database = sqlite3.connect(':memory:')
            try:
                database.execute('SELECT * FROM missing_provider_fixture')
            finally:
                database.close()
        session = self.session(Script([fail]))
        result = session.run()
        self.assertEqual('error', result['status'])
        error = result['provider_error']
        self.assertEqual(sqlite3.SQLITE_ERROR, error['sqlite_errorcode'])
        self.assertEqual('SQLITE_ERROR', error['sqlite_errorname'])
        self.assertIn('missing_provider_fixture', error['traceback'])
        database = sqlite3.connect(session.database)
        try:
            original = json.loads(database.execute("SELECT body FROM objects WHERE kind='model_transport_error'").fetchone()[0])
        finally:
            database.close()
        self.assertEqual(error['sqlite_errorcode'], original['sqlite_errorcode'])
        self.assertEqual(error['message'], original['message'])
        self.assertEqual(error['sqlite_errorname'], original['sqlite_errorname'])
        self.assertIn('missing_provider_fixture', original['traceback'])
        self.assertTrue(original['request_ref'].startswith('obj:'))

    def test_actual_file_execution_and_stable_prefix(self):
        p = Script([tools(("exec_command", {"command": "printf first > result.txt"})), tools(("exec_command", {"command": "cat result.txt"})), answer("done")])
        s = self.session(p)
        result = s.run()
        self.assertEqual("completed", result["status"])
        self.assertEqual("first", (self.work / "result.txt").read_text())
        for earlier, later in zip(p.requests, p.requests[1:]):
            self.assertEqual(earlier["messages"], later["messages"][:len(earlier["messages"])])
            self.assertEqual(earlier["tools"], later["tools"])
        self.assertEqual(2, len(self.effects))

    def test_truncated_tools_never_execute_and_partial_answer_never_finishes(self):
        p = Script([tools(("exec_command", {"command": "touch forbidden"}), reason="length"), answer("partial plan", "length"), answer("real answer")])
        result = self.session(p).run()
        self.assertEqual("real answer", result["report"])
        self.assertFalse((self.work / "forbidden").exists())
        self.assertEqual([], self.effects)

    def test_agent_compaction_actual_request_and_continuation(self):
        continuation = "Exact model-authored note: file result.txt exists; inspect it."
        p = Script([tools(("exec_command", {"command": "printf before > result.txt"})), tools(("compact", {})), answer(json.dumps({"continuation": continuation})), tools(("exec_command", {"command": "cat result.txt"})), answer("done")])
        result = self.session(p, metadata={"child": "existing-child"}).run()
        compact_request = p.requests[2]
        self.assertNotIn("tools", compact_request)
        self.assertNotIn("parallel_tool_calls", compact_request)
        self.assertNotIn("tool_choice", compact_request)
        self.assertIn("response_format", compact_request)
        self.assertEqual(p.requests[1]["messages"], compact_request["messages"][:len(p.requests[1]["messages"])])
        self.assertIn({"role": "assistant", "content": continuation}, p.requests[3]["messages"])
        self.assertIn({"role": "user", "content": "task"}, p.requests[3]["messages"])
        self.assertTrue(any("not new user requirements" in (m.get("content") or "") for m in p.requests[3]["messages"]))
        self.assertEqual(1, result["epoch"])
        saved = json.loads((self.root / "session/session.json").read_text())
        self.assertEqual("existing-child", saved["config"]["metadata"]["child"])

    def test_failed_external_compaction_preserves_history(self):
        p = Script([answer("not json")])
        s = self.session(p)
        before = copy.deepcopy(s.state)
        result = s.compact()
        self.assertEqual("compaction_failed", result["status"])
        self.assertEqual(before, s._load())

    def test_original_inputs_retained_by_default_and_model_can_omit_then_restore(self):
        original = 'Literal original public task: keep zero distinct from absent.'
        followup = 'Literal follow-up: preserve false too.'
        p = Script([tools(('compact', {})), answer('{"continuation":"An attributed model interpretation."}'),
                    tools(('compact', {'retain_inputs': False})), answer('{"continuation":"Working without original input text."}'),
                    tools(('exec_command', {'command': 'printf editable > result.txt'})),
                    tools(('compact', {'retain_inputs': True})), answer('{"continuation":"Restore literal inputs."}'), answer('done')])
        session = self.session(p, public_task=original)
        session.append_user(followup)
        session.run()
        default_users = [m['content'] for m in p.requests[2]['messages'] if m['role'] == 'user']
        self.assertEqual(default_users.count(original), 1)
        self.assertEqual(default_users.count(followup), 1)
        omitted_users = [m['content'] for m in p.requests[4]['messages'] if m['role'] == 'user']
        self.assertNotIn(original, omitted_users)
        self.assertNotIn(followup, omitted_users)
        restored_users = [m['content'] for m in p.requests[7]['messages'] if m['role'] == 'user']
        self.assertEqual(restored_users.count(original), 1)
        self.assertEqual(restored_users.count(followup), 1)
        self.assertEqual((self.work / 'result.txt').read_text(), 'editable')
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(session._load()['epoch'], 3)

    def test_operator_compaction_same_no_tools_path(self):
        p = Script([answer('{"continuation":"operator-requested summary"}'), answer("done")])
        s = self.session(p)
        s.request_compaction(identity="external-one")
        s.request_compaction(identity="external-one")
        s.run()
        self.assertEqual(2, len(p.requests))
        self.assertNotIn("tools", p.requests[0])
        self.assertIn({"role": "assistant", "content": "operator-requested summary"}, p.requests[1]["messages"])

    def test_failed_queued_compaction_reports_result_to_agent(self):
        p = Script([answer("invalid summary"), answer("continued after failure")])
        s = self.session(p)
        s.request_compaction()
        result = s.run()
        self.assertEqual("continued after failure", result["report"])
        self.assertIn("compaction_failed", p.requests[1]["messages"][-1]["content"])
        self.assertEqual(0, result["epoch"])

    def test_followup_after_stop_resumes_but_later_stop_wins(self):
        p = Script([answer("handled follow-up")])
        s = self.session(p)
        s.request_stop()
        s.append_user("Continue with this follow-up")
        self.assertEqual("handled follow-up", s.run()["report"])
        s.append_user("Another follow-up")
        s.request_stop()
        self.assertEqual("stopped", s.run()["status"])
        self.assertEqual(1, len(p.requests))

    def test_stop_and_followup_during_inference_discards_old_response_then_resumes(self):
        entered, release = threading.Event(), threading.Event()
        def response(_):
            entered.set()
            release.wait(5)
            return tools(("exec_command", {"command": "touch forbidden"}))
        p = Script([response, answer("handled replacement instruction")])
        s = self.session(p)
        result = []
        t = threading.Thread(target=lambda: result.append(s.run()))
        t.start()
        self.assertTrue(entered.wait(3))
        s.request_stop()
        s.append_user("Continue, but inspect instead of creating anything")
        release.set()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertFalse((self.work / "forbidden").exists())
        self.assertEqual("handled replacement instruction", result[0]["report"])

    def test_resume_uncertain_effect_does_not_replay(self):
        p = Script([answer("observed uncertain result")])
        s = self.session(p)
        state = s._load()
        state["messages"].append(tools(("exec_command", {"command": "printf repeat >> result.txt"}))["choices"][0]["message"])
        state["pending"] = {"calls": [{"id": "call_0", "name": "exec_command", "args": {"command": "printf repeat >> result.txt"}, "effect_id": "reserved"}], "index": 0, "started": True}
        state["status"] = "running"
        s._save(state)
        (self.work / "result.txt").write_text("once")
        resumed = self.session(p)
        resumed.run()
        self.assertEqual("once", (self.work / "result.txt").read_text())
        self.assertEqual([], self.effects)
        self.assertEqual(original_receipt(p.requests[0]['messages'][-1]['content'])['state'], 'unknown')

    def test_stop_during_inference_prevents_action(self):
        entered, release = threading.Event(), threading.Event()
        def response(_):
            entered.set()
            release.wait(5)
            return tools(("exec_command", {"command": "touch forbidden"}))
        s = self.session(Script([response]))
        result = []
        t = threading.Thread(target=lambda: result.append(s.run()))
        t.start()
        self.assertTrue(entered.wait(3))
        with self.assertRaises(SessionBusy):
            s.run()
        # A separately constructed control handle can reach an active session.
        self.session(None).request_stop()
        release.set()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual("stopped", result[0]["status"])
        self.assertFalse((self.work / "forbidden").exists())

    def test_finish_role_reports_claim_not_grade(self):
        p = Script([tools(("finish_role", {"outcome": "changes_requested", "report": "Checkbox lacks the requested attribute"}))])
        result = self.session(p).run()
        self.assertEqual("changes_requested", result["outcome"])
        self.assertNotIn("passed", result)

    def test_mixed_finish_control_rejected_as_not_executed(self):
        p = Script([tools(("finish_role", {"outcome": "complete", "report": "done"}),
                          ("exec_command", {"command": "printf real > result.txt"})), answer("later report")])
        result = self.session(p).run()
        self.assertEqual("later report", result["report"])
        rejected = next(m for m in p.requests[1]["messages"] if m.get("tool_call_id") == "call_0")
        receipt = original_receipt(rejected["content"])
        self.assertFalse(receipt["executed"])
        self.assertEqual("not_executed", receipt["state"])
        self.assertEqual("real", (self.work / "result.txt").read_text())

    def test_plain_prose_is_not_approval(self):
        result = self.session(Script([answer("Found a serious defect")])).run()
        self.assertEqual("completed", result["status"])
        self.assertIsNone(result["outcome"])

    def test_stop_between_tools_skips_remaining_effects(self):
        p = Script([tools(("exec_command", {"command": "printf first > result.txt"}),
                          ("exec_command", {"command": "touch forbidden"}))])
        s = self.session(p)
        s.reconfigure(reason='Exercise legacy ordered execution', tool_execution='ordered')
        ordinary = s.executor
        def stop_after_first(*args):
            result = ordinary(*args)
            s.request_stop()
            return result
        s.executor = stop_after_first
        result = s.run()
        self.assertEqual("stopped", result["status"])
        self.assertEqual(1, len(self.effects))
        self.assertFalse((self.work / "forbidden").exists())
        self.assertEqual(original_receipt(s._load()['messages'][-1]['content'])['state'], 'not_executed')

    def test_truncated_compaction_json_is_rejected(self):
        s = self.session(Script([answer('{"continuation":"looks complete"}', "length")]))
        before = s._load()
        self.assertEqual("compaction_failed", s.compact()["status"])
        self.assertEqual(before, s._load())

    def test_reasoning_fields_preserved_and_refusal_distinct(self):
        raw = answer("done")
        raw["choices"][0]["message"]["reasoning_details"] = [{"type": "opaque", "data": "token"}]
        parsed = parse_completion(raw)
        self.assertEqual(raw["choices"][0]["message"], parsed.message)
        self.assertEqual(CompletionKind.REFUSAL, parse_completion(answer("cannot", "content_filter")).kind)

    def test_provider_configuration_freezes_without_secrets(self):
        env = {"A4_MODEL": "model-one", "A4_BASE_URL": "http://127.0.0.1:9999/v1", "A4_API_KEY": "never-persist-this", "A4_API_PROTOCOL": "chat_completions"}
        with patch.dict("os.environ", env):
            provider = HTTPProvider(self.root / "transport")
            saved = (provider.root / "provider-config.json").read_text()
            self.assertNotIn("never-persist-this", saved)
            self.assertEqual("model-one", json.loads(saved)["model"])
            with patch.dict("os.environ", {"A4_MODEL": "model-two"}):
                with self.assertRaisesRegex(ValueError, "Provider configuration changed"):
                    HTTPProvider(provider.root)
            with patch.dict("os.environ", {"A4_BASE_URL": "http://127.0.0.1:8888/v1"}):
                with self.assertRaisesRegex(ValueError, "Provider configuration changed"):
                    provider.complete({"model": "model-one"})


if __name__ == "__main__":
    unittest.main()
