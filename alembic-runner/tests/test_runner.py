import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alembic_runner import CONFIG_KEYS, TOKEN_ENV, Backend, Flow, Plan, Runner, RunnerError
from alembic_runner.runner import PASSTHROUGH_ENV

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
        # the interpreter running the fake binary adds its own locale and, on
        # macOS, CoreFoundation variables; anything else must come from the runner.
        platform = {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
        allowed = {"HOME", "ALEMBIC_STATE_BACKEND", "NETBOX_TOKEN", *PASSTHROUGH_ENV, *platform}
        self.assertLessEqual(set(env), allowed)
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

    def test_exit_codes_are_named(self):
        for code, meaning in (
            (64, "usage error"),
            (69, "the backend could not be reached"),
            (75, "applying again resumes it"),
        ):
            self.behave(f"exit {code}")
            outcome = self.runner.plan(self.flow, "9", self.inventory)
            self.assertTrue(outcome.error.startswith(f"alembic plan exited {code}: "))
            self.assertIn(meaning, outcome.error)

    def test_missing_output_is_an_error(self):
        outcome = self.runner.plan(self.flow, "9", self.inventory)
        self.assertIn("plan output unreadable", outcome.error)

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

    def test_apply_leaves_the_stale_check_to_alembic(self):
        plan = Plan.from_bytes(b'{"ops": []}')
        self.runner.apply(self.flow, "9", plan, plan.sha256)
        argv = self.record()["argv"]
        self.assertEqual(argv[:3], ["apply", "-p", argv[2]])
        self.assertNotIn("--no-stale-check", argv)

    def test_apply_exit_65_is_stale(self):
        self.behave("exit 65")
        plan = Plan.from_bytes(b'{"ops": []}')
        outcome = self.runner.apply(self.flow, "9", plan, plan.sha256)
        self.assertTrue(outcome.stale)
        self.assertIsNone(outcome.error)
        self.assertIsNone(outcome.report)

    def test_version_range(self):
        self.assertEqual(self.runner.check_version(), "0.13.0")
        for text in ("alembic 0.12.0", "alembic 0.14.0", "something else"):
            self.fake.with_suffix(".version").write_text(text)
            with self.assertRaises(RunnerError, msg=text):
                self.runner.check_version()

    def test_import_talks_to_the_source_with_its_credentials(self):
        flow = Flow(
            "1",
            Backend("netbox", {"url": "https://target"}, env={"NETBOX_TOKEN": "target"}),
            source=Backend("nautobot", {"url": "https://src"}, env={"NAUTOBOT_TOKEN": "source"}),
        )
        outcome = self.runner.import_(flow, "9", self.inventory)
        record = self.record()
        self.assertEqual(record["argv"][:3], ["import", "-f", str(self.inventory)])
        config = Path(record["argv"][record["argv"].index("--backend-config") + 1])
        self.assertEqual(json.loads(config.read_text())["url"], "https://src")
        self.assertEqual(record["env"].get("NAUTOBOT_TOKEN"), "source")
        self.assertNotIn("NETBOX_TOKEN", record["env"])
        self.assertIn("no inventory", outcome.error)

    def test_map_gets_no_credentials(self):
        self.runner.map(self.flow, "9", self.inventory, self.tmp / "spec.yaml")
        record = self.record()
        self.assertEqual(record["argv"][0], "map")
        self.assertNotIn("NETBOX_TOKEN", record["env"])

    def test_import_needs_a_source(self):
        with self.assertRaises(RunnerError):
            self.runner.import_(self.flow, "9", self.inventory)

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
        self.plugins = self.tmp / "plugins"
        self.plugins.mkdir()
        self.runner = Runner(
            self.tmp / "work", alembic_path=ALEMBIC, timeout=60, plugins_dir=self.plugins
        )
        self.runner.check_version()
        self.flow = Flow("1", self.store_backend(self.store))
        ws = self.runner.workspace(self.flow)
        self.inventory = ws.write_input("1", {"inv.yaml": INVENTORY}, "inv.yaml")

    def store_backend(self, path):
        """an alembic plugin named for `path`, over the store adapter keeping its
        objects in `path`."""
        plugin = {
            "backend": "external",
            "command": sys.executable,
            "args": [str(FIXTURES / "store_adapter.py")],
            "setup": {"path": str(path)},
        }
        (self.plugins / f"{path.stem}.yaml").write_text(json.dumps(plugin))
        return Backend(path.stem.lower(), plugin=True)

    def edit_store(self, name):
        store = json.loads(self.store.read_text())
        store["objects"]["1"]["attrs"]["name"] = name
        self.store.write_text(json.dumps(store))

    def test_plan_apply_converges(self):
        outcome = self.runner.plan(self.flow, "1", self.inventory)
        self.assertIsNone(outcome.error, outcome.completed.stderr)
        self.assertEqual(outcome.plan.summary(), {"create": 1, "update": 0, "delete": 0})

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
        applied = self.runner.apply(self.flow, "2", outcome.plan, outcome.plan.sha256)
        self.assertTrue(applied.stale, applied.completed.stderr)
        self.assertIsNone(applied.error)
        store = json.loads(self.store.read_text())
        self.assertEqual(store["objects"]["1"]["attrs"]["name"], "edited again")

    def test_drift_is_reported(self):
        self.runner.apply(self.flow, "1", *self._plan("1"))
        self.edit_store("edited")
        drift = self.runner.drift(self.flow, "2", self.inventory)
        self.assertEqual(drift.report.counts(), {"changed": 1, "missing": 0, "extra": 0})

    def test_import_then_plan_into_another_backend(self):
        # the flow's own target starts out converged; a second flow reads it as
        # its source and plans the same objects into an empty store.
        self.runner.apply(self.flow, "1", *self._plan("1"))
        flow = Flow("2", self.store_backend(self.tmp / "other.json"), source=self.flow.target)
        imported = self.runner.import_(flow, "1", self.inventory)
        self.assertIsNone(imported.error, imported.completed.stderr)
        doc = json.loads(imported.inventory.read_text())
        self.assertEqual([o["key"] for o in doc["objects"]], [{"slug": "fra1"}])

        outcome = self.runner.plan(flow, "1", imported.inventory)
        self.assertIsNone(outcome.error, outcome.completed.stderr)
        self.assertEqual(outcome.plan.summary(), {"create": 1, "update": 0, "delete": 0})

    def test_map_reshapes_an_inventory(self):
        spec = self.tmp / "map.yaml"
        spec.write_text(
            "schema:\n  types:\n    dcim.location:\n      key:\n        name: { type: string }\n"
            "rules:\n  - name: sites\n    match: dcim.site\n    emit:\n"
            '      type: dcim.location\n      key: { name: "${attrs.name}" }\n'
        )
        mapped = self.runner.map(self.flow, "1", self.inventory, spec)
        self.assertIsNone(mapped.error, mapped.completed.stderr)
        doc = json.loads(mapped.inventory.read_text())
        self.assertEqual([o["type"] for o in doc["objects"]], ["dcim.location"])

    def _plan(self, run):
        plan = self.runner.plan(self.flow, run, self.inventory).plan
        return plan, plan.sha256


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(ALEMBIC, "needs the alembic binary on PATH or in $ALEMBIC")
class ConfigKeyTests(unittest.TestCase):
    """the keys a stored backend may set are the keys the pinned alembic parses."""

    def plan(self, config):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            (work / "inv.yaml").write_text("schema:\n  types: {}\nobjects: []\n")
            (work / "backend.json").write_text(json.dumps(config))
            env = {"PATH": os.environ.get("PATH", ""), "HOME": d}
            env.update({name: "t" for name in TOKEN_ENV.values()})
            return subprocess.run(
                [
                    ALEMBIC,
                    "plan",
                    "-f",
                    "inv.yaml",
                    "--backend-config",
                    "backend.json",
                    "-o",
                    "plan.json",
                ],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )

    def test_every_allowed_key_parses(self):
        for kind, keys in CONFIG_KEYS.items():
            # an unreachable url: the run stops after the config is read.
            config = {"backend": kind, **{key: "http://127.0.0.1:9" for key in keys}}
            done = self.plan(config)
            self.assertNotIn("parse adapter config", done.stderr, f"{kind}: {done.stderr}")

    def test_a_key_alembic_does_not_take_is_refused(self):
        for kind in TOKEN_ENV:
            done = self.plan({"backend": kind, "not_a_key": "x"})
            self.assertEqual(done.returncode, 65, f"{kind}: {done.stderr}")
            self.assertIn("parse adapter config", done.stderr, kind)
