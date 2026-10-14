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
from write_peer_routes import (  # noqa: E402
    _ssm_body,
    extension_route_documents,
    stage_route_document,
)


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

    def test_lab_route_document_fits_standard_ssm(self) -> None:
        route = {
            "lambda_function_name": "arbitium0813-peer-lab-staging",
            "lambda_arn": "arn:aws:lambda:us-east-1:982081058012:function:arbitium0813-peer-lab-staging",
            "ecs_cluster": "arbitium0813-peer-lab",
            "ecs_task_definition": "arbitium0813-peer-lab-ecs",
            "ecs_results_bucket": "arbitium0813-peer-lab-ecs-982081058012",
            "region": "us-east-1",
            "subnets": [
                "subnet-0473ee081fbffa66b",
                "subnet-0d8a21bff1a8dd367",
                "subnet-064c2a99b5ab4dec0",
                "subnet-0814beb7b52df72e8",
                "subnet-0d95f814b0f9aca54",
                "subnet-018a70eab61e09995",
            ],
            "security_groups": ["sg-0525c5c8df9f16219"],
            "launch_type": "fargate",
            "network_mode": "awsvpc",
        }
        production = dict(route)
        production["lambda_function_name"] = "arbitium0813-peer-lab-production"
        production["lambda_arn"] = (
            "arn:aws:lambda:us-east-1:982081058012:function:arbitium0813-peer-lab-production"
        )
        documents = extension_route_documents(
            {
                "staging": {
                    "arbitiumlab": dict(route),
                    "arbitiumtriage": dict(route),
                    "arbitiumextra": dict(route),
                },
                "production": {
                    "arbitiumlab": dict(production),
                    "arbitiumtriage": dict(production),
                    "arbitiumextra": dict(production),
                },
            }
        )
        self.assertEqual(
            set(documents),
            {"arbitiumlab", "arbitiumtriage", "arbitiumextra"},
        )
        lab = documents["arbitiumlab"]
        self.assertEqual(
            lab["stages"]["staging"]["arbitiumlab"]["lambda_function_name"],
            "arbitium0813-peer-lab-staging",
        )
        self.assertNotIn("arbitiumtriage", lab["stages"]["staging"])
        for document in documents.values():
            self.assertLessEqual(len(_ssm_body(document)), 4096)

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
