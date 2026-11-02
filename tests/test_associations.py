import base64
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


loader = importlib.machinery.SourceFileLoader(
    "keepassxc_pass", str(Path(__file__).resolve().parents[1] / "keepassxc-pass")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
app = importlib.util.module_from_spec(spec)
loader.exec_module(app)


class NotAssociated(Exception):
    pass


class FakeKeePassXC:
    """Model independent authorizations for two databases, with no real secrets."""

    def __init__(self):
        self.active = "database-a"
        self.authorizations = {}
        self.dialogs = []
        self.logins = {
            "database-a": [{"uuid": "aaa", "password": "password-a"}],
            "database-b": [{"uuid": "bbb", "password": "password-b"}],
        }

    def connect(self):
        server = self

        class Connection:
            def get_databasehash(self):
                return server.active

            def load_associate(self, name, public_key):
                self.association = (name, public_key)

            def test_associate(self):
                if self.association != server.authorizations.get(server.active):
                    raise NotAssociated("Database not associated")
                return True

            def associate(self):
                server.dialogs.append(server.active)
                # Names may be identical across databases; keys must stay distinct.
                self.association = ("cli", bytes([len(server.dialogs)]) * 32)
                server.authorizations[server.active] = self.association

            def dump_associate(self):
                return self.association

            def get_logins(self, url):
                self.test_associate()
                return server.logins[server.active] if url == "https://cli.keepassxc" else []

        return Connection()


class AssociationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config_dir = Path(temporary.name) / "config"
        self.association_file = self.config_dir / "association.json"
        self.server = FakeKeePassXC()
        protocol = types.ModuleType("keepassxc_proxy_client.protocol")
        protocol.ResponseUnsuccesfulException = NotAssociated
        package = types.ModuleType("keepassxc_proxy_client")
        package.protocol = protocol
        patches = [
            patch.object(app, "CONFIG_DIR", self.config_dir),
            patch.object(app, "ASSOCIATION_FILE", self.association_file),
            patch.object(app, "GLOBAL_CONFIG", self.config_dir / "config.toml"),
            patch.object(app, "connect", self.server.connect),
            patch.dict(sys.modules, {
                "keepassxc_proxy_client": package,
                "keepassxc_proxy_client.protocol": protocol,
            }),
        ]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)

    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["keepassxc-pass", *args]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = app.main()
        return status, stdout.getvalue(), stderr.getvalue()

    def pair(self):
        status, stdout, stderr = self.run_cli("--pair")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stdout, "")

    def lookup(self, uuid, expected):
        status, stdout, stderr = self.run_cli("--uuid", uuid, "--no-prompt")
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stdout, expected + "\n")
        self.assertEqual(stderr, "")

    def legacy_file(self):
        association = ("old-client", b"k" * 32)
        self.server.authorizations["database-a"] = association
        self.config_dir.mkdir()
        self.association_file.write_text(json.dumps({
            "name": association[0],
            "public_key": base64.b64encode(association[1]).decode(),
        }))

    def test_pair_two_databases_and_switch_without_reauthorization(self):
        self.pair()
        self.server.active = "database-b"
        self.pair()
        for _ in range(2):
            self.lookup("bbb", "password-b")
            self.server.active = "database-a"
            self.lookup("aaa", "password-a")
            self.server.active = "database-b"
        self.assertEqual(self.server.dialogs, ["database-a", "database-b"])
        self.assertEqual(set(app.load_associations()["databases"]), {"database-a", "database-b"})

    def test_pair_is_idempotent(self):
        self.pair()
        original = self.association_file.read_bytes()
        self.pair()
        self.assertEqual(self.association_file.read_bytes(), original)
        self.assertEqual(self.server.dialogs, ["database-a"])

    def test_old_file_migrates_on_lookup_without_dialog(self):
        self.legacy_file()
        self.lookup("aaa", "password-a")
        associations = app.load_associations()
        self.assertIn("database-a", associations["databases"])
        self.assertNotIn("legacy", associations)
        self.assertEqual(self.server.dialogs, [])

    def test_pair_existing_legacy_database_reuses_authorization(self):
        self.legacy_file()
        self.pair()
        self.assertEqual(self.server.dialogs, [])
        self.lookup("aaa", "password-a")

    def test_pair_new_database_preserves_legacy_key(self):
        self.legacy_file()
        self.server.active = "database-b"
        self.pair()
        self.assertIn("legacy", app.load_associations())
        self.lookup("bbb", "password-b")
        self.server.active = "database-a"
        self.lookup("aaa", "password-a")
        self.assertEqual(set(app.load_associations()["databases"]), {"database-a", "database-b"})
        self.assertNotIn("legacy", app.load_associations())
        self.assertEqual(self.server.dialogs, ["database-b"])

    def test_unknown_database_does_not_pair_or_modify_store(self):
        self.pair()
        original = self.association_file.read_bytes()
        self.server.active = "database-b"
        status, stdout, stderr = self.run_cli("--uuid", "bbb", "--no-prompt")
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Active database is not paired", stderr)
        self.assertEqual(self.association_file.read_bytes(), original)
        self.assertEqual(self.server.dialogs, ["database-a"])

    def test_wrong_database_does_not_consume_legacy_key(self):
        self.legacy_file()
        original = self.association_file.read_bytes()
        self.server.active = "database-b"
        status, stdout, stderr = self.run_cli("--uuid", "bbb", "--no-prompt")
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Database not associated", stderr)
        self.assertEqual(self.association_file.read_bytes(), original)
        self.server.active = "database-a"
        self.lookup("aaa", "password-a")

    def test_switching_tabs_during_pair_does_not_save_under_wrong_database(self):
        self.pair()
        original = self.association_file.read_bytes()
        self.server.active = "database-b"
        connection = self.server.connect()
        with patch.object(connection, "get_databasehash", side_effect=["database-b", "database-a"]), \
                patch.object(app, "connect", return_value=connection):
            status, stdout, stderr = self.run_cli("--pair")
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Active database changed", stderr)
        self.assertEqual(self.association_file.read_bytes(), original)

    def test_switching_tabs_during_migration_preserves_legacy_key(self):
        self.legacy_file()
        original = self.association_file.read_bytes()
        connection = self.server.connect()
        with patch.object(connection, "get_databasehash", side_effect=["database-a", "database-b"]), \
                patch.object(app, "connect", return_value=connection):
            status, stdout, stderr = self.run_cli("--uuid", "aaa", "--no-prompt")
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Active database changed", stderr)
        self.assertEqual(self.association_file.read_bytes(), original)

    def test_revoked_association_can_be_repaired_without_losing_other_database(self):
        self.pair()
        self.server.active = "database-b"
        self.pair()
        self.server.active = "database-a"
        del self.server.authorizations["database-a"]
        self.pair()
        self.lookup("aaa", "password-a")
        self.server.active = "database-b"
        self.lookup("bbb", "password-b")

    def test_failed_pair_keeps_all_saved_keys(self):
        self.pair()
        original = self.association_file.read_bytes()
        self.server.active = "database-b"
        connection = self.server.connect()
        with patch.object(connection, "associate", side_effect=NotAssociated("Cancelled")), \
                patch.object(app, "connect", return_value=connection):
            status, stdout, stderr = self.run_cli("--pair")
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Cancelled", stderr)
        self.assertEqual(self.association_file.read_bytes(), original)

    def test_failed_atomic_write_preserves_original_and_cleans_temporary(self):
        self.pair()
        original = self.association_file.read_bytes()
        self.server.active = "database-b"
        with patch.object(app.os, "replace", side_effect=OSError("Write failed")):
            status, stdout, stderr = self.run_cli("--pair")
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Write failed", stderr)
        self.assertEqual(self.association_file.read_bytes(), original)
        self.assertEqual(list(self.config_dir.iterdir()), [self.association_file])

    def test_store_is_private_on_creation_update_and_migration(self):
        self.pair()
        self.assertEqual(stat.S_IMODE(self.config_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.association_file.stat().st_mode), 0o600)
        self.association_file.chmod(0o644)
        self.server.active = "database-b"
        self.pair()
        self.assertEqual(stat.S_IMODE(self.association_file.stat().st_mode), 0o600)

    def test_legacy_migration_makes_store_private(self):
        self.legacy_file()
        self.association_file.chmod(0o644)
        self.lookup("aaa", "password-a")
        self.assertEqual(stat.S_IMODE(self.association_file.stat().st_mode), 0o600)

    def test_unavailable_keepassxc_keeps_store_and_terminal_fallback(self):
        self.pair()
        original = self.association_file.read_bytes()
        with patch.object(app, "connect", side_effect=OSError("Unavailable")), \
                patch.object(app.getpass, "getpass", return_value="fallback") as prompt:
            status, stdout, stderr = self.run_cli("--uuid", "aaa")
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "fallback\n")
        self.assertIn("Unavailable", stderr)
        prompt.assert_called_once_with("Password: ")
        self.assertEqual(self.association_file.read_bytes(), original)

    def test_invalid_store_is_not_overwritten(self):
        self.config_dir.mkdir()
        for contents in ("not json", "[]", '{"databases": []}', '{}'):
            with self.subTest(contents=contents):
                self.association_file.write_text(contents)
                status, stdout, stderr = self.run_cli("--pair")
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr)
                self.assertEqual(self.association_file.read_text(), contents)
        self.assertEqual(self.server.dialogs, [])

    def test_missing_uuid_still_fails(self):
        with patch.object(app, "find_local_config", return_value=None):
            status, stdout, stderr = self.run_cli("--no-prompt")
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertIn("No UUID", stderr)


if __name__ == "__main__":
    unittest.main()
