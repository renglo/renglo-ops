#!/usr/bin/env python3
"""Fetch deploy config from SSM and optionally merge repo secrets / export env."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from lambda_env import filter_lambda_env


def _fetch_ssm_value(name: str, region: str) -> str:
    try:
        import boto3
    except ImportError:
        boto3 = None  # type: ignore

    if boto3 is not None:
        client = boto3.client("ssm", region_name=region)
        resp = client.get_parameter(Name=name, WithDecryption=True)
        return str(resp["Parameter"]["Value"])

    proc = subprocess.run(
        [
            "aws",
            "ssm",
            "get-parameter",
            "--name",
            name,
            "--with-decryption",
            "--region",
            region,
            "--output",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return str(json.loads(proc.stdout)["Parameter"]["Value"])


def _fetch_ssm_json(name: str, region: str) -> dict[str, Any]:
    raw = _fetch_ssm_value(name, region)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"SSM parameter {name} must contain a JSON object")
    return data


def _merge_secrets(payload: dict[str, Any], secret_specs: list[str]) -> None:
    secrets = payload.setdefault("SECRETS", {})
    if not isinstance(secrets, dict):
        secrets = {}
        payload["SECRETS"] = secrets
    for spec in secret_specs:
        if "=" in spec:
            key, env_name = spec.split("=", 1)
        else:
            key, env_name = spec, spec
        val = os.environ.get(env_name.strip(), "").strip()
        if val:
            secrets[key.strip()] = val


def _merge_var_from_ssm(
    payload: dict[str, Any],
    var_key: str,
    parameter_name: str,
    region: str,
) -> None:
    val = _fetch_ssm_value(parameter_name, region).strip()
    if not val:
        return
    vars_block = payload.setdefault("VARS", {})
    if not isinstance(vars_block, dict):
        vars_block = {}
        payload["VARS"] = vars_block
    vars_block[var_key] = val


def _load_platform_env_overlay(path: Path) -> dict[str, str]:
    """Load flat KEY: value YAML/JSON; values coerced to strings."""
    text = path.read_text(encoding="utf-8")
    data: Any
    if path.suffix.lower() in {".yml", ".yaml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML required for --overlay-env YAML files") from exc
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: overlay must be a mapping")
    env = data.get("env")
    if isinstance(env, dict) and "name" in data and "github" in data:
        data = env
    out: dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        out[str(key)] = str(value).strip() if not isinstance(value, (dict, list)) else json.dumps(value)
    return out


def _overlay_platform_env(payload: dict[str, Any], overlay: dict[str, str]) -> None:
    """Merge overlay into VARS; overlay wins on key clash."""
    vars_block = payload.setdefault("VARS", {})
    if not isinstance(vars_block, dict):
        vars_block = {}
        payload["VARS"] = vars_block
    for key, value in overlay.items():
        if value:
            vars_block[key] = value


def _runtime_env(payload: dict[str, Any], stage: str = "") -> dict[str, str]:
    vars_block = payload.get("VARS") or {}
    secrets_block = payload.get("SECRETS") or {}
    if not isinstance(vars_block, dict):
        vars_block = {}
    if not isinstance(secrets_block, dict):
        secrets_block = {}

    merged: dict[str, str] = {}
    for src in (vars_block, secrets_block):
        for k, v in src.items():
            if v is None:
                continue
            s = str(v).strip()
            if s:
                merged[str(k)] = s

    if "FE_BASE_URL" not in merged and merged.get("AMPLIFY_CONSOLE_URL"):
        merged["FE_BASE_URL"] = merged["AMPLIFY_CONSOLE_URL"]
    if stage and "SYS_ENV" not in merged:
        merged["SYS_ENV"] = stage
    elif not stage and "SYS_ENV" not in merged and merged.get("ENVIRONMENT"):
        merged["SYS_ENV"] = merged["ENVIRONMENT"]

    return filter_lambda_env(merged)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch deploy config from SSM Parameter Store")
    parser.add_argument(
        "--parameter",
        required=True,
        help="Primary SSM parameter (JSON: platform-vars or deploy-input)",
    )
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--output", default="", help="Write full deploy_input JSON to this path")
    parser.add_argument(
        "--merge-secret",
        action="append",
        default=[],
        help="Merge env var into SECRETS (KEY or KEY=ENV_NAME); repeatable",
    )
    parser.add_argument(
        "--merge-parameter",
        action="append",
        default=[],
        help="Merge plain SSM string into VARS (VAR_KEY=/ssm/path); repeatable",
    )
    parser.add_argument("--export-env", default="", help="Write shell KEY=value lines (one per line)")
    parser.add_argument(
        "--export-lambda-merge",
        default="",
        help="Write lambda_env_merge.json for deploy_lambda_codedeploy.py",
    )
    parser.add_argument("--stage", default="", help="Stage label for SYS_ENV when exporting lambda merge")
    parser.add_argument(
        "--overlay-env",
        default="",
        help="YAML/JSON file of VARS overlays (platform_env.yml); overlay wins over SSM",
    )
    parser.add_argument(
        "--targets",
        default="",
        help="deploy_targets.yml; when peers: is set, overwrite PEER_EXTENSIONS with the catalog union",
    )
    args = parser.parse_args()

    payload = _fetch_ssm_json(args.parameter, args.region)
    if args.merge_secret:
        _merge_secrets(payload, args.merge_secret)
    for spec in args.merge_parameter:
        if "=" not in spec:
            print(f"Invalid --merge-parameter (expected VAR_KEY=/ssm/path): {spec!r}", file=sys.stderr)
            return 1
        var_key, param_name = spec.split("=", 1)
        var_key = var_key.strip()
        param_name = param_name.strip()
        if not var_key or not param_name:
            print(f"Invalid --merge-parameter: {spec!r}", file=sys.stderr)
            return 1
        _merge_var_from_ssm(payload, var_key, param_name, args.region)

    if args.overlay_env:
        overlay_path = Path(args.overlay_env)
        if not overlay_path.is_file():
            print(f"overlay-env not found: {overlay_path}", file=sys.stderr)
            return 1
        try:
            _overlay_platform_env(payload, _load_platform_env_overlay(overlay_path))
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"overlay-env failed: {exc}", file=sys.stderr)
            return 1
        print(f"Applied overlay from {overlay_path}")

    if args.targets:
        targets_path = Path(args.targets)
        if not targets_path.is_file():
            print(f"targets not found: {targets_path}", file=sys.stderr)
            return 1
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML required for --targets") from exc
        from peers import apply_peer_extensions_from_peers, load_peers

        targets = yaml.safe_load(targets_path.read_text(encoding="utf-8")) or {}
        vars_block = payload.setdefault("VARS", {})
        if not isinstance(vars_block, dict):
            vars_block = {}
            payload["VARS"] = vars_block
        apply_peer_extensions_from_peers(vars_block, load_peers(targets))
        print(f"Derived PEER_EXTENSIONS from {targets_path} peers catalog")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out}")

    runtime = _runtime_env(payload, stage=args.stage)

    if args.export_env:
        lines = [f"{k}={v}" for k, v in sorted(runtime.items())]
        Path(args.export_env).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Wrote {len(lines)} env vars to {args.export_env}")

    if args.export_lambda_merge:
        Path(args.export_lambda_merge).write_text(
            json.dumps(runtime, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote lambda merge ({len(runtime)} keys) to {args.export_lambda_merge}")

    if not args.output and not args.export_env and not args.export_lambda_merge:
        print(json.dumps(payload, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
