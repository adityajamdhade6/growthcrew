"""Guardrail metrics: a winner on the primary metric must not damage these."""

from growthcrew.experiments.bayes import rate_samples
from growthcrew.experiments.models import GuardrailData

# Flag when the winner is this likely to be worse than the control by more than the tolerance.
HARM_PROBABILITY = 0.8


def check_guardrails(winner: str, control: str, guardrails: list[GuardrailData]) -> list[str]:
    """Reasons the winner should not be accepted automatically. Empty when it is clean."""
    flags = []
    if winner == control:
        return flags
    for data in guardrails:
        spec = data.spec
        if data.arms:
            by_label = {arm.label: index for index, arm in enumerate(data.arms)}
            if winner not in by_label or control not in by_label:
                continue
            samples = rate_samples(data.arms)
            new, old = samples[:, by_label[winner]], samples[:, by_label[control]]
            if spec.lower_is_better:
                harm = float((new > old * (1 + spec.tolerance)).mean())
            else:
                harm = float((new < old * (1 - spec.tolerance)).mean())
            if harm >= HARM_PROBABILITY:
                arms = data.arms
                flags.append(
                    f"{spec.metric}: '{winner}' is {harm:.0%} likely to be more than "
                    f"{spec.tolerance:.0%} worse than '{control}' "
                    f"({arms[by_label[winner]].rate:.2%} against "
                    f"{arms[by_label[control]].rate:.2%})"
                )
        elif winner in data.values and control in data.values and data.values[control]:
            new, old = data.values[winner], data.values[control]
            change = (new - old) / abs(old)
            worse = change > spec.tolerance if spec.lower_is_better else change < -spec.tolerance
            if worse:
                flags.append(
                    f"{spec.metric}: '{winner}' is {abs(change):.0%} worse than '{control}' "
                    f"({new:.2f} against {old:.2f}); the tolerance is {spec.tolerance:.0%}"
                )
    return flags
