"""Staging and production peers are different Lambdas."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1] / "lib" / "renglo_ops" / "release"
sys.path.insert(0, str(RELEASE))

from peers import handlers_lambda_function_name  # noqa: E402
from write_peer_routes import stage_route_document  # noqa: E402


class PeerStageTests(unittest.TestCase):
    def test_function_names_are_per_stage(self) -> None:
        self.assertEqual(
            handlers_lambda_function_name("apollo1", "tourbot", "staging"),
            "apollo1-peer-tourbot-staging",
        )
        self.assertEqual(
            handlers_lambda_function_name("apollo1", "tourbot", "production"),
            "apollo1-peer-tourbot-production",
        )

    def test_route_document_keeps_the_stages_apart(self) -> None:
        document = stage_route_document(
            {
                "staging": {"tourbotlink": {"lambda_function_name": "apollo1-peer-tourbot-staging"}},
                "production": {"tourbotlink": {"lambda_function_name": "apollo1-peer-tourbot-production"}},
            }
        )
        self.assertEqual(
            document["stages"]["staging"]["tourbotlink"]["lambda_function_name"],
            "apollo1-peer-tourbot-staging",
        )
        self.assertEqual(
            document["stages"]["production"]["tourbotlink"]["lambda_function_name"],
            "apollo1-peer-tourbot-production",
        )
        self.assertEqual(
            document["routes"]["tourbotlink"]["lambda_function_name"],
            "apollo1-peer-tourbot-production",
        )


if __name__ == "__main__":
    unittest.main()
