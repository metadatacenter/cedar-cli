"""Compatibility worker API; implementations live in train_support."""
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import time
from rich.console import Console
from rich.table import Column, Table
from rich.text import Text
from org.metadatacenter.github_ci import (
    GREEN_CONCLUSIONS,
    GithubCIProbeError,
    latest_runs_by_name,
    probe_exact_commit,
    run_url,
)
from org.metadatacenter import smoke_gate
from org.metadatacenter.npm_policy import npm_user_config_findings
from org.metadatacenter.util.BuildTrain import BuildTrain
from org.metadatacenter.util.Util import Util
from org.metadatacenter.util.NexusCredentials import environment_with_nexus_credentials

from org.metadatacenter.train_support.dispatch import (
    _dry_run,
    dispatch,
)

from org.metadatacenter.train_support.git import (
    _git,
)

from org.metadatacenter.train_support.output import (
    console,
)

from org.metadatacenter.train_support.policy import (
    FAILED_CONCLUSIONS,
    REPOSITORY,
    SourceCIVerdict,
    TRAIN_TITLE_RE,
    VERSION_FILES,
    WATCH_HEARTBEAT_SECONDS,
    WORKFLOW,
)

from org.metadatacenter.train_support.preflight import (
    _configuration_summary,
    _github_preflight,
    _local_configuration_preflight,
    _npm_configuration_preflight,
    _preflight,
    _preflight_failure,
    _publication_targets_preflight,
    _smoke_gate_preflight,
    _source_ci_preflight,
)

from org.metadatacenter.train_support.reporting import (
    _report_open_work,
    report_main_ahead,
    report_source_ci,
)

from org.metadatacenter.train_support.status import (
    _active_subcheck,
    _elapsed,
    _failed_subcheck,
    _group_summary,
    _job_state,
    _render_recovery,
    _render_stage_records,
    _workflow_summary,
    status,
)

from org.metadatacenter.train_support.survey import (
    BranchDivergence,
    _open_work,
    _source_alignment,
    main_ahead_survey,
    source_ci_survey,
)

from org.metadatacenter.train_support.workflow import (
    _active_workflow_runs,
    _dispatched_run_id,
    _newest_dispatched_train,
    _stage_records,
    _stages,
    _workflow_progress,
    _workflow_run,
    _workflow_runs,
)


from org.metadatacenter.train_support import dispatch as _dispatch_component
from org.metadatacenter.train_support import git as _git_component
from org.metadatacenter.train_support import output as _output_component
from org.metadatacenter.train_support import policy as _policy_component
from org.metadatacenter.train_support import preflight as _preflight_component
from org.metadatacenter.train_support import reporting as _reporting_component
from org.metadatacenter.train_support import status as _status_component
from org.metadatacenter.train_support import survey as _survey_component
from org.metadatacenter.train_support import workflow as _workflow_component


class BuildTrainWorker:
    WORKFLOW = WORKFLOW
    REPOSITORY = REPOSITORY
    TRAIN_TITLE_RE = TRAIN_TITLE_RE
    FAILED_CONCLUSIONS = FAILED_CONCLUSIONS
    WATCH_HEARTBEAT_SECONDS = WATCH_HEARTBEAT_SECONDS

    @staticmethod
    def _open_work():
        return _survey_component._open_work()

    @staticmethod
    def _source_alignment():
        return _survey_component._source_alignment()

    @staticmethod
    def _git(root, *arguments):
        return _git_component._git(root, *arguments)

    @staticmethod
    def _report_open_work(findings):
        return _reporting_component._report_open_work(findings)

    @staticmethod
    def _dispatched_run_id(result):
        return _workflow_component._dispatched_run_id(result)

    @staticmethod
    def _stages(version):
        return _workflow_component._stages(version)

    @staticmethod
    def _stage_records(version):
        return _workflow_component._stage_records(version)

    @staticmethod
    def _workflow_runs():
        return _workflow_component._workflow_runs()

    @staticmethod
    def _newest_dispatched_train():
        return _workflow_component._newest_dispatched_train()

    @staticmethod
    def _workflow_run(version):
        return _workflow_component._workflow_run(version)

    @staticmethod
    def _workflow_progress(run_id):
        return _workflow_component._workflow_progress(run_id)

    @staticmethod
    def _job_state(job):
        return _status_component._job_state(job)

    @staticmethod
    def _group_summary(jobs, total):
        return _status_component._group_summary(jobs, total)

    @staticmethod
    def _workflow_summary(payload):
        return _status_component._workflow_summary(payload)

    @staticmethod
    def _failed_subcheck(payload):
        return _status_component._failed_subcheck(payload)

    @staticmethod
    def _active_subcheck(payload):
        return _status_component._active_subcheck(payload)

    @staticmethod
    def _elapsed(seconds):
        return _status_component._elapsed(seconds)

    @staticmethod
    def _render_stage_records(records):
        return _status_component._render_stage_records(records)

    @staticmethod
    def _render_recovery(version, records, workflow):
        return _status_component._render_recovery(version, records, workflow)

    @staticmethod
    def status(version=None, watch=False):
        return _status_component.status(version, watch)

    @staticmethod
    def _configuration_summary():
        return _preflight_component._configuration_summary()

    @staticmethod
    def _github_preflight():
        return _preflight_component._github_preflight()

    @staticmethod
    def _active_workflow_runs():
        return _workflow_component._active_workflow_runs()

    @staticmethod
    def _publication_targets_preflight():
        return _preflight_component._publication_targets_preflight()

    @staticmethod
    def _npm_configuration_preflight():
        return _preflight_component._npm_configuration_preflight()

    @staticmethod
    def source_ci_survey(source=None, reporter=None):
        return _survey_component.source_ci_survey(source, reporter)

    @staticmethod
    def _source_ci_preflight(source=None):
        return _preflight_component._source_ci_preflight(source)

    @staticmethod
    def report_source_ci(show_all=False):
        return _reporting_component.report_source_ci(show_all)
    BranchDivergence = BranchDivergence
    VERSION_FILES = VERSION_FILES

    @staticmethod
    def main_ahead_survey(repositories=None):
        return _survey_component.main_ahead_survey(repositories)

    @staticmethod
    def report_main_ahead(show_all=False):
        return _reporting_component.report_main_ahead(show_all)

    @staticmethod
    def _local_configuration_preflight():
        return _preflight_component._local_configuration_preflight()

    @staticmethod
    def _smoke_gate_preflight(source=None):
        return _preflight_component._smoke_gate_preflight(source)

    @staticmethod
    def _preflight(selected, resume):
        return _preflight_component._preflight(selected, resume)

    @staticmethod
    def _preflight_failure(findings):
        return _preflight_component._preflight_failure(findings)

    @staticmethod
    def _dry_run(selected, resume, command):
        return _dispatch_component._dry_run(selected, resume, command)

    @staticmethod
    def dispatch(resume=None, dry_run=False):
        return _dispatch_component.dispatch(resume, dry_run)
