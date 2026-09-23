"""The artifact document store's unique @id indexes, as the check reports them."""
import unittest
from unittest.mock import patch

from org.metadatacenter.util.ProcessRunner import CommandOutput
from org.metadatacenter.worker.StoreWorker import StoreWorker

ENVIRONMENT = {
    "CEDAR_MONGO_HOST": "127.0.0.1",
    "CEDAR_MONGO_PORT": "27017",
    "CEDAR_MONGO_APP_USER_NAME": "cedarMongoUser",
    "CEDAR_MONGO_APP_USER_PASSWORD": "secret",
}

PROVISIONED = CommandOutput([
    "templates\t4899\tunique",
    "template-elements\t5612\tunique",
    "template-fields\t141773\tunique",
    "template-instances\t150583\tunique",
], 0)

UNPROVISIONED = CommandOutput([
    "templates\t4899\tmissing",
    "template-elements\t5612\tmissing",
    "template-fields\t141773\tmissing",
    "template-instances\t150583\tunique",
], 0)


class StoreCheckTest(unittest.TestCase):
    def _check(self, output, environment=None):
        with patch.object(StoreWorker, "_shell", return_value="mongosh"), \
                patch("org.metadatacenter.worker.StoreWorker.ModeManager.profile_environment",
                      return_value=environment if environment is not None else ENVIRONMENT):
            return StoreWorker.check_stores(runner=lambda: output)

    def test_a_provisioned_store_passes(self):
        self.assertEqual(0, self._check(PROVISIONED))

    def test_a_missing_index_fails(self):
        self.assertEqual(1, self._check(UNPROVISIONED))

    def test_a_collection_the_store_did_not_report_fails(self):
        partial = CommandOutput(["templates\t4899\tunique"], 0)
        self.assertEqual(1, self._check(partial))

    def test_a_store_that_does_not_answer_fails(self):
        """An unreadable store is unknown, which the check must not read as provisioned."""
        self.assertEqual(1, self._check(CommandOutput([], 1)))

    def test_absent_credentials_fail_before_connecting(self):
        self.assertEqual(1, self._check(PROVISIONED, environment={"CEDAR_MONGO_HOST": "127.0.0.1"}))

    def test_no_mongo_shell_fails(self):
        with patch.object(StoreWorker, "_shell", return_value=None):
            self.assertEqual(1, StoreWorker.check_stores(runner=lambda: PROVISIONED))

    def test_the_probe_never_carries_the_password_into_its_output(self):
        """The command line holds the URI; nothing the check prints may."""
        with patch.object(StoreWorker, "_shell", return_value="mongosh"), \
                patch("org.metadatacenter.worker.StoreWorker.ModeManager.profile_environment",
                      return_value=ENVIRONMENT), \
                patch("org.metadatacenter.worker.StoreWorker.console") as console:
            StoreWorker.check_stores(runner=lambda: PROVISIONED)
        printed = " ".join(str(call) for call in console.print.call_args_list)
        self.assertNotIn(ENVIRONMENT["CEDAR_MONGO_APP_USER_PASSWORD"], printed)


if __name__ == "__main__":
    unittest.main()
