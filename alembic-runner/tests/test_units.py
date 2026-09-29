import json
import stat
import tempfile
import unittest
from pathlib import Path

from alembic_runner import (
    ACTIVE,
    TERMINAL,
    ApplyReport,
    Backend,
    BackendError,
    DocumentError,
    DriftReport,
    Plan,
    RunStatus,
    StateStore,
    TransitionError,
    Workspace,
    WorkspaceError,
    check_transition,
    failure_status,
    plugin_names,
    token_env,
)

S = RunStatus


def plan_bytes(ops, schema_preview=None):
    doc = {"schema": {"types": {}}, "ops": ops}
    if schema_preview is not None:
        doc["schema_preview"] = schema_preview
    return json.dumps(doc).encode()


CREATE = {"op": "create", "uid": "u1", "type_name": "dcim.site", "desired": {}}
DELETE = {"op": "delete", "uid": "u2", "type_name": "dcim.site", "key": {}, "backend_id": 4}


class StatusTests(unittest.TestCase):
    def test_happy_path(self):
        path = [S.PENDING, S.PLANNING, S.AWAITING_APPROVAL, S.APPROVED, S.APPLYING, S.APPLIED]
        for current, new in zip(path, path[1:], strict=False):
            self.assertEqual(check_transition(current, new), new)

    def test_accepts_strings(self):
        self.assertEqual(check_transition("planning", "failed"), S.FAILED)

    def test_refuses_skipping_approval(self):
        with self.assertRaises(TransitionError):
            check_transition(S.AWAITING_APPROVAL, S.APPLYING)

    def test_terminal_states_are_final(self):
        for status in TERMINAL:
            for new in RunStatus:
                with self.assertRaises(TransitionError):
                    check_transition(status, new)

    def test_resume_after_failed_apply(self):
        self.assertEqual(check_transition(S.APPLY_FAILED, S.APPLYING), S.APPLYING)

    def test_failure_status_follows_the_phase(self):
        self.assertEqual(failure_status(S.PLANNING), S.FAILED)
        self.assertEqual(failure_status(S.APPROVED), S.FAILED)
        self.assertEqual(failure_status(S.APPLYING), S.APPLY_FAILED)
        self.assertIsNone(failure_status(S.AWAITING_APPROVAL))
        for status in TERMINAL:
            self.assertIsNone(failure_status(status))

    def test_active_runs_are_not_terminal(self):
        self.assertFalse(ACTIVE & TERMINAL)


class PlanTests(unittest.TestCase):
    def test_summary_counts_ops(self):
        plan = Plan.from_bytes(plan_bytes([CREATE, DELETE]))
        self.assertEqual(plan.summary(), {"create": 1, "update": 0, "delete": 1})
        self.assertTrue(plan.has_deletes)
        self.assertFalse(plan.is_empty)

    def test_empty(self):
        self.assertTrue(Plan.from_bytes(plan_bytes([], {})).is_empty)
        self.assertTrue(Plan.from_bytes(plan_bytes([], {"created_fields": []})).is_empty)

    def test_schema_work_alone_is_not_empty(self):
        plan = Plan.from_bytes(plan_bytes([], {"created_fields": ["dcim.device.x"]}))
        self.assertFalse(plan.is_empty)

    def test_same_work_compares_ops_and_schema(self):
        a = Plan.from_bytes(plan_bytes([CREATE]))
        self.assertTrue(a.same_work(Plan.from_bytes(plan_bytes([CREATE]))))
        self.assertFalse(a.same_work(Plan.from_bytes(plan_bytes([CREATE, DELETE]))))
        self.assertFalse(
            a.same_work(Plan.from_bytes(plan_bytes([CREATE], {"created_fields": ["x"]})))
        )

    def test_sha256_is_of_the_bytes(self):
        raw = plan_bytes([CREATE])
        self.assertEqual(len(Plan.from_bytes(raw).sha256), 64)
        self.assertNotEqual(Plan.from_bytes(raw).sha256, Plan.from_bytes(raw + b" ").sha256)

    def test_rejects_non_plans(self):
        for raw in (b"not json", b"[]", b'{"schema": {}}'):
            with self.assertRaises(DocumentError):
                Plan.from_bytes(raw)

    def test_drift_report(self):
        empty = {"changed": [], "missing": [], "extra": []}
        self.assertFalse(DriftReport.from_bytes(json.dumps(empty).encode()).has_drift)
        drifted = DriftReport.from_bytes(json.dumps({**empty, "missing": [{}]}).encode())
        self.assertEqual(drifted.counts(), {"changed": 0, "missing": 1, "extra": 0})
        with self.assertRaises(DocumentError):
            DriftReport.from_bytes(b'{"changed": []}')

    def test_apply_report(self):
        report = ApplyReport.from_bytes(b'{"applied": [{}], "previously_applied_count": 2}')
        self.assertEqual((report.applied_count, report.resumed_count), (1, 2))
        with self.assertRaises(DocumentError):
            ApplyReport.from_bytes(b"{}")


class BackendTests(unittest.TestCase):
    def test_config_document(self):
        backend = Backend("netbox", {"url": "https://nb"}, env={"NETBOX_TOKEN": "t"})
        self.assertEqual(backend.config_document(), {"backend": "netbox", "url": "https://nb"})

    def test_refuses_tokens_in_config(self):
        with self.assertRaises(BackendError):
            Backend("netbox", {"url": "https://nb", "token": "t"})

    def test_refuses_process_control_in_config(self):
        for key in ("command", "working_dir", "env", "backend"):
            with self.assertRaises(BackendError, msg=key):
                Backend("external", {key: "x"}, command="/usr/bin/adapter")

    def test_external_command_comes_from_settings(self):
        with self.assertRaises(BackendError):
            Backend("external", {})
        with self.assertRaises(BackendError):
            Backend("external", {"command": "/bin/sh"}, command="/usr/bin/adapter")
        with self.assertRaises(BackendError):
            Backend("netbox", {}, command="/usr/bin/adapter")
        doc = Backend("external", {"args": ["x"]}, command="/usr/bin/adapter").config_document()
        self.assertEqual(doc["command"], "/usr/bin/adapter")

    def test_unknown_kind(self):
        with self.assertRaises(BackendError):
            Backend("generic", {})

    def test_plugin_names_follow_alembic(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("lab.yaml", "Prom.YML", "notes.txt", ".yaml"):
                (Path(d) / name).write_text("backend: external\n")
            (Path(d) / "dir.yaml").mkdir()
            self.assertEqual(plugin_names(d), ["lab", "prom"])
        self.assertEqual(plugin_names(None), [])
        self.assertEqual(plugin_names("/nonexistent"), [])

    def test_a_plugin_backend_has_no_config(self):
        Backend("lab", plugin=True)
        with self.assertRaises(BackendError):
            Backend("lab", {"setup": {}}, plugin=True)
        with self.assertRaises(BackendError):
            Backend("lab", plugin=True, command="/bin/sh")

    def test_token_env(self):
        self.assertEqual(token_env("nautobot", "t"), {"NAUTOBOT_TOKEN": "t"})
        with self.assertRaises(BackendError):
            token_env("external", "t")

    def test_state_env(self):
        self.assertEqual(StateStore().env("flow-1"), {"ALEMBIC_STATE_BACKEND": "local"})
        pg = StateStore.from_settings({"backend": "postgres", "postgres_url": "postgres://x"})
        self.assertEqual(pg.env("flow-1")["ALEMBIC_STATE_KEY"], "flow-1")
        with self.assertRaises(BackendError):
            StateStore("postgres")


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(Path(self.tmp.name), "7")

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_input_tree(self):
        entry = self.ws.write_input("3", {"inv.yaml": "a", "lib/sites.yaml": "b"}, "inv.yaml")
        self.assertEqual(entry.read_text(), "a")
        self.assertEqual((entry.parent / "lib" / "sites.yaml").read_text(), "b")

    def test_rewriting_input_drops_old_files(self):
        self.ws.write_input("3", {"inv.yaml": "a", "old.yaml": "x"}, "inv.yaml")
        entry = self.ws.write_input("3", {"inv.yaml": "a"}, "inv.yaml")
        self.assertFalse((entry.parent / "old.yaml").exists())

    def test_refuses_paths_outside_input(self):
        for path in ("../x.yaml", "/etc/passwd", "a/../../x", ""):
            with self.assertRaises(WorkspaceError, msg=path):
                self.ws.write_input("3", {path: "x", "inv.yaml": "a"}, "inv.yaml")

    def test_entry_must_be_an_input(self):
        with self.assertRaises(WorkspaceError):
            self.ws.write_input("3", {"inv.yaml": "a"}, "other.yaml")

    def test_refuses_unsafe_ids(self):
        with self.assertRaises(WorkspaceError):
            Workspace(Path(self.tmp.name), "../7")
        with self.assertRaises(WorkspaceError):
            self.ws.run_dir("a/b")
        with self.assertRaises(WorkspaceError):
            self.ws.write_file("3", "../plan.json", b"")

    def test_config_is_private(self):
        path = self.ws.write_backend_config("3", "target", {"backend": "netbox"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text()), {"backend": "netbox"})


if __name__ == "__main__":
    unittest.main()
