"""Peer Lambda publish loads the zip from S3."""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RELEASE = Path(__file__).resolve().parents[1] / "lib" / "renglo_ops" / "release"
sys.path.insert(0, str(RELEASE))

from peer_packager import cmd_publish, publish_lambda_code_commands  # noqa: E402
from peers import handlers_results_bucket_name  # noqa: E402


class PublishLambdaFromS3Tests(unittest.TestCase):
    def test_bucket_name_matches_the_peer_stack(self) -> None:
        self.assertEqual(
            handlers_results_bucket_name("arbitium0813", "982081058012", "lab"),
            "arbitium0813-peer-lab-ecs-982081058012",
        )

    def test_commands_load_the_zip_from_s3(self) -> None:
        commands = publish_lambda_code_commands(
            function_name="arbitium0813-peer-lab-staging",
            zip_path=Path("/tmp/lambda_deployment.zip"),
            region="us-east-1",
            bucket="arbitium0813-peer-lab-ecs-982081058012",
            key="lambda-code/arbitium0813-peer-lab-staging.zip",
        )
        flat = " ".join(" ".join(cmd) for cmd in commands)
        self.assertNotIn("--zip-file", flat)
        self.assertEqual(commands[0][1], "s3api")
        self.assertIn("--s3-bucket", commands[1])
        self.assertIn("arbitium0813-peer-lab-ecs-982081058012", commands[1])
        self.assertIn("lambda-code/arbitium0813-peer-lab-staging.zip", commands[1])

    def test_publish_does_not_send_the_zip_inline(self) -> None:
        import peer_packager

        seen: list[list[str]] = []
        with tempfile.TemporaryDirectory() as raw:
            zip_path = Path(raw) / "lambda_deployment.zip"
            zip_path.write_bytes(b"PK")
            args = argparse.Namespace(
                env_name="arbitium0813",
                peer_id="lab",
                stage="staging",
                zip=str(zip_path),
                region="us-east-1",
                account="982081058012",
                s3_bucket="",
                env_json="",
            )
            with (
                mock.patch.object(peer_packager, "_run", lambda cmd, **_kwargs: seen.append(cmd)),
                mock.patch.object(peer_packager, "_wait_function_updated"),
                mock.patch.object(peer_packager, "merge_peer_lambda_env", return_value={"A": "b"}),
            ):
                self.assertEqual(cmd_publish(args), 0)
        joined = "\n".join(" ".join(cmd) for cmd in seen)
        self.assertNotIn("--zip-file", joined)
        self.assertEqual(seen[0][1], "s3api")
        self.assertIn("arbitium0813-peer-lab-ecs-982081058012", seen[0])
        self.assertIn("--s3-key", seen[1])
        self.assertIn("lambda-code/arbitium0813-peer-lab-staging.zip", seen[1])


if __name__ == "__main__":
    unittest.main()
