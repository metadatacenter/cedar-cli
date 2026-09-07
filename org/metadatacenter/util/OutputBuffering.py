"""Keep progress visible when the CLI writes to a file rather than a terminal."""

import sys


def line_buffer_when_redirected(streams=None):
    """Flush each printed line at once on every stream that is not a terminal.

    A long release or train watch is often run under nohup, or with its output redirected, and
    Python then block-buffers stdout: a progress line printed in the first minute reaches the file
    minutes later, or when the process ends. A terminal already flushes per line, so the change
    is made only where it alters anything. Returns the names of the streams it reconfigured.
    """
    adjusted = []
    for stream in (sys.stdout, sys.stderr) if streams is None else streams:
        reconfigure = getattr(stream, "reconfigure", None)
        try:
            if stream is None or reconfigure is None or stream.isatty():
                continue
            reconfigure(line_buffering=True)
        except (AttributeError, OSError, ValueError):
            continue
        adjusted.append(getattr(stream, "name", repr(stream)))
    return adjusted
