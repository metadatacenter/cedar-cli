import unittest

from org.metadatacenter.util.OutputBuffering import line_buffer_when_redirected


class FakeStream:
    def __init__(self, name, tty, reconfigurable=True):
        self.name = name
        self._tty = tty
        self.reconfigured_with = None
        if not reconfigurable:
            del self.reconfigure

    def isatty(self):
        return self._tty

    def reconfigure(self, **options):
        self.reconfigured_with = options


class LineBufferingTest(unittest.TestCase):
    """A redirected stream shows each progress line as it is printed; a terminal is left alone."""

    def test_a_redirected_stream_is_line_buffered_and_a_terminal_is_not(self):
        terminal = FakeStream("<stdout>", tty=True)
        redirected = FakeStream("<stderr>", tty=False)

        adjusted = line_buffer_when_redirected([terminal, redirected])

        self.assertEqual(["<stderr>"], adjusted)
        self.assertIsNone(terminal.reconfigured_with)
        self.assertEqual({"line_buffering": True}, redirected.reconfigured_with)

    def test_a_stream_that_cannot_be_reconfigured_is_skipped(self):
        class Plain:
            name = "plain"

            @staticmethod
            def isatty():
                return False

        self.assertEqual([], line_buffer_when_redirected([Plain(), None]))


if __name__ == "__main__":
    unittest.main()
