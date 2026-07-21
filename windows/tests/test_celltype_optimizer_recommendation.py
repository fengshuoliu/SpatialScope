from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.spatialscope_analysis.celltype_assignment import (  # noqa: E402
    celltype_assignment_recommendation_metrics,
    rank_celltype_assignment_optimizer_results,
    rank_celltype_assignment_parameter_sweep_results,
    recommend_celltype_assignment_optimizer_result,
    recommend_celltype_assignment_parameter_sweep_result,
    validate_celltype_config_names,
)


class CelltypeOptimizerRecommendationTests(unittest.TestCase):
    @staticmethod
    def _conflicting_results() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "combo_index": 1,
                    "count::T cell": 100,
                    "count::Unassigned": 6,
                    "count::Ambiguous": 0,
                    "assigned_defined_total": 100,
                    "unresolved_total": -999,
                    "n_cells": 106,
                    "error": "",
                },
                {
                    "combo_index": 2,
                    "count::T cell": 70,
                    "count::Unassigned": 0,
                    "count::Ambiguous": 4,
                    "assigned_defined_total": 70,
                    "unresolved_total": 999,
                    "n_cells": 74,
                    "error": "",
                },
                {
                    "combo_index": 3,
                    "count::T cell": 200,
                    "count::Unassigned": 4,
                    "count::Ambiguous": 1,
                    "assigned_defined_total": 200,
                    "n_cells": 205,
                    "error": "",
                },
                {
                    "combo_index": 4,
                    "count::T cell": 500,
                    "count::Unassigned": 0,
                    "count::Ambiguous": 0,
                    "assigned_defined_total": 500,
                    "n_cells": 500,
                    "error": "simulated failure",
                },
            ]
        )

    def test_every_screening_path_minimizes_unassigned_plus_ambiguous(self) -> None:
        results = self._conflicting_results()
        for ranker in (
            rank_celltype_assignment_parameter_sweep_results,
            rank_celltype_assignment_optimizer_results,
        ):
            with self.subTest(ranker=ranker.__name__):
                ranked = ranker(results, defined_celltype_names=["T cell"])
                self.assertEqual(ranked["combo_index"].tolist(), [2, 3, 1])
                self.assertEqual(ranked["unresolved_total"].tolist(), [4, 5, 6])

        for recommender in (
            recommend_celltype_assignment_parameter_sweep_result,
            recommend_celltype_assignment_optimizer_result,
        ):
            with self.subTest(recommender=recommender.__name__):
                recommendation = recommender(
                    results,
                    defined_celltype_names=["T cell"],
                )
                self.assertIsNotNone(recommendation)
                self.assertEqual(int(recommendation["combo_index"]), 2)
                self.assertEqual(
                    celltype_assignment_recommendation_metrics(recommendation),
                    {
                        "combo_index": 2,
                        "ambiguous_cells": 4,
                        "unassigned_cells": 0,
                        "unresolved_cells": 4,
                        "assigned_cells": 70,
                        "sampled_cells": 74,
                    },
                )

    def test_ties_are_numeric_stable_and_independent_of_input_order(self) -> None:
        results = pd.DataFrame(
            [
                {
                    "combo_index": "11",
                    "count::Unassigned": "4",
                    "count::Ambiguous": "0",
                    "assigned_defined_total": "91",
                    "error": "",
                },
                {
                    "combo_index": "9",
                    "count::Unassigned": "4",
                    "count::Ambiguous": "0",
                    "assigned_defined_total": "92",
                    "error": "",
                },
                {
                    "combo_index": "3",
                    "count::Unassigned": "3",
                    "count::Ambiguous": "1",
                    "assigned_defined_total": "500",
                    "error": "",
                },
                {
                    "combo_index": "7",
                    "count::Unassigned": "4",
                    "count::Ambiguous": "0",
                    "assigned_defined_total": "92",
                    "error": "",
                },
            ]
        )
        expected = [7, 9, 11, 3]
        for seed in range(5):
            shuffled = results.sample(frac=1, random_state=seed).reset_index(drop=True)
            ranked = rank_celltype_assignment_optimizer_results(shuffled)
            self.assertEqual([int(value) for value in ranked["combo_index"]], expected)

    def test_invalid_status_counts_cannot_win(self) -> None:
        results = pd.DataFrame(
            [
                {"combo_index": 1, "count::Unassigned": -1, "count::Ambiguous": 0, "error": ""},
                {"combo_index": 2, "count::Unassigned": "bad", "count::Ambiguous": 0, "error": ""},
                {"combo_index": 4, "count::Unassigned": 0.5, "count::Ambiguous": 0, "error": ""},
                {"combo_index": 3, "count::Unassigned": 2, "count::Ambiguous": 1, "error": ""},
            ]
        )
        ranked = rank_celltype_assignment_optimizer_results(results)
        self.assertEqual(ranked["combo_index"].tolist(), [3])

    def test_reserved_and_duplicate_celltype_names_are_rejected(self) -> None:
        for name in ("Unassigned", " unassigned ", "AMBIGUOUS", "Ambiguous"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "reserved"):
                validate_celltype_config_names([{"name": name}])

        with self.assertRaisesRegex(ValueError, "unique"):
            validate_celltype_config_names([{"name": "T cell"}, {"name": " t CELL "}])

        validate_celltype_config_names([{"name": "T cell"}, {"name": "B cell"}])


if __name__ == "__main__":
    unittest.main()
