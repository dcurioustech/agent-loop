from __future__ import annotations

from .base import Provider
from . import register


@register
class GeminiProvider(Provider):
    name = "gemini"
    binary = "gemini"
    danger_env = "ALLOW_DANGEROUS_GEMINI"

    def build_argv(self, prompt: str) -> list[str]:
        argv = [self.binary, "-p", prompt]
        if self.dangerous_enabled:
            argv.extend(["--approval-mode", "yolo"])
        return argv
