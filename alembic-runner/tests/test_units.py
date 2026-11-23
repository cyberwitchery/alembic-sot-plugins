import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path

from alembic_runner import (
    ACTIVE,
    BUILTIN_KINDS,
    CONFIG_KEYS,
    STARTABLE,
    TOKEN_ENV,
    TRANSITIONS,
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
from alembic_runner.review import deferred


def plan_bytes(ops, schema_preview=None):
    doc = {"schema": {"types": {}}, "ops": ops}
    if schema_preview is not None:
        doc["schema_preview"] = schema_preview
    return json.dumps(doc).encode()


CREATE = {"op": "create", "uid": "u1", "type_name": "dcim.site", "desired": {}}
DELETE = {"op": "delete", "uid": "u2", "type_name": "dcim.site", "key": {}, "backend_id": 4}

FINAL = ("no_changes", "drifted", "failed", "rejected", "superseded", "expired", "applied", "stale")


class StatusTests(unittest.TestCase):
    def test_happy_path(self):
        path = [
            RunStatus.PENDING,
            RunStatus.PLANNING,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.APPROVED,
            RunStatus.APPLYING,
            RunStatus.APPLIED,
        ]
        for current, new in zip(path, path[1:], strict=False):
            self.assertEqual(check_transition(current, new), new)

    def test_accepts_strings(self):
        self.assertEqual(check_transition("planning", "failed"), RunStatus.FAILED)

    def test_refuses_skipping_approval(self):
        with self.assertRaises(TransitionError):
            check_transition(RunStatus.AWAITING_APPROVAL, RunStatus.APPLYING)

    def test_terminal_states_are_final(self):
        for status in FINAL:
            for new in RunStatus:
                with self.assertRaises(TransitionError):
                    check_transition(status, new)

    def test_resume_after_failed_apply(self):
        self.assertEqual(
            check_transition(RunStatus.APPLY_FAILED, RunStatus.APPLYING), RunStatus.APPLYING
        )

    def test_failure_status_follows_the_phase(self):
        self.assertEqual(failure_status(RunStatus.PLANNING), RunStatus.FAILED)
        self.assertEqual(failure_status(RunStatus.APPROVED), RunStatus.FAILED)
        self.assertEqual(failure_status(RunStatus.APPLYING), RunStatus.APPLY_FAILED)
        self.assertIsNone(failure_status(RunStatus.AWAITING_APPROVAL))
        for status in FINAL:
            self.assertIsNone(failure_status(status))

    def test_an_active_run_can_move_on(self):
        self.assertLessEqual(ACTIVE, set(TRANSITIONS))

    def test_an_apply_starts_from_an_approval_or_a_failed_apply(self):
        self.assertEqual(
            STARTABLE, {s for s, moves in TRANSITIONS.items() if RunStatus.APPLYING in moves}
        )
        self.assertIn("approved", STARTABLE)

    def test_an_approved_run_with_a_reason_waits_to_be_started(self):
        self.assertTrue(deferred("approved", "Another run is applying to t; try again later."))
        self.assertFalse(deferred("approved", ""))
        self.assertFalse(deferred("apply_failed", "boom"))


class PlanTests(unittest.TestCase):
    def test_summary_counts_ops(self):
        plan = Plan.from_bytes(plan_bytes([CREATE, DELETE]))
        self.assertEqual(plan.summary(), {"create": 1, "update": 0, "delete": 1})
        self.assertFalse(plan.is_empty)

    def test_empty(self):
        self.assertTrue(Plan.from_bytes(plan_bytes([], {})).is_empty)
        self.assertTrue(Plan.from_bytes(plan_bytes([], {"created_fields": []})).is_empty)

    def test_schema_work_alone_is_not_empty(self):
        plan = Plan.from_bytes(plan_bytes([], {"created_fields": ["dcim.device.x"]}))
        self.assertFalse(plan.is_empty)

    def test_sha256_is_of_the_bytes(self):
        raw = plan_bytes([CREATE])
        self.assertEqual(Plan.from_bytes(raw).sha256, hashlib.sha256(raw).hexdigest())
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
        self.assertEqual(report.applied_count, 1)
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
        for kind in BUILTIN_KINDS:
            for key in ("command", "working_dir", "env", "backend"):
                with self.assertRaises(BackendError, msg=f"{kind} {key}"):
                    Backend(kind, {key: "x"})

    def test_every_builtin_kind_has_config_keys_and_a_token(self):
        self.assertEqual(set(CONFIG_KEYS), set(BUILTIN_KINDS))
        self.assertEqual(set(TOKEN_ENV), set(BUILTIN_KINDS))

    def test_peeringdb_takes_no_config(self):
        Backend("peeringdb", {})
        for key in ("url", "instance"):
            with self.assertRaises(BackendError, msg=key):
                Backend("peeringdb", {key: "x"})

    def test_unknown_kind(self):
        for kind in ("generic", "external"):
            with self.assertRaises(BackendError, msg=kind):
                Backend(kind, {})

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

    def test_token_env(self):
        self.assertEqual(token_env("nautobot", "t"), {"NAUTOBOT_TOKEN": "t"})
        with self.assertRaises(BackendError):
            token_env("lab", "t")

    def test_state_env(self):
        self.assertEqual(StateStore().env("flow-1"), {"ALEMBIC_STATE_BACKEND": "local"})
        pg = StateStore.from_settings({"backend": "postgres", "postgres_url": "postgres://x"})
        self.assertEqual(
            pg.env("flow-1"),
            {
                "ALEMBIC_STATE_BACKEND": "postgres",
                "ALEMBIC_STATE_POSTGRES_URL": "postgres://x",
                "ALEMBIC_STATE_KEY": "flow-1",
            },
        )
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
