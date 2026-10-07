"""Provider-neutral CODEXDEVTEAM coordination kernel.

Kernel policy stays provider-neutral; runtime adapters and bounded supervisor
orchestration remain explicit opt-in components.
"""

__version__ = "0.2.0"

from .identity import WorkerIdentity
from .host_config import WindowsHostConfig
from .host_runtime import HostRuntimeBindings, build_gate_runner, load_host_runtime
from .host_runner import (build_host_cycle_inputs, closeout_host_cycle,
                          activate_fresh_host, park_configured_host,
                          request_host_stop, run_configured_host_loop,
                          watch_host_stop_request)
from .prompts import render_checker_prompt, render_maker_prompt
from .tasks import TaskState, allowed_transition
from .territory import TerritoryDecision, decide_write, validate_grant
from .firewall import TerritoryPolicy, WriteAuthorization
from .state import HeadLease, LeaseError, PlanProjectionPending, StateStore
from .protocol import PROTOCOL_VERSION, TaskRecord, grant_within_owned, validate_task_set
from .worktrees import GitWorktreeManager, WorktreeError, WorktreeInfo
from .host_commit import (CommitLimits, HostCommitResult, QuiescenceProof,
                          RefusalReason, host_commit)
from .secrets import find_secrets
from .plan_markdown import (ParsedPlan, PlanWriteConflict, parse_plan_markdown,
                            patch_plan_task_state)
from .registry import (REGISTRY_VERSION, STRICT_REQUIRED_CAPABILITIES, RolePolicy,
                       WorkerDefinition, WorkerRegistry)
from .control import CONTROL_VERSION, ControlDecision, ControlMessage, validate_control
from .gate import CheckResult, GateResult, GateRunner
from .review import ReviewVerdict, review_digest, validate_review
from .dispatch import (CapacityObservation, DispatchError, TaskClassPolicy, assign_task,
                       apply_fast_tier_restrictions,
                       eligible_workers, identity_snapshot, worker_readiness,
                       WorkerReadiness)
from .supervisor import (ContinuousSupervisorResult, EscalationNotifier,
                         MakerCloseoutResult, NotificationCycleResult, Supervisor,
                         SupervisorCycleResult, SupervisorLaunchCycleResult,
                         SupervisorPolicy, TaskInvocationCycleResult,
                         TaskCheckerCycleResult)
from .health import (DEFAULT_STAGNATION_POLICY, HealthSignal, StagnationSample,
                     is_stagnant, remedial_kind, stale_signal, update_streak)
from .runtime import (CodexExecAdapter, InvocationRequest, InvocationResult,
                      request_for_worker)
from .onboarding import OnboardingInspection, OnboardingMode, inspect_project
from .sync import (FRAMEWORK_PREFIX, PROJECT_STATE_PREFIX, SyncAction, SyncConflict,
                   SyncPlan, apply_framework_sync, plan_framework_sync,
                   three_way_merge_json)
from .installer import (InstallationConflict, install_devdepartment_sidecar,
                        install_fresh_project, upgrade_codexdevteam_project)
from .test_runs import TestRunCache, TestRunError, TestRunResult
from .memory import EvidenceFact, EvidenceMemory, FactInjection, render_fact_injection
from .miners import (mine_hot_file_facts, mine_passing_test_facts,
                     mine_review_catch_facts, mine_gate_history_facts)
from .compatibility import (CompatibilityDecision, CompatibilityLevel,
                            DEVDEPARTMENT_WAVE_E_SHA, assess_compatibility)
from .handover import (HANDOVER_MAP_VERSION, HandoverPreview,
                       handover_map_template, plan_handover, stage_handover)
from .fast_tier import FastTierResult, FastTierRunner
from .fast_tier_eval import evaluate_run_log_corpus
from .usage import (InvocationSummary, TierOutcome, TierRentComparison, UsageRate,
                    PilotMetrics, compare_tier_rent, measure_pilot,
                    summarize_invocations)
from .publishing import PublicationDecision, decide_publication
from .plan_archive import (ArchiveConflict, ArchiveResult, ArchivedBlock,
                           append_archive_blocks, plan_archive,
                           read_archived_block, NotesRotation,
                           plan_notes_rotation, append_notes_rotation)

__all__ = [
    "__version__",
    "WorkerIdentity",
    "WindowsHostConfig",
    "HostRuntimeBindings",
    "load_host_runtime",
    "build_gate_runner",
    "build_host_cycle_inputs",
    "closeout_host_cycle",
    "run_configured_host_loop",
    "park_configured_host",
    "activate_fresh_host",
    "request_host_stop",
    "watch_host_stop_request",
    "render_maker_prompt",
    "render_checker_prompt",
    "TaskState",
    "allowed_transition",
    "TerritoryDecision",
    "decide_write",
    "validate_grant",
    "TerritoryPolicy",
    "WriteAuthorization",
    "HeadLease",
    "LeaseError",
    "PlanProjectionPending",
    "StateStore",
    "PROTOCOL_VERSION",
    "TaskRecord",
    "validate_task_set",
    "grant_within_owned",
    "GitWorktreeManager",
    "WorktreeError",
    "WorktreeInfo",
    "CommitLimits",
    "HostCommitResult",
    "QuiescenceProof",
    "RefusalReason",
    "host_commit",
    "find_secrets",
    "ParsedPlan",
    "PlanWriteConflict",
    "parse_plan_markdown",
    "patch_plan_task_state",
    "REGISTRY_VERSION",
    "STRICT_REQUIRED_CAPABILITIES",
    "WorkerDefinition",
    "WorkerRegistry",
    "RolePolicy",
    "CONTROL_VERSION",
    "ControlDecision",
    "ControlMessage",
    "validate_control",
    "CheckResult",
    "GateResult",
    "GateRunner",
    "ReviewVerdict",
    "review_digest",
    "validate_review",
    "DispatchError",
    "TaskClassPolicy",
    "CapacityObservation",
    "Supervisor",
    "ContinuousSupervisorResult",
    "SupervisorCycleResult",
    "SupervisorLaunchCycleResult",
    "NotificationCycleResult",
    "MakerCloseoutResult",
    "EscalationNotifier",
    "TaskInvocationCycleResult",
    "TaskCheckerCycleResult",
    "SupervisorPolicy",
    "assign_task",
    "eligible_workers",
    "apply_fast_tier_restrictions",
    "WorkerReadiness",
    "worker_readiness",
    "identity_snapshot",
    "DEFAULT_STAGNATION_POLICY",
    "HealthSignal",
    "StagnationSample",
    "is_stagnant",
    "remedial_kind",
    "stale_signal",
    "update_streak",
    "CodexExecAdapter",
    "InvocationRequest",
    "InvocationResult",
    "request_for_worker",
    "OnboardingInspection",
    "OnboardingMode",
    "inspect_project",
    "FRAMEWORK_PREFIX",
    "PROJECT_STATE_PREFIX",
    "SyncAction",
    "SyncConflict",
    "SyncPlan",
    "apply_framework_sync",
    "plan_framework_sync",
    "three_way_merge_json",
    "InstallationConflict",
    "install_fresh_project",
    "upgrade_codexdevteam_project",
    "install_devdepartment_sidecar",
    "TestRunCache",
    "TestRunError",
    "TestRunResult",
    "EvidenceFact",
    "EvidenceMemory",
    "FactInjection",
    "render_fact_injection",
    "mine_passing_test_facts",
    "mine_hot_file_facts",
    "mine_review_catch_facts",
    "mine_gate_history_facts",
    "CompatibilityDecision",
    "CompatibilityLevel",
    "DEVDEPARTMENT_WAVE_E_SHA",
    "assess_compatibility",
    "HANDOVER_MAP_VERSION",
    "HandoverPreview",
    "handover_map_template",
    "plan_handover",
    "stage_handover",
    "FastTierResult",
    "FastTierRunner",
    "evaluate_run_log_corpus",
    "UsageRate",
    "InvocationSummary",
    "PilotMetrics",
    "measure_pilot",
    "summarize_invocations",
    "TierOutcome",
    "TierRentComparison",
    "compare_tier_rent",
    "PublicationDecision",
    "decide_publication",
    "ArchiveConflict",
    "ArchiveResult",
    "ArchivedBlock",
    "append_archive_blocks",
    "plan_archive",
    "read_archived_block",
    "NotesRotation",
    "plan_notes_rotation",
    "append_notes_rotation",
]
