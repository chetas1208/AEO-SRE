"""Local Laya runtime. Weights are optional; when disabled or unloadable the runtime reports unavailable."""
from __future__ import annotations

import threading
from typing import Any

import structlog

from app.core.config import get_settings
from app.intelligence.laya.prompts import control_action_options
from app.intelligence.laya.schemas import LayaChoiceResult, LayaState

log = structlog.get_logger()

_LOCK = threading.Lock()
_RUNTIME: "LayaRuntime | None" = None


class LayaRuntime:
    def __init__(self) -> None:
        s = get_settings()
        self.enabled = bool(s.laya_enabled)
        self.checkpoint = (s.laya_checkpoint or "").strip()
        self._ready = False
        self._loading = False
        self._error: str | None = None
        self._model_version = self.checkpoint or "not-configured"
        if self.enabled:
            self._try_load()

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def loading(self) -> bool:
        return self._loading

    @property
    def error(self) -> str | None:
        return self._error

    def _try_load(self) -> None:
        """Optional HF checkpoint load. Failure leaves runtime unavailable (no crash)."""
        self._loading = True
        try:
            # Heavy deps are optional; production can enable when torch/transformers are installed.
            import importlib.util

            if importlib.util.find_spec("transformers") is None or importlib.util.find_spec("torch") is None:
                self._error = "torch/transformers not installed (optional ml extra)"
                return
            # Actual weight load is deferred until benchmark confirms checkpoint; stub marks unavailable.
            self._error = "checkpoint load not wired; set LAYA_ENABLED=false or install weights pipeline"
        except Exception as exc:  # noqa: BLE001
            self._error = repr(exc)
            log.warning("laya.load_failed", error=self._error)
        finally:
            self._loading = False

    def choose(
        self,
        *,
        question: str,
        options: list[str],
        state: LayaState | dict[str, Any] | None = None,
    ) -> LayaChoiceResult:
        if not self.enabled or not self._ready:
            return LayaChoiceResult(
                available=False,
                model_version=self._model_version,
                question=question,
                options=list(options),
                escalation_probability=1.0,
                source="unavailable",
                detail=self._error or "Laya disabled or not loaded",
            )
        # Placeholder until checkpoint pipeline is benchmarked (docs/AGENTMATCH_DECISION_LAYER.md).
        return LayaChoiceResult(
            available=False,
            model_version=self._model_version,
            question=question,
            options=list(options),
            escalation_probability=1.0,
            source="unavailable",
            detail="checkpoint inference not enabled in this build",
        )

    def control_action_prior(self, state: LayaState) -> LayaChoiceResult:
        opts = control_action_options()
        return self.choose(question="control_action", options=opts, state=state)


def get_laya_runtime() -> LayaRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        with _LOCK:
            if _RUNTIME is None:
                _RUNTIME = LayaRuntime()
    return _RUNTIME
