"""Staging and production peers are different Lambdas."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1] / "lib" / "renglo_ops" / "release"
sys.path.insert(0, str(RELEASE))

from peers import handlers_lambda_function_name  # noqa: E402
from render_deploy_matrix import _peer_rows  # noqa: E402
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

    def test_peer_matrix_follows_enabled_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bom = root / "peers_bom" / "tourbot"
            bom.mkdir(parents=True)
            (bom / "v0.1.1.json").write_text(
                json.dumps({"python": {"apollo-tourbotlink": "0.1.2"}}) + "\n",
                encoding="utf-8",
            )
            data = {
                "peers": {
                    "tourbot": {
                        "compute": "lambda_only",
                        "extensions": ["tourbotlink"],
                        "peers_bom": "0.1.1",
                    }
                },
                "tenants": {
                    "apollo1": {
                        "aws_account": "858045071584",
                        "aws_region": "us-east-1",
                        "stages": {
                            "staging": {"enabled": True},
                            "production": {"enabled": True},
                        },
                    }
                },
            }
            rows = _peer_rows(data, root)
            self.assertEqual([row["deploy_stage"] for row in rows], ["staging", "production"])
            self.assertEqual(
                [row["function_name"] for row in rows],
                ["apollo1-peer-tourbot-staging", "apollo1-peer-tourbot-production"],
            )

            data["tenants"]["apollo1"]["stages"]["production"]["enabled"] = False
            staging_only = _peer_rows(data, root)
            self.assertEqual([row["deploy_stage"] for row in staging_only], ["staging"])

    def test_staging_row_uses_staging_peer_pin(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bom = root / "peers_bom" / "tourbot"
            bom.mkdir(parents=True)
            (bom / "v0.1.1.json").write_text("{}\n", encoding="utf-8")
            (bom / "v0.1.2.json").write_text("{}\n", encoding="utf-8")
            data = {
                "peers": {
                    "tourbot": {
                        "compute": "lambda_only",
                        "extensions": ["tourbotlink"],
                        "peers_bom": "0.1.1",
                    }
                },
                "staging_pins": {"bom": "0.1.2", "peers": {"tourbot": "0.1.2"}},
                "tenants": {
                    "apollo1": {
                        "aws_account": "858045071584",
                        "aws_region": "us-east-1",
                        "stages": {
                            "staging": {"enabled": True},
                            "production": {"enabled": True},
                        },
                    }
                },
            }
            rows = {row["deploy_stage"]: row for row in _peer_rows(data, root)}
            self.assertEqual(rows["staging"]["handlers_bom"], "0.1.2")
            self.assertEqual(rows["staging"]["handlers_bom_file"], "peers_bom/tourbot/v0.1.2.json")
            self.assertEqual(rows["production"]["handlers_bom"], "0.1.1")
            self.assertEqual(rows["production"]["handlers_bom_file"], "peers_bom/tourbot/v0.1.1.json")


if __name__ == "__main__":
    unittest.main()
