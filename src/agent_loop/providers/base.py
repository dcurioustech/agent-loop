from __future__ import annotations

import os
import shutil
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class ProviderError(RuntimeError):
    pass


#: Grace period for the output pump to drain after the child exits.
_PUMP_JOIN_SECONDS = 5


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
        """Stream the agent's output to the terminal *and* the run log.

        The child cannot simply inherit our stdout: `safety.tee_stdout_to`
        rebinds `sys.stdout` at the Python level, while a subprocess writes to
        the inherited file descriptor 1 directly. Anything the agent printed
        would reach the terminal and bypass the log entirely — which is most of
        what an audit trail is for. So its output is piped back here and
        re-emitted through `print`, which does go through the tee.

        A pump thread forwards lines as they arrive so long runs stay live
        rather than surfacing only once the agent exits.
        """
        argv = self.build_argv(prompt)
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            errors="replace",
        )

        def _pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                print(line, end="", flush=True)

        pump = threading.Thread(target=_pump, daemon=True)
        pump.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pump.join(timeout=_PUMP_JOIN_SECONDS)
            print(
                f"[agent-loop] {self.name} timed out after {timeout}s",
                flush=True,
            )
            return 124

        pump.join(timeout=_PUMP_JOIN_SECONDS)
        return proc.returncode

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
