"""Intervention proposal + execution. Public API: propose_interventions, InterventionExecutor, ManualExecutor (default), record_manual_execution,
GitHubPRExecutor (optional), build_diff."""
from app.interventions.changes import FileChange, ManualTask, ProposedChange, apply_change, build_diff
from app.interventions.executor import (
    ExecutionRefused,
    ExecutionResult,
    ExecutionStatus,
    Executor,
    GitHubPRExecutor,
    InterventionExecutor,
    authorize,
    select_executor,
)
from app.interventions.llm import LLMClient, LLMUnavailable
from app.interventions.manual import (
    ManualExecutor,
    ProfoundAgentExecutor,
    executor_capabilities,
    record_manual_execution,
)
from app.interventions.propose import plan_candidates, propose_interventions

__all__ = [
    "ExecutionRefused", "ExecutionResult", "ExecutionStatus", "Executor", "FileChange", "GitHubPRExecutor", "InterventionExecutor",
    "LLMClient", "LLMUnavailable", "ManualExecutor", "ManualTask", "ProfoundAgentExecutor", "ProposedChange", "apply_change",
    "authorize", "build_diff", "executor_capabilities", "plan_candidates", "propose_interventions", "record_manual_execution",
    "select_executor",
]
