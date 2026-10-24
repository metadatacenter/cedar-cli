"""Wall-clock evidence for the complete frontend command, including deployment and smoke."""
from contextlib import contextmanager
import json
from pathlib import Path
import time
from uuid import uuid4

from rich.console import Console


class FrontendTimings:
    def __init__(self, home, kind):
        self.home, self.kind, self.phases = Path(home), kind, []
        self.started = time.monotonic()
        self.started_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    @contextmanager
    def phase(self, name):
        started, passed = time.monotonic(), False
        try:
            yield
            passed = True
        finally:
            seconds = round(time.monotonic() - started, 3)
            self.phases.append({'phase': name, 'seconds': seconds, 'passed': passed})
            Console().print(f'Frontend timing: {name}: {seconds:.1f}s ({"passed" if passed else "failed"})')

    def finish(self, passed):
        directory = self.home / '.cedar/build-reports/frontend-runs'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (time.strftime('%Y%m%dT%H%M%SZ-', time.gmtime()) + uuid4().hex[:8] + '.json')
        seconds = round(time.monotonic() - self.started, 3)
        path.write_text(json.dumps({'schemaVersion': 1, 'kind': self.kind,
            'startedAt': self.started_at, 'seconds': seconds, 'passed': passed,
            'phases': self.phases}, indent=2) + '\n')
        Console().print(f'Frontend command: {seconds:.1f}s; timings: {path}', markup=False)


@contextmanager
def frontend_timings(home, kind):
    timing, passed = FrontendTimings(home, kind), False
    try:
        yield timing
        passed = True
    finally:
        timing.finish(passed)
