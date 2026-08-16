from __future__ import annotations

import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class ProviderError(RuntimeError):
    pass


@dataclass
class CapturedResult:
    """Result of a non-streaming, output-capturing provider invocation.

    Produced only by `Provider.run_captured`, which is used by `agent-loop
    init` to read a provider's full response. The developer/reviewer loop
    keeps using `Provider.run`, which streams straight to the terminal.
    """

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Provider(ABC):
    name: str
    binary: str
    danger_env: str
    #: Flag this CLI uses to pin a model; overridable per provider.
    model_flag: str = "--model"

    def __init__(self, model: Optional[str] = None) -> None:
        #: When None, the underlying CLI resolves its own default model.
        self.model = model or None

    @abstractmethod
    def build_argv(self, prompt: str) -> list[str]:
        ...

    def _model_argv(self) -> list[str]:
        """Argv fragment that pins the model, or empty to defer to the CLI."""
        return [self.model_flag, self.model] if self.model else []

    @property
    def dangerous_enabled(self) -> bool:
        return os.environ.get(self.danger_env, "0") == "1"

    def preflight(self) -> None:
        if shutil.which(self.binary) is None:
            raise ProviderError(
                f"Missing CLI for provider '{self.name}': '{self.binary}' not on PATH."
            )

    def run(self, prompt: str, timeout: int) -> int:
        argv = self.build_argv(prompt)
        try:
            return subprocess.run(argv, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            print(
                f"[agent-loop] {self.name} timed out after {timeout}s",
                flush=True,
            )
            return 124

    def run_captured(self, prompt: str, timeout: int) -> CapturedResult:
        """Run the provider non-interactively, capturing stdout/stderr.

        Unlike `run`, nothing is streamed to the terminal — this is for
        callers (namely `agent-loop init`) that need to parse the provider's
        full response rather than watch it work.
        """
        argv = self.build_argv(prompt)
        try:
            result = subprocess.run(
                argv, timeout=timeout, capture_output=True, text=True
            )
            return CapturedResult(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        except subprocess.TimeoutExpired as e:
            return CapturedResult(
                returncode=124,
                stdout=e.stdout or "",
                stderr=e.stderr or "",
                timed_out=True,
            )
