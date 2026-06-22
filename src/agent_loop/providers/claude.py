from __future__ import annotations

from .base import Provider
from . import register


@register
class ClaudeProvider(Provider):
    name = "claude"
    binary = "claude"
    danger_env = "ALLOW_DANGEROUS_CLAUDE"

    def build_argv(self, prompt: str) -> list[str]:
        argv = [self.binary, "-p", prompt]
        if self.dangerous_enabled:
            argv.append("--dangerously-skip-permissions")
        argv.extend(["--output-format", "text"])
        return argv
