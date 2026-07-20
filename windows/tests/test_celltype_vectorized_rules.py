from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Dict, Sequence

import numpy as np
import pandas as pd


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from native_engine import NativeEngine  # noqa: E402
from src.spatialscope_analysis.celltype_assignment import (  # noqa: E402
    NUC_KEY,
    _classify_simple_celltypes_vectorized,
    marker_name_to_key,
)


def _classify_simple_reference(
    df_cells: pd.DataFrame,
    celltype_cfg: Sequence[Dict[str, Any]],
    *,
    min_pos_pix: int,
    resolve_ambiguous: bool,
    ambiguous_min_probability: float,
    ambiguous_min_gap: float,
) -> Dict[str, Any]:
    """The pre-vectorization scalar rules, retained only as a test oracle."""

    def is_pos(row: pd.Series, marker_key: str) -> bool:
        if marker_key == NUC_KEY:
            return bool(row.get("NUCLEUS_pos", False))
        return bool(row.get(f"{marker_key}_pos", False))

    def marker_probability(row: pd.Series, marker_key: str) -> float:
        if marker_key == NUC_KEY:
            return 1.0 if float(row.get("NUCLEUS_pos_pix", 0)) > 0 else 0.0
        pix = float(row.get(f"{marker_key}_pos_pix", 0.0))
        intensity = float(row.get(f"{marker_key}_sum_intensity", 0.0))
        scale_pix = max(1.0, float(max(min_pos_pix, 1)))
        pix_prob = 1.0 - np.exp(-max(0.0, pix) / scale_pix)
        intensity_prob = 1.0 - np.exp(-max(0.0, intensity) / max(1.0, scale_pix / 2.0))
        return float(np.clip(0.65 * pix_prob + 0.35 * intensity_prob, 0.0, 1.0))

    def matches(cell_type: Dict[str, Any], row: pd.Series) -> bool:
        all_positive = [marker_name_to_key(marker) for marker in cell_type.get("all_pos", [])]
        all_negative = [marker_name_to_key(marker) for marker in cell_type.get("all_neg", [])]
        any_groups = [
            [marker_name_to_key(marker) for marker in group]
            for group in cell_type.get("any_pos_groups", [])
        ]
        if not all(is_pos(row, marker_key) for marker_key in all_positive):
            return False
        if not all(not is_pos(row, marker_key) for marker_key in all_negative):
            return False
        return all(not group or any(is_pos(row, marker_key) for marker_key in group) for group in any_groups)

    def score(cell_type: Dict[str, Any], row: pd.Series) -> float:
        terms = [
            marker_probability(row, marker_name_to_key(marker))
            for marker in cell_type.get("all_pos", [])
        ]
        terms.extend(
            1.0 - marker_probability(row, marker_name_to_key(marker))
            for marker in cell_type.get("all_neg", [])
        )
        for group in cell_type.get("any_pos_groups", []):
            if group:
                terms.append(max(marker_probability(row, marker_name_to_key(marker)) for marker in group))
        if not terms:
            return 0.5
        clipped = [float(np.clip(value, 1e-6, 1.0)) for value in terms]
        return float(np.exp(np.mean(np.log(clipped))))

    result: Dict[str, list[Any]] = {
        "celltype_id": [],
        "celltype": [],
        "matched_celltypes": [],
        "n_matched_celltypes": [],
        "ambiguous_best_type": [],
        "ambiguous_best_probability": [],
        "ambiguous_second_probability": [],
        "ambiguous_probability_gap": [],
        "ambiguous_candidate_probabilities": [],
    }
    for _, row in df_cells.iterrows():
        matched = [
            (type_index, str(cell_type["name"]), cell_type)
            for type_index, cell_type in enumerate(celltype_cfg, start=1)
            if matches(cell_type, row)
        ]
        result["matched_celltypes"].append("|".join(name for _, name, _ in matched))
        result["n_matched_celltypes"].append(len(matched))
        result["ambiguous_best_type"].append("")
        result["ambiguous_best_probability"].append(0.0)
        result["ambiguous_second_probability"].append(0.0)
        result["ambiguous_probability_gap"].append(0.0)
        result["ambiguous_candidate_probabilities"].append("")
        if not matched:
            result["celltype_id"].append(0)
            result["celltype"].append("Unassigned")
            continue
        if len(matched) == 1:
            result["celltype_id"].append(matched[0][0])
            result["celltype"].append(matched[0][1])
            continue

        candidate_scores = [
            (type_index, name, float(max(score(cell_type, row), 1e-6)))
            for type_index, name, cell_type in matched
        ]
        total_score = float(sum(candidate_score for _, _, candidate_score in candidate_scores))
        probabilities = sorted(
            [
                (type_index, name, candidate_score / total_score)
                for type_index, name, candidate_score in candidate_scores
            ],
            key=lambda item: item[2],
            reverse=True,
        )
        best_type_index, best_name, best_probability = probabilities[0]
        second_probability = probabilities[1][2]
        probability_gap = best_probability - second_probability
        result["ambiguous_best_type"][-1] = best_name
        result["ambiguous_best_probability"][-1] = best_probability
        result["ambiguous_second_probability"][-1] = second_probability
        result["ambiguous_probability_gap"][-1] = probability_gap
        result["ambiguous_candidate_probabilities"][-1] = "; ".join(
            f"{name}={probability:.3f}" for _, name, probability in probabilities
        )
        if (
            resolve_ambiguous
            and best_probability >= ambiguous_min_probability
            and probability_gap >= ambiguous_min_gap
        ):
            result["celltype_id"].append(best_type_index)
            result["celltype"].append(best_name)
        else:
            result["celltype_id"].append(0)
            result["celltype"].append("Ambiguous")
    return result


class VectorizedCelltypeRuleTests(unittest.TestCase):
    @staticmethod
    def _cells() -> pd.DataFrame:
        frame = pd.DataFrame(
            {
                "NUCLEUS_pos_pix": [20, 18, 16, 14, 12, 10],
                "NUCLEUS_pos": [True] * 6,
                "A_pos_pix": [8, 7, 0, 9, 0, 8],
                "A_sum_intensity": [5.0, 4.0, 0.0, 8.0, 0.0, 2.0],
                "B_pos_pix": [0, 6, 7, 0, 0, 8],
                "B_sum_intensity": [0.0, 3.0, 7.0, 0.0, 0.0, 4.0],
                "C_pos_pix": [0, 0, 6, 7, 0, 9],
                "C_sum_intensity": [0.0, 0.0, 2.0, 6.0, 0.0, 9.0],
            }
        )
        for marker in ("A", "B", "C"):
            frame[f"{marker}_pos"] = frame[f"{marker}_pos_pix"] >= 5
        return frame

    @staticmethod
    def _rules() -> list[Dict[str, Any]]:
        return [
            {"name": "Empty", "mode": "simple", "all_pos": [], "all_neg": [], "any_pos_groups": []},
            {
                "name": "A and not B",
                "mode": "simple",
                "all_pos": ["Nucleus", "A"],
                "all_neg": ["B"],
                "any_pos_groups": [],
            },
            {
                "name": "B or C",
                "mode": "simple",
                "all_pos": ["Nucleus"],
                "all_neg": [],
                "any_pos_groups": [["B", "C"]],
            },
            {
                "name": "Not C",
                "mode": "simple",
                "all_pos": ["Nucleus"],
                "all_neg": ["C"],
                "any_pos_groups": [],
            },
        ]

    def test_empty_positive_negative_any_group_and_overlap_match_scalar_reference(self) -> None:
        arguments = {
            "min_pos_pix": 5,
            "resolve_ambiguous": True,
            "ambiguous_min_probability": 0.40,
            "ambiguous_min_gap": 0.02,
        }
        expected = _classify_simple_reference(self._cells(), self._rules(), **arguments)
        actual = _classify_simple_celltypes_vectorized(self._cells(), self._rules(), **arguments)
        for column in expected:
            if column.startswith("ambiguous_") and "type" not in column and "candidate" not in column:
                np.testing.assert_array_equal(np.asarray(actual[column]), np.asarray(expected[column]))
            else:
                self.assertEqual(list(actual[column]), list(expected[column]), column)

    def test_unassigned_unique_and_unresolved_ambiguous_match_scalar_reference(self) -> None:
        rules = self._rules()[1:]
        arguments = {
            "min_pos_pix": 5,
            "resolve_ambiguous": False,
            "ambiguous_min_probability": 0.99,
            "ambiguous_min_gap": 0.99,
        }
        expected = _classify_simple_reference(self._cells(), rules, **arguments)
        actual = _classify_simple_celltypes_vectorized(self._cells(), rules, **arguments)
        for column in expected:
            if column.startswith("ambiguous_") and "type" not in column and "candidate" not in column:
                np.testing.assert_array_equal(np.asarray(actual[column]), np.asarray(expected[column]))
            else:
                self.assertEqual(list(actual[column]), list(expected[column]), column)

    def test_persisted_macos_marker_field_names_are_restored(self) -> None:
        normalized = NativeEngine._normalize_celltype(
            {
                "name": "Macrophage",
                "colorHex": "#123456",
                "mode": "simple",
                "allPositiveMarkers": ["Nucleus", "F4_80"],
                "allNegativeMarkers": ["MPO"],
                "anyPositiveGroups": [["CD11b", "CD11c"]],
            },
            0,
        )
        self.assertEqual(normalized["all_pos"], ["Nucleus", "F4_80"])
        self.assertEqual(normalized["all_neg"], ["MPO"])
        self.assertEqual(normalized["any_pos_groups"], [["CD11b", "CD11c"]])


if __name__ == "__main__":
    unittest.main()
