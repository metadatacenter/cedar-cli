import json
import os
import subprocess
from pathlib import Path

from org.metadatacenter.model.CedarMode import CedarMode


class ModeError(ValueError):
    pass


class ModeManager:
    """Persist the selected CEDAR topology and prepare commands for a bare shell."""

    STATE_FILE = "mode.json"
    NATIVE_PROFILE = "cedar-profile-native-develop.sh"
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
    def record(cls, mode: CedarMode, environment=None):
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
    def profile_environment(cls, surface: str, mode: CedarMode = None):
        mode = mode or cls.require_surface(surface)
        relative = cls.NATIVE_PROFILE if surface == "native" else cls.DOCKER_PROFILE
        profile = cls.cedar_home() / relative
        if not profile.is_file():
            raise ModeError(f"CEDAR {surface} profile does not exist: {profile}")

        base_environment = {**os.environ, "CEDAR_HOME": str(cls.cedar_home())}
        state = cls.state()
        if state:
            base_environment.update(state.get("environment") or {})

        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1" >/dev/null && env -0',
                "cedarcli-profile",
                str(profile),
            ],
            env=base_environment,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise ModeError(
                f"Could not load CEDAR {surface} profile {profile}"
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
                java_home = java.stdout.strip()
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
    def validate_mode(cls, mode: CedarMode):
        surfaces = ("native", "docker") if mode is CedarMode.HYBRID else (mode.value,)
        environments = {}
        required = {
            "native": ("CEDAR_HOME", "CEDAR_HOST", "CEDAR_UTIL_BIN"),
            "docker": ("CEDAR_HOME", "CEDAR_HOST", "CEDAR_NGINX_HOST"),
        }
        for surface in surfaces:
            environment = cls.profile_environment(surface, mode)
            missing = [name for name in required[surface] if not environment.get(name)]
            if missing:
                raise ModeError(
                    f"CEDAR {surface} profile is incomplete: {', '.join(missing)}"
                )
            environments[surface] = environment

        native_controller = cls.cedar_home() / "cedar-development" / "ops" / "cedar-services.sh"
        if "native" in surfaces and not native_controller.is_file():
            raise ModeError(f"Native service controller does not exist: {native_controller}")

        if "docker" in surfaces:
            from org.metadatacenter.worker.DockerWorker import DockerWorker

            if DockerWorker.validate(environment=environments["docker"]):
                raise ModeError("CEDAR Docker Compose validation failed")
        return environments

    @classmethod
    def configure(cls, mode: CedarMode):
        existing = cls.current()
        if existing is not None:
            raise ModeError(
                f"CEDAR mode is already set to {existing.value}; "
                "run cedarcli mode --clear before selecting another mode"
            )
        cls.require_runtime_compatible(mode)
        if mode in (CedarMode.HYBRID, CedarMode.DOCKER):
            cls.require_docker_start_compatible(mode)
        requested_environment = {
            name: os.environ[name]
            for name in cls.PERSISTED_ENVIRONMENT
            if name in os.environ
        }
        environments = cls.validate_mode(mode)
        persisted_environment = environments.get("docker", {})
        persisted_environment.update(requested_environment)
        cls.record(mode, persisted_environment)

    @classmethod
    def bootstrap(cls, arguments):
        cls.cedar_home()
        if not arguments or "--help" in arguments or "-h" in arguments:
            return
        surface = arguments[0]
        if surface in ("native", "docker"):
            cleanup = len(arguments) > 1 and arguments[1] == "stop"
            cls.apply_profile(surface, check_runtime=not cleanup)
            return
        if surface == "mode":
            return
        mode = cls.current()
        if mode is not None:
            default_surface = "docker" if mode is CedarMode.DOCKER else "native"
            os.environ.update(cls.profile_environment(default_surface, mode))
