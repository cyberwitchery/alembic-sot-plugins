import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alembic_runner import Backend, Flow, Plan, Runner, RunnerError

FIXTURES = Path(__file__).parent / "fixtures"

INVENTORY = """\
schema:
  types:
    dcim.site:
      key:
        slug: { type: slug }
      fields:
        name: { type: string }
        slug: { type: slug }
objects:
  - uid: "11111111-1111-1111-1111-111111111111"
    type: dcim.site
    key: { slug: fra1 }
    attrs: { name: FRA1, slug: fra1 }
"""


class FakeBinaryTests(unittest.TestCase):
    """argv, environment and failure handling, against a stand-in binary."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.fake = self.tmp / "alembic"
        shutil.copy(FIXTURES / "fake_alembic.py", self.fake)
        self.fake.chmod(0o755)
        self.runner = Runner(self.tmp / "work", alembic_path=str(self.fake), timeout=5)
        self.flow = Flow(
            "1",
            Backend("netbox", {"url": "https://nb"}, env={"NETBOX_TOKEN": "secret"}),
            allow_delete=True,
        )
        self.inventory = self.tmp / "inv.yaml"
        self.inventory.write_text(INVENTORY)

    def behave(self, mode):
        self.fake.with_suffix(".mode").write_text(mode)

    def record(self):
        return json.loads(self.fake.with_suffix(".record").read_text())

    def test_environment_is_built_not_inherited(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY": "django", "DB_PASSWORD": "pw"}):
            self.runner.plan(self.flow, "9", self.inventory)
        env = self.record()["env"]
        self.assertNotIn("SECRET_KEY", env)
        self.assertNotIn("DB_PASSWORD", env)
        self.assertEqual(env["NETBOX_TOKEN"], "secret")
        self.assertEqual(env["ALEMBIC_STATE_BACKEND"], "local")
        self.assertEqual(env["HOME"], str(self.tmp / "work" / "flows" / "1"))

    def test_runs_in_the_flow_directory(self):
        self.runner.plan(self.flow, "9", self.inventory)
        self.assertEqual(
            Path(self.record()["cwd"]).resolve(), (self.tmp / "work/flows/1").resolve()
        )

    def test_plan_argv_and_config(self):
        self.runner.plan(self.flow, "9", self.inventory)
        argv = self.record()["argv"]
        self.assertEqual(argv[:3], ["plan", "-f", str(self.inventory)])
        self.assertIn("--allow-delete", argv)
        config = Path(argv[argv.index("--backend-config") + 1])
        self.assertEqual(json.loads(config.read_text()), {"backend": "netbox", "url": "https://nb"})
        self.assertNotIn("secret", config.read_text())

    def test_failure_is_reported(self):
        self.behave("fail")
        outcome = self.runner.plan(self.flow, "9", self.inventory)
        self.assertIsNone(outcome.plan)
        self.assertEqual(outcome.error, "alembic plan exited 1")
        self.assertIn("backend unreachable", outcome.completed.stderr)

    def test_missing_output_is_an_error(self):
        outcome = self.runner.plan(self.flow, "9", self.inventory)
        self.assertIn("plan output unreadable", outcome.error)

    def test_unreadable_dry_run_is_not_a_verdict(self):
        self.behave("garbage")
        approved = Plan.from_bytes(b'{"ops": []}')
        check = self.runner.check_stale(self.flow, "9", self.inventory, approved)
        self.assertFalse(check.stale)
        self.assertIn("dry-run output unreadable", check.error)

    def test_timeout(self):
        self.behave("sleep")
        self.runner.timeout = 0.5
        outcome = self.runner.plan(self.flow, "9", self.inventory)
        self.assertTrue(outcome.completed.timed_out)
        self.assertIsNone(outcome.completed.exit_code)
        self.assertIn("timed out", outcome.error)

    def test_apply_refuses_a_changed_plan(self):
        plan = Plan.from_bytes(b'{"ops": []}')
        with self.assertRaises(RunnerError):
            self.runner.apply(self.flow, "9", plan, "0" * 64)

    def test_version_range(self):
        self.assertEqual(self.runner.check_version(), "0.9.0")
        for text in ("alembic 0.8.4", "alembic 0.10.0", "something else"):
            self.fake.with_suffix(".version").write_text(text)
            with self.assertRaises(RunnerError, msg=text):
                self.runner.check_version()

    def test_missing_binary(self):
        runner = Runner(self.tmp / "work", alembic_path=str(self.tmp / "nope"))
        with self.assertRaises(RunnerError):
            runner.plan(self.flow, "9", self.inventory)


ALEMBIC = shutil.which(os.environ.get("ALEMBIC", "alembic"))


@unittest.skipUnless(ALEMBIC, "needs the alembic binary on PATH or in $ALEMBIC")
class RealBinaryTests(unittest.TestCase):
    """the real cli against a json-file external adapter."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.store = self.tmp / "store.json"
        self.runner = Runner(self.tmp / "work", alembic_path=ALEMBIC, timeout=60)
        self.runner.check_version()
        target = Backend(
            "external",
            {"args": [str(FIXTURES / "store_adapter.py")], "setup": {"path": str(self.store)}},
            command=sys.executable,
        )
        self.flow = Flow("1", target)
        ws = self.runner.workspace(self.flow)
        self.inventory = ws.write_input("1", {"inv.yaml": INVENTORY}, "inv.yaml")

    def edit_store(self, name):
        store = json.loads(self.store.read_text())
        store["objects"]["1"]["attrs"]["name"] = name
        self.store.write_text(json.dumps(store))

    def test_plan_apply_converges(self):
        outcome = self.runner.plan(self.flow, "1", self.inventory)
        self.assertIsNone(outcome.error, outcome.completed.stderr)
        self.assertEqual(outcome.plan.summary(), {"create": 1, "update": 0, "delete": 0})

        check = self.runner.check_stale(self.flow, "1", self.inventory, outcome.plan)
        self.assertIsNone(check.error, check.completed.stderr)
        self.assertFalse(check.stale)

        applied = self.runner.apply(self.flow, "1", outcome.plan, outcome.plan.sha256)
        self.assertIsNone(applied.error, applied.completed.stderr)
        self.assertEqual(applied.report.applied_count, 1)

        again = self.runner.plan(self.flow, "2", self.inventory)
        self.assertTrue(again.plan.is_empty)
        drift = self.runner.drift(self.flow, "3", self.inventory)
        self.assertIsNone(drift.error, drift.completed.stderr)
        self.assertFalse(drift.report.has_drift)

    def test_target_change_after_plan_is_stale(self):
        self.runner.apply(self.flow, "1", *self._plan("1"))
        self.edit_store("edited")
        outcome = self.runner.plan(self.flow, "2", self.inventory)
        self.assertEqual(outcome.plan.summary()["update"], 1)

        self.edit_store("edited again")
        check = self.runner.check_stale(self.flow, "2", self.inventory, outcome.plan)
        self.assertIsNone(check.error, check.completed.stderr)
        self.assertTrue(check.stale)

    def test_drift_is_reported(self):
        self.runner.apply(self.flow, "1", *self._plan("1"))
        self.edit_store("edited")
        drift = self.runner.drift(self.flow, "2", self.inventory)
        self.assertEqual(drift.report.counts(), {"changed": 1, "missing": 0, "extra": 0})

    def _plan(self, run):
        plan = self.runner.plan(self.flow, run, self.inventory).plan
        return plan, plan.sha256


if __name__ == "__main__":
    unittest.main()
