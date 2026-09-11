"""CEDAR release transport."""
from __future__ import annotations
from org.metadatacenter.util.BuildTrain import BuildTrain
from org.metadatacenter.util.NexusCredentials import CredentialError, environment_with_nexus_credentials
from pathlib import Path, PurePosixPath
import base64
import json
import re
import urllib.request
from org.metadatacenter.release_support.errors import (
    ReleaseError,
    RetryableReleaseError,
)
from org.metadatacenter.release_support.policy import (
    NEXUS_REPOSITORY_PROBE,
    NEXUS_WRITABLE_ENDPOINT,
    RETRYABLE_TRANSPORT_TEXT,
)


def _command_failure_is_retryable(command: list[str], detail: str) -> bool:
    """Classify only transport failures from commands whose release work is resumable.

    Maven/npm HTTP 500 is deliberately excluded: Nexus uses it when the Community Edition
    request budget is exhausted, and retrying that condition makes it worse. Gateway/service
    availability failures are bounded retries. Git pushes additionally retry a server-side 5xx;
    their ref guards make a response lost after a successful push safe to reconcile.
    """
    if not command:
        return False
    tool = Path(command[0]).name
    text = detail.lower()
    if any(token in text for token in RETRYABLE_TRANSPORT_TEXT):
        return tool in {"git", "mvn", "mvnw", "npm"} or tool.endswith("mvnw")
    if re.search(r"(?:http|status code:?)[^0-9]*(502|503|504)\b", text):
        return tool in {"git", "mvn", "mvnw", "npm"} or tool.endswith("mvnw")
    if tool == "git" and re.search(r"(?:http|status code:?)[^0-9]*5\d\d\b", text):
        return True
    return False


def _raise_command_failure(command: list[str], message: str, detail: str = "") -> None:
    exception = (
        RetryableReleaseError
        if _command_failure_is_retryable(command, detail)
        else ReleaseError
    )
    suffix = f": {detail}" if detail else ""
    raise exception(f"{message}{suffix}")


def _environment_with_nexus_credentials(environment=None) -> dict:
    """Translate shared configuration errors into the release error contract."""
    try:
        return environment_with_nexus_credentials(environment)
    except CredentialError as error:
        raise ReleaseError(str(error)) from error


class HttpClient:
    """Small authenticated reader used for state and npm registry artifacts."""

    def __init__(self, opener=None, environment=None):
        self.opener = opener or urllib.request.urlopen
        self.environment = _environment_with_nexus_credentials(environment)

    def _headers(self, url: str) -> dict[str, str]:
        if not url.startswith("https://nexus.bmir.stanford.edu/"):
            return {}
        username = self.environment.get("BMIR_NEXUS_USERNAME")
        password = self.environment.get("BMIR_NEXUS_PASSWORD")
        if not username or not password:
            return {}
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def read(self, url: str, *, missing_ok: bool = False) -> bytes | None:
        request = urllib.request.Request(url, headers=self._headers(url))
        try:
            with self.opener(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            raise ReleaseError(f"cannot read {url}: HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RetryableReleaseError(f"cannot read {url}: {error}") from error

    def read_json(self, url: str, *, missing_ok: bool = False) -> tuple[dict, bytes] | None:
        content = self.read(url, missing_ok=missing_ok)
        if content is None:
            return None
        try:
            value = json.loads(content)
        except json.JSONDecodeError as error:
            raise ReleaseError(f"invalid JSON at {url}: {error}") from error
        if not isinstance(value, dict):
            raise ReleaseError(f"expected a JSON object at {url}")
        return value, content


class TrainState:
    def __init__(self, http: HttpClient, base_url: str = BuildTrain.STATE_BASE_URL):
        self.http = http
        self.base_url = base_url.rstrip("/")

    def read_json(self, relative_path: str) -> tuple[dict, bytes]:
        result = self.http.read_json(f"{self.base_url}/{relative_path}")
        assert result is not None
        return result


class NexusCircuitBreaker:
    """One cheap health gate before a phase that would otherwise make many Nexus calls.

    A direct connection failure may clear and is safe to retry after backoff. An HTTP
    response is an explicit refusal. In particular, a healthy writable
    endpoint followed by a failing repository read is the observed request-budget failure;
    retrying it immediately only spends more of the same budget.
    """

    def __init__(self, http: HttpClient, environment=None):
        self.http = http
        self.environment = _environment_with_nexus_credentials(environment)

    def _read(self, url: str, label: str, purpose: str) -> None:
        try:
            self.http.read(url)
        except RetryableReleaseError as error:
            raise RetryableReleaseError(
                f"Nexus circuit breaker could not reach {label} before {purpose}: {error}"
            ) from error
        except ReleaseError as error:
            raise ReleaseError(
                f"Nexus circuit breaker is open before {purpose}: {label} failed ({error})"
            ) from error

    def require(self, purpose: str) -> None:
        if not self.environment.get("BMIR_NEXUS_USERNAME") or not self.environment.get(
            "BMIR_NEXUS_PASSWORD"
        ):
            raise ReleaseError(
                f"Nexus circuit breaker is open before {purpose}: "
                "BMIR_NEXUS_USERNAME and BMIR_NEXUS_PASSWORD are required"
            )
        self._read(NEXUS_WRITABLE_ENDPOINT, "writable status", purpose)
        try:
            self._read(NEXUS_REPOSITORY_PROBE, "repository read", purpose)
        except RetryableReleaseError:
            raise
        except ReleaseError as error:
            raise ReleaseError(
                f"{error}. Nexus status is writable but repository content is unavailable; "
                "this is consistent with the daily request budget being exhausted. Refusing "
                "bulk publication/verification until Nexus recovers"
            ) from error
