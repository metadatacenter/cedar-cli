import json
import os
import subprocess
from pathlib import Path

from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.model.CedarProfile import CedarProfile


class ModeError(ValueError):
    pass


class ModeManager:
    """Persist the selected CEDAR topology and prepare commands for a bare shell."""

    STATE_FILE = "mode.json"
    NATIVE_PROFILE = "cedar-development/bin/templates/cedar-profile-native.sh"
    DOCKER_PROFILE = "cedar-development/bin/templates/cedar-profile-docker.sh"
    FRONTEND_NAMES = (
        "EDITOR",
        "CONTENT",
        "OPENVIEW",
        "MONITORING",
        "BRIDGING",
        "WORKSPACE",
        "DESIGNER",
    )
    PERSISTED_ENVIRONMENT = (
        "CEDAR_IMAGE_PREFIX",
        "CEDAR_BASE_IMAGE_PREFIX",
        "CEDAR_TERMINOLOGY_STORE_CATALOG",
    )

    @classmethod
    def cedar_home(cls) -> Path:
        configured = os.environ.get("CEDAR_HOME")
        if configured:
            home = Path(configured).expanduser().resolve()
        else:
            home = Path(__file__).resolve().parents[4]
            os.environ["CEDAR_HOME"] = str(home)
        return home

    @classmethod
    def state_path(cls) -> Path:
        return cls.cedar_home() / ".cedar" / cls.STATE_FILE

    @classmethod
    def state(cls):
        try:
            with cls.state_path().open("r", encoding="utf-8") as state_file:
                state = json.load(state_file)
            state["mode"] = CedarMode(state["mode"])
            if state.get("profile") is not None:
                state["profile"] = CedarProfile(state["profile"])
            return state
        except FileNotFoundError:
            return None
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise ModeError(
                f"CEDAR mode state is invalid: {cls.state_path()}: {error}"
            ) from error

    @classmethod
    def current(cls):
        state = cls.state()
        return None if state is None else state["mode"]

    @classmethod
    def current_profile(cls):
        state = cls.state()
        return None if state is None else state.get("profile")

    @classmethod
    def require_profile(cls):
        """The native environment this host runs, which nothing may guess on its behalf.

        A host recorded before profiles existed has none, and defaulting it would hand a server
        the workstation profile, whose TLS-verification bypass belongs on no server.
        """
        profile = cls.current_profile()
        if profile is None:
            raise ModeError(
                "CEDAR native profile is not recorded; run cedarcli mode --clear and then "
                "cedarcli mode native --profile develop|server"
            )
        return profile

    @classmethod
    def record(cls, mode: CedarMode, environment=None, profile: CedarProfile = None):
        existing = cls.current()
        if existing is not None:
            raise ModeError(
                f"CEDAR mode is already set to {existing.value}; "
                "run cedarcli mode --clear before selecting another mode"
            )
        path = cls.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as state_file:
            state = {"mode": mode.value}
            if profile is not None:
                state["profile"] = profile.value
            persisted = {
                name: environment[name]
                for name in cls.PERSISTED_ENVIRONMENT
                if environment is not None and name in environment
            }
            if persisted:
                state["environment"] = persisted
            json.dump(state, state_file, indent=2)
            state_file.write("\n")
        os.replace(temporary, path)

    @classmethod
    def clear(cls, force=False):
        current = cls.require_mode()
        cls.require_selected_services_stopped(current)
        deployment_record_cleared = False
        if current in (CedarMode.HYBRID, CedarMode.DOCKER):
            from org.metadatacenter.worker.DockerWorker import DockerWorker

            _version, daemon_error = DockerWorker._docker_server_version()
            projects = set() if daemon_error else DockerWorker.running_compose_projects()
            if projects:
                cleanup = []
                if projects.difference({"cedar-admin"}):
                    cleanup.append("cedarcli docker stop all")
                if "cedar-admin" in projects:
                    cleanup.append("cedarcli docker stop admin")
                raise ModeError(
                    "CEDAR Docker projects are still running: "
                    f"{', '.join(sorted(projects))}; run {' and '.join(cleanup)} "
                    "before clearing the mode. --force cannot bypass running containers"
                )
            if daemon_error and not force:
                raise ModeError(
                    "Docker is unavailable, so cedarcli cannot confirm that the deployment "
                    "is stopped. Start Docker and run cedarcli docker stop all, or use "
                    "cedarcli mode --clear --force if Docker has deliberately been shut down"
                )
            if DockerWorker.active_deployment() is not None:
                DockerWorker._clear_active_deployment()
                deployment_record_cleared = True
        try:
            cls.state_path().unlink()
        except FileNotFoundError:
            raise ModeError("CEDAR mode is not set")
        return deployment_record_cleared

    @classmethod
    def require_selected_services_stopped(cls, mode: CedarMode):
        """Do not discard the only CLI mode capable of stopping its native processes."""
        if mode is CedarMode.NATIVE:
            running_native = cls.running_native_services()
            if running_native:
                raise ModeError(
                    "Native CEDAR applications are still running: "
                    f"{', '.join(sorted(running_native))}; run cedarcli native stop all "
                    "before clearing the mode"
                )
            listeners = cls.host_infrastructure_listeners()
            if listeners:
                raise ModeError(
                    "Native infrastructure is still listening on CEDAR host ports: "
                    f"{', '.join(sorted(listeners))}; run cedarcli native stop infra "
                    "before clearing the mode"
                )
        elif mode is CedarMode.HYBRID:
            from org.metadatacenter.worker.NativeWorker import NativeWorker

            running_native = cls.running_native_services()
            frontends = running_native.intersection(NativeWorker.FRONTENDS)
            if frontends:
                raise ModeError(
                    "Native frontend services are still running: "
                    f"{', '.join(sorted(frontends))}; run cedarcli native stop frontends "
                    "before clearing hybrid mode"
                )
        return mode

    @classmethod
    def profile_environment(cls, surface: str, mode: CedarMode = None,
                            profile: CedarProfile = None):
        mode = mode or cls.require_surface(surface)
        relative = cls.NATIVE_PROFILE if surface == "native" else cls.DOCKER_PROFILE
        profile_path = cls.cedar_home() / relative
        if not profile_path.is_file():
            raise ModeError(
                f"CEDAR {surface} profile does not exist: {profile_path}; the checkout of "
                "cedar-development at CEDAR_HOME is incomplete"
            )

        base_environment = {**os.environ, "CEDAR_HOME": str(cls.cedar_home())}
        if surface == "native":
            base_environment["CEDAR_PROFILE"] = (profile or cls.require_profile()).value
        state = cls.state()
        if state:
            base_environment.update(state.get("environment") or {})

        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1" >/dev/null && env -0',
                "cedarcli-profile",
                str(profile_path),
            ],
            env=base_environment,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise ModeError(
                f"Could not load CEDAR {surface} profile {profile_path}"
                + (f": {detail}" if detail else "")
            )

        environment = {}
        for entry in result.stdout.split(b"\0"):
            if b"=" not in entry:
                continue
            key, value = entry.split(b"=", 1)
            environment[key.decode("utf-8")] = value.decode("utf-8")

        if state:
            environment.update(state.get("environment") or {})

        if surface == "native":
            java_home = cls.java_17_home(environment)
            environment["JAVA_HOME"] = java_home
            environment["PATH"] = f"{java_home}/bin:{environment.get('PATH', '')}"

        if mode is CedarMode.HYBRID and surface == "native":
            host = environment.get("CEDAR_HOST")
            environment["CEDAR_FRONTEND_BIND_HOST"] = "0.0.0.0"
            if host:
                environment["CEDAR_WORKSPACE_FRONTEND_URL"] = f"https://workspace.{host}"
                environment["CEDAR_TEMPLATE_DESIGNER_FRONTEND_URL"] = f"https://designer.{host}"
        if surface == "docker":
            for frontend in cls.FRONTEND_NAMES:
                container = environment.get(f"CEDAR_FRONTEND_{frontend}_CONTAINER_HOST")
                if container:
                    environment[f"CEDAR_FRONTEND_{frontend}_HOST"] = (
                        "host.docker.internal" if mode is CedarMode.HYBRID else container
                    )
            nginx = environment.get("CEDAR_NGINX_HOST")
            if nginx:
                environment["CEDAR_AUTH_HOST_TARGET"] = nginx
            environment["CEDAR_DOCKER_MODE"] = (
                "hybrid" if mode is CedarMode.HYBRID else "full"
            )
        return environment

    @classmethod
    def java_17_home(cls, environment):
        """Where JDK 17 lives on this host, which every native CEDAR process is built and run on.

        macOS answers through java_home. Elsewhere the host's own login environment is the
        authority, and a JDK is looked for only when it said nothing, so a deliberate choice is
        never overridden by a guess.
        """
        java_home_tool = Path("/usr/libexec/java_home")
        if java_home_tool.is_file():
            java = subprocess.run(
                [str(java_home_tool), "-v", "17"],
                capture_output=True,
                text=True,
                check=False,
            )
            if java.returncode or not java.stdout.strip():
                raise ModeError("Java 17 is required for CEDAR native mode")
            return java.stdout.strip()

        configured = environment.get("JAVA_HOME")
        if configured and Path(configured, "bin", "java").is_file():
            return configured
        for candidate in sorted(Path("/usr/lib/jvm").glob("java-17-*")):
            if Path(candidate, "bin", "java").is_file():
                return str(candidate)
        raise ModeError(
            "Java 17 is required for CEDAR native mode, and no JDK 17 was found; export "
            "JAVA_HOME for it before running cedarcli"
        )

    @classmethod
    def apply_profile(cls, surface: str, check_runtime=True):
        mode = cls.require_surface(surface, check_runtime=check_runtime)
        os.environ.update(cls.profile_environment(surface, mode))
        return mode

    @classmethod
    def require_mode(cls):
        mode = cls.current()
        if mode is None:
            raise ModeError(
                "CEDAR mode is not set; run cedarcli mode native|hybrid|docker first"
            )
        return mode

    @classmethod
    def require_surface(cls, surface: str, check_runtime=True):
        mode = cls.require_mode()
        if check_runtime:
            cls.require_runtime_compatible(mode)
        allowed = {
            CedarMode.NATIVE: {"native"},
            CedarMode.HYBRID: {"native", "docker"},
            CedarMode.DOCKER: {"docker"},
        }[mode]
        if surface not in allowed:
            raise ModeError(
                f"CEDAR mode is {mode.value}; cedarcli {surface} commands are not allowed"
            )
        return mode

    @classmethod
    def require_runtime_compatible(cls, mode: CedarMode):
        from org.metadatacenter.model.DockerDeploymentMode import DockerDeploymentMode
        from org.metadatacenter.worker.DockerWorker import DockerWorker

        active = DockerWorker.active_deployment()
        projects = DockerWorker.running_compose_projects()
        expected = {
            CedarMode.NATIVE: None,
            CedarMode.HYBRID: DockerDeploymentMode.HYBRID,
            CedarMode.DOCKER: DockerDeploymentMode.FULL,
        }[mode]

        if mode is CedarMode.NATIVE and (active is not None or projects):
            if active is DockerDeploymentMode.FULL:
                detail = "docker"
            elif active is not None:
                detail = active.value
            else:
                detail = ", ".join(sorted(projects))
            raise ModeError(
                f"CEDAR mode is native, but a Docker deployment is active ({detail}); "
                "clear native mode, select docker or hybrid as recorded, and stop the "
                "Docker deployment before returning to native mode"
            )
        if active is not None and active is not expected:
            configured = "docker" if expected is DockerDeploymentMode.FULL else mode.value
            recorded = "docker" if active is DockerDeploymentMode.FULL else active.value
            raise ModeError(
                f"Configured CEDAR mode {configured} conflicts with the active Docker "
                f"deployment ({recorded}); stop the recorded deployment before changing mode"
            )
        if mode is CedarMode.HYBRID and "cedar-frontend" in projects:
            raise ModeError(
                "CEDAR mode is hybrid, but Docker frontend containers are still running; "
                "run cedarcli docker stop frontends before using the hybrid deployment"
            )
        return mode

    @classmethod
    def require_docker_start_compatible(cls, mode: CedarMode):
        """Reject Docker starts that would collide with verified native host processes."""
        if mode is CedarMode.NATIVE:
            return mode
        running_native = cls.running_native_services()
        if mode is CedarMode.DOCKER and running_native:
            raise ModeError(
                "Native CEDAR services are still running: "
                f"{', '.join(sorted(running_native))}; stop native CEDAR before using docker mode"
            )
        if mode is CedarMode.HYBRID:
            from org.metadatacenter.worker.NativeWorker import NativeWorker

            native_backends = running_native.difference(NativeWorker.FRONTENDS)
            if native_backends:
                raise ModeError(
                    "Native backend services are still running: "
                    f"{', '.join(sorted(native_backends))}; hybrid requires the Docker backend"
                )
        listeners = cls.host_infrastructure_listeners()
        if listeners:
            raise ModeError(
                "Host infrastructure is still listening on Docker-required ports: "
                f"{', '.join(sorted(listeners))}; stop the native infrastructure before "
                "starting the Docker backend"
            )
        return mode

    @classmethod
    def running_native_services(cls):
        controller = cls.cedar_home() / "cedar-development" / "ops" / "cedar-services.sh"
        if not controller.is_file():
            return set()
        environment = {
            **os.environ,
            "CEDAR_HOME": str(cls.cedar_home()),
            "CEDAR_SERVICES_INSPECT_ONLY": "true",
        }
        result = subprocess.run(
            [str(controller), "running"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.strip()
            raise ModeError(
                "Could not inspect native CEDAR processes"
                + (f": {detail}" if detail else "")
            )
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    @classmethod
    def host_infrastructure_listeners(cls):
        controller = cls.cedar_home() / "cedar-development" / "ops" / "cedar-services.sh"
        if not controller.is_file():
            return set()
        environment = {
            **os.environ,
            "CEDAR_HOME": str(cls.cedar_home()),
            "CEDAR_SERVICES_INSPECT_ONLY": "true",
        }
        result = subprocess.run(
            [str(controller), "running-infra"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.strip()
            raise ModeError(
                "Could not inspect native CEDAR infrastructure"
                + (f": {detail}" if detail else "")
            )
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    @classmethod
    def require_native_backend(cls, operation: str):
        mode = cls.require_surface("native")
        if mode is CedarMode.HYBRID:
            raise ModeError(
                f"CEDAR mode is hybrid; native {operation} would operate on the Docker backend"
            )
        return mode

    @classmethod
    def require_native_frontend_services(cls, services, operation: str):
        mode = cls.require_surface("native")
        if mode is not CedarMode.HYBRID:
            return mode
        from org.metadatacenter.worker.NativeWorker import NativeWorker

        requested = tuple(services)
        if not requested or any(service not in NativeWorker.FRONTENDS for service in requested):
            raise ModeError(
                f"CEDAR mode is hybrid; native {operation} is allowed only for frontend services"
            )
        return mode

    @classmethod
    def require_docker_frontends(cls, operation: str):
        mode = cls.require_surface("docker", check_runtime=operation != "stop")
        if mode is CedarMode.HYBRID and operation != "stop":
            raise ModeError(
                f"CEDAR mode is hybrid; Docker frontend {operation} is not allowed"
            )
        return mode

    @classmethod
    def docker_topology(cls, check_runtime=True):
        from org.metadatacenter.model.DockerDeploymentMode import DockerDeploymentMode

        mode = cls.require_surface("docker", check_runtime=check_runtime)
        return (
            DockerDeploymentMode.HYBRID
            if mode is CedarMode.HYBRID
            else DockerDeploymentMode.FULL
        )

    @classmethod
    def validate_mode(cls, mode: CedarMode, profile: CedarProfile = None):
        surfaces = ("native", "docker") if mode is CedarMode.HYBRID else (mode.value,)
        environments = {}
        required = {
            "native": ("CEDAR_HOME", "CEDAR_HOST", "CEDAR_UTIL_BIN"),
            "docker": ("CEDAR_HOME", "CEDAR_HOST", "CEDAR_NGINX_HOST"),
        }
        for surface in surfaces:
            environment = cls.profile_environment(surface, mode, profile)
            missing = [name for name in required[surface] if not environment.get(name)]
            if missing:
                raise ModeError(
                    f"CEDAR {surface} profile is incomplete: {', '.join(missing)}"
                )
            if surface == "native":
                cls.require_profile_invariants(environment, profile or cls.require_profile())
            environments[surface] = environment

        native_controller = cls.cedar_home() / "cedar-development" / "ops" / "cedar-services.sh"
        if "native" in surfaces and not native_controller.is_file():
            raise ModeError(f"Native service controller does not exist: {native_controller}")

        if "docker" in surfaces:
            from org.metadatacenter.worker.DockerWorker import DockerWorker

            if DockerWorker.validate(environment=environments["docker"]):
                raise ModeError("CEDAR Docker Compose validation failed")
        return environments

    # Secrets that ship as "changeme" and stop a server that never had them replaced. Checked for
    # a server, where a placeholder is a misconfiguration, and left alone for a workstation, which
    # may legitimately run parts of the estate it has no credentials for.
    SERVER_SECRETS = (
        "CEDAR_KEYCLOAK_ADMIN_PASSWORD",
        "CEDAR_MONGO_APP_USER_PASSWORD",
        "CEDAR_MYSQL_ROOT_PASSWORD",
        "CEDAR_NEO4J_USER_PASSWORD",
        "CEDAR_SALT_API_KEY",
    )

    @classmethod
    def require_profile_invariants(cls, environment, profile: CedarProfile):
        """What the recorded profile promises about the environment it produced.

        The workstation profile bypasses Keycloak's TLS verification, which is safe only against
        the locally issued .orgx leaves a workstation talks to. A host that took that profile by
        accident reaches its real Keycloak with verification off, and nothing else would say so.
        """
        insecure_tls = environment.get("CEDAR_KEYCLOAK_ALLOW_INSECURE_TLS", "")
        if profile is CedarProfile.SERVER and insecure_tls.lower() == "true":
            raise ModeError(
                "CEDAR profile is server, but the environment bypasses Keycloak TLS "
                "verification; CEDAR_KEYCLOAK_ALLOW_INSECURE_TLS must be false off a workstation"
            )
        if profile is CedarProfile.DEVELOP and insecure_tls.lower() != "true":
            raise ModeError(
                "CEDAR profile is develop, but CEDAR_KEYCLOAK_ALLOW_INSECURE_TLS is "
                f"{insecure_tls or 'unset'}; the local .orgx leaves are not in any truststore"
            )

        target = environment.get("CEDAR_FRONTEND_TARGET")
        if not target:
            raise ModeError("CEDAR native profile set no CEDAR_FRONTEND_TARGET")
        frontend_missing = [
            f"CEDAR_FRONTEND_{target}_{suffix}"
            for suffix in ("UI_HOST", "REST_HOST", "USER1_LOGIN", "USER2_LOGIN")
            if not environment.get(f"CEDAR_FRONTEND_{target}_{suffix}")
        ]
        if frontend_missing:
            raise ModeError(
                "CEDAR frontend settings are incomplete, and the frontend builds require every "
                f"one of them: {', '.join(frontend_missing)}"
            )

        if profile is CedarProfile.SERVER:
            placeholders = [
                name for name in cls.SERVER_SECRETS
                if "changeme" in environment.get(name, "").lower()
            ]
            if placeholders:
                raise ModeError(
                    "CEDAR set-env-internal.sh still holds template placeholders: "
                    f"{', '.join(placeholders)}"
                )
        return profile

    @classmethod
    def adopt_profile(cls, profile: CedarProfile):
        """Record the environment of a host that selected its mode before profiles existed.

        Clearing the mode would mean stopping the applications first, which is too much to ask of
        a running host for a fact it can state without touching them.
        """
        state = cls.state()
        cls.validate_mode(state["mode"], profile)
        path = cls.state_path()
        temporary = path.with_suffix(path.suffix + ".tmp")
        recorded = {"mode": state["mode"].value, "profile": profile.value}
        if state.get("environment"):
            recorded["environment"] = state["environment"]
        with temporary.open("w", encoding="utf-8") as state_file:
            json.dump(recorded, state_file, indent=2)
            state_file.write("\n")
        os.replace(temporary, path)
        return profile

    @classmethod
    def configure(cls, mode: CedarMode, profile: CedarProfile = None):
        existing = cls.current()
        if existing is mode and profile is not None and cls.current_profile() is None:
            return cls.adopt_profile(profile)
        if existing is not None:
            raise ModeError(
                f"CEDAR mode is already set to {existing.value}; "
                "run cedarcli mode --clear before selecting another mode"
            )
        native = mode in (CedarMode.NATIVE, CedarMode.HYBRID)
        if native and profile is None:
            raise ModeError(
                f"CEDAR mode {mode.value} runs native applications; name the environment with "
                "--profile develop|server"
            )
        if not native and profile is not None:
            raise ModeError("CEDAR mode docker runs no native applications; --profile is not used")
        cls.require_runtime_compatible(mode)
        if mode in (CedarMode.HYBRID, CedarMode.DOCKER):
            cls.require_docker_start_compatible(mode)
        requested_environment = {
            name: os.environ[name]
            for name in cls.PERSISTED_ENVIRONMENT
            if name in os.environ
        }
        environments = cls.validate_mode(mode, profile)
        persisted_environment = environments.get("docker", {})
        persisted_environment.update(requested_environment)
        cls.record(mode, persisted_environment, profile)

    @classmethod
    def bootstrap(cls, arguments):
        cls.cedar_home()
        if not arguments or "--help" in arguments or "-h" in arguments:
            return
        surface = arguments[0]
        # Image builds consume only cedar-docker-build's manifest and explicit environment values;
        # they neither inspect nor mutate a deployment. Keeping them profile-free lets immutable
        # train jobs build from their pinned CLI and Docker-builder checkouts in a clean workspace.
        if surface == "docker" and len(arguments) > 1 and arguments[1] == "build":
            return
        if surface in ("native", "docker"):
            cleanup = len(arguments) > 1 and arguments[1] == "stop"
            cls.apply_profile(surface, check_runtime=not cleanup)
            return
        if surface in ("mode", "env"):
            return
        mode = cls.current()
        if mode is not None:
            default_surface = "docker" if mode is CedarMode.DOCKER else "native"
            os.environ.update(cls.profile_environment(default_surface, mode))
