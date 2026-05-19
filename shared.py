"""Shared dataclasses and utilities."""

from dataclasses import dataclass


@dataclass
class TokenUsage:
    """Track API token usage and estimate costs."""
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, usage):
        """Add usage from API response."""
        if usage:
            self.input_tokens += getattr(usage, 'input_tokens', 0)
            self.output_tokens += getattr(usage, 'output_tokens', 0)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_estimate(self) -> float:
        """Estimate cost in USD (Haiku 4.5 pricing: $1/1M input, $5/1M output)."""
        return (self.input_tokens * 1.0 + self.output_tokens * 5.0) / 1_000_000
