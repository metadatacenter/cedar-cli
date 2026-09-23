import unittest
from unittest.mock import patch

from org.metadatacenter.worker.NativeWorker import NativeWorker


class HealthGroupTest(unittest.TestCase):
    """
    A health gate must be able to name the group a host actually runs.

    An application host serves its frontends as static trees from nginx and runs none of the seven
    ui-* processes, so an all-or-nothing probe reports failure however healthy the backend is.
    """

    def test_all_selects_everything_by_naming_nothing(self):
        self.assertEqual((), NativeWorker.health_group("all"))
        self.assertEqual((), NativeWorker.health_group(None))

    def test_microservices_names_the_fifteen_backends(self):
        services = NativeWorker.health_group("microservices")
        self.assertEqual(15, len(services))
        self.assertIn("resource", services)
        self.assertNotIn("ui-main", services)

    def test_frontends_names_the_seven_ui_processes(self):
        services = NativeWorker.health_group("frontends")
        self.assertEqual(7, len(services))
        self.assertIn("ui-main", services)
        self.assertNotIn("resource", services)

    def test_an_unknown_group_is_refused_rather_than_silently_meaning_all(self):
        with self.assertRaises(ValueError):
            NativeWorker.health_group("backends")

    def test_the_selected_group_reaches_the_controller(self):
        with patch.object(NativeWorker, "execute") as execute:
            NativeWorker.health("microservices")
        services = execute.call_args.kwargs["services"]
        self.assertEqual(NativeWorker.MICROSERVICES, services)
        self.assertIn("microservices", execute.call_args.kwargs["title"])

    def test_the_default_still_judges_every_managed_application(self):
        with patch.object(NativeWorker, "execute") as execute:
            NativeWorker.health()
        self.assertEqual((), execute.call_args.kwargs["services"])


if __name__ == "__main__":
    unittest.main()
