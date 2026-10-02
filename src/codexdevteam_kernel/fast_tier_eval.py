"""Offline evaluation of fast-tier classifications against labeled log corpora."""

from collections import Counter
import json
from typing import Iterable, Mapping

from .fast_tier import _RUN_KINDS, FastTierRunner, _parse_run_classification
from .secrets import find_secrets


def evaluate_run_log_corpus(
    cases: Iterable[Mapping[str, object]],
    predictions: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Measure classification and evidence accuracy without calling a model.

    Case objects have ``case``, ``kind``, ``log``, and ``evidence_line`` fields,
    plus optional ``reset_at``. Predictions have ``case``, ``kind``, and
    ``evidence_line``, plus optional ``reset_at``. Null prediction kind and
    evidence represent an abstention. Missing predictions are also abstentions.
    Unknown and duplicate IDs are rejected.
    """
    gold: dict[str, tuple[str, str, str | None]] = {}
    logs: dict[str, str] = {}
    for item in cases:
        if not isinstance(item, Mapping):
            raise ValueError("every corpus case must be an object")
        if set(item) - {"case", "kind", "log", "evidence_line", "reset_at"}:
            raise ValueError("corpus case contains unknown fields")
        case_id, kind, log, evidence = (
            item.get("case"), item.get("kind"), item.get("log"), item.get("evidence_line")
        )
        if (not isinstance(case_id, str) or not case_id.strip()
                or len(case_id) > 128 or find_secrets(case_id)):
            raise ValueError("corpus case ID must be non-empty, bounded, and redacted")
        if case_id in gold:
            raise ValueError("corpus case IDs must be unique")
        if (not isinstance(kind, str) or kind not in _RUN_KINDS
                or not isinstance(log, str) or not log.strip()
                or len(log) > FastTierRunner.MAX_LOG_CHARS or find_secrets(log)):
            raise ValueError("corpus labels and logs must be valid, bounded, and redacted")
        if (not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 1000
                or evidence not in log.splitlines() or find_secrets(evidence)):
            raise ValueError("gold evidence must be an exact redacted line from its log")
        reset_at = item.get("reset_at")
        if reset_at is not None and not isinstance(reset_at, str):
            raise ValueError("gold reset_at must be a timezone-qualified ISO 8601 timestamp")
        gold_output = {"kind": kind, "evidence_line": evidence}
        if reset_at is not None:
            gold_output["reset_at"] = reset_at
        if _parse_run_classification(json.dumps(gold_output), log) is None:
            raise ValueError("gold classification contains an invalid optional reset_at")
        gold[case_id] = (kind, evidence, reset_at)
        logs[case_id] = log
    if not gold:
        raise ValueError("corpus must contain at least one case")

    predicted: dict[str, tuple[str | None, str | None, str | None]] = {}
    for item in predictions:
        if (not isinstance(item, Mapping)
                or set(item) not in ({"case", "kind", "evidence_line"},
                                     {"case", "kind", "evidence_line", "reset_at"})):
            raise ValueError("every prediction must contain case, kind, evidence_line, and optional reset_at")
        case_id, kind, evidence = item["case"], item["kind"], item["evidence_line"]
        if not isinstance(case_id, str) or case_id not in gold:
            raise ValueError("prediction refers to an unknown corpus case")
        if case_id in predicted:
            raise ValueError("prediction case IDs must be unique")
        if kind is not None and (not isinstance(kind, str) or kind not in _RUN_KINDS):
            raise ValueError("prediction kind must be a supported kind or null")
        if evidence is not None and (
            not isinstance(evidence, str) or len(evidence) > 1000 or find_secrets(evidence)
        ):
            raise ValueError("prediction evidence must be bounded and redacted")
        if (kind is None) != (evidence is None):
            raise ValueError("prediction kind and evidence must both be present or both be null")
        reset_at = item.get("reset_at")
        if reset_at is not None and not isinstance(reset_at, str):
            raise ValueError("prediction reset_at must be a timestamp or null")
        if kind is None and reset_at is not None:
            raise ValueError("an abstention cannot include reset_at")
        if kind is not None:
            candidate = {"kind": kind, "evidence_line": evidence}
            if reset_at is not None:
                candidate["reset_at"] = reset_at
            if _parse_run_classification(json.dumps(candidate), logs[case_id]) is None:
                raise ValueError("prediction contains invalid evidence or optional reset_at")
        predicted[case_id] = (kind, evidence, reset_at)

    confusion: Counter[tuple[str, str]] = Counter()
    evidence_correct = 0
    exact_correct = 0
    reset_at_correct = 0
    reset_at_cases = 0
    for case_id, (expected_kind, expected_evidence, expected_reset_at) in gold.items():
        actual_kind, actual_evidence, actual_reset_at = predicted.get(case_id, (None, None, None))
        confusion[(expected_kind, actual_kind or "abstain")] += 1
        if actual_evidence == expected_evidence:
            evidence_correct += 1
        if expected_reset_at is not None:
            reset_at_cases += 1
            if actual_reset_at == expected_reset_at:
                reset_at_correct += 1
        if (actual_kind == expected_kind and actual_evidence == expected_evidence
                and actual_reset_at == expected_reset_at):
            exact_correct += 1

    total = len(gold)
    covered = sum(1 for kind, _, _ in predicted.values() if kind is not None)
    by_kind: dict[str, dict[str, int | float]] = {}
    f1_values: list[float] = []
    for kind in sorted(_RUN_KINDS):
        true_positive = confusion[(kind, kind)]
        false_positive = sum(count for (expected, actual), count in confusion.items()
                             if actual == kind and expected != kind)
        false_negative = sum(count for (expected, actual), count in confusion.items()
                             if expected == kind and actual != kind)
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_values.append(f1)
        by_kind[kind] = {
            "support": sum(count for (expected, _), count in confusion.items()
                           if expected == kind),
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    labels = sorted(_RUN_KINDS)
    return {
        "case_count": total,
        "prediction_count": len(predicted),
        "coverage": covered / total,
        "abstentions": total - covered,
        "kind_accuracy": sum(confusion[(kind, kind)] for kind in _RUN_KINDS) / total,
        "evidence_accuracy": evidence_correct / total,
        "reset_at_case_count": reset_at_cases,
        "reset_at_accuracy": reset_at_correct / reset_at_cases if reset_at_cases else None,
        "exact_accuracy": exact_correct / total,
        "selective_exact_accuracy": exact_correct / covered if covered else 0.0,
        "macro_f1": sum(f1_values) / len(f1_values),
        "labels": labels,
        "confusion_matrix": {
            expected: {actual: confusion[(expected, actual)]
                       for actual in (*labels, "abstain")}
            for expected in labels
        },
        "per_kind": by_kind,
        "enablement_decision": "not_provided",
    }
