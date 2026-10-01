#!/usr/bin/env python3
"""Build and publish a peer's zip Lambda and optional ECS image from a wheelhouse.

Resource names follow ``{env}-peer-{peerId}``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lambda_env import merge_peer_lambda_env  # noqa: E402
from peers import handlers_unit_name  # noqa: E402
from prepare_handlers_wheelhouse import pin_specs  # noqa: E402

# CDK seed ZipFile is always named index.py (AWS convention). Real handler zips
# ship lambda_router.py plus an index.py shim so index.handler still reaches
# the router if CDK resets Handler. Publish also pins this entry point.
LAMBDA_HANDLER = "lambda_router.lambda_handler"
INDEX_SHIM = _SCRIPTS / "lambda_index_shim.py"


def _load_env_json(path: str) -> dict[str, str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"--env-json must be a JSON object: {path}")
    out: dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        out[str(key).strip()] = str(value)
    return out


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=cwd, env=env)


def _ecs_env_list(env: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in sorted(env.items())]


def sync_peer_ecs_task_env(
    env_name: str,
    peer_id: str,
    extra: dict[str, str] | None,
    *,
    region: str,
    log: bool = True,
) -> str:
    """Register a new task-def revision with the same runtime env as the peer Lambda."""
    import boto3

    ecs = boto3.client("ecs", region_name=region)
    unit = handlers_unit_name(env_name, peer_id)
    family = f"{unit}-ecs"
    runtime_env = merge_peer_lambda_env(env_name, extra, log=log)
    env_list = _ecs_env_list(runtime_env)

    listed = ecs.list_task_definitions(familyPrefix=family, sort="DESC", status="ACTIVE")
    arns = listed.get("taskDefinitionArns") or []
    if not arns:
        raise RuntimeError(f"No ACTIVE ECS task definition for family {family!r}")

    desc = ecs.describe_task_definition(taskDefinition=arns[0])["taskDefinition"]
    containers: list[dict] = []
    for raw in desc["containerDefinitions"]:
        container = dict(raw)
        container.pop("containerArn", None)
        if container.get("name") == "handler":
            container["environment"] = env_list
        containers.append(container)

    reg_kw: dict = {"family": desc["family"], "containerDefinitions": containers}
    for key in (
        "taskRoleArn",
        "executionRoleArn",
        "networkMode",
        "volumes",
        "placementConstraints",
        "requiresCompatibilities",
        "cpu",
        "memory",
        "ipcMode",
        "pidMode",
        "proxyConfiguration",
        "inferenceAccelerators",
        "ephemeralStorage",
        "runtimePlatform",
    ):
        if desc.get(key) is not None:
            reg_kw[key] = desc[key]

    resp = ecs.register_task_definition(**reg_kw)
    new_arn = resp["taskDefinition"]["taskDefinitionArn"]
    print(f"Registered {new_arn} with {len(env_list)} env var(s) for {family}")
    return new_arn


def _wait_function_updated(unit: str, region: str) -> None:
    _run(
        [
            "aws",
            "lambda",
            "wait",
            "function-updated",
            "--function-name",
            unit,
            "--region",
            region,
        ]
    )


def _dockerfile(*, large: bool) -> str:
    entry = (
        'COPY ecs_handler_entrypoint.py /ecs_entrypoint.py\n'
        'WORKDIR /build/output\n'
        'ENTRYPOINT ["python3.12", "/ecs_entrypoint.py"]\n'
        if large
        else "WORKDIR /build/output\n"
    )
    zip_line = "true" if large else "cd /build/output && zip -r /build/lambda_deployment.zip . -q"
    return f"""FROM public.ecr.aws/lambda/python:3.12
RUN microdnf install -y zip && microdnf clean all
WORKDIR /build
COPY wheelhouse/ /build/wheelhouse/
COPY handlers-assets/ /build/assets/
COPY packages.txt /build/packages.txt
COPY lambda_index_shim.py /build/lambda_index_shim.py
RUN python3.12 -m pip install --upgrade pip setuptools wheel -q \\
 && mkdir -p /build/output \\
 && python3.12 -m pip install --no-cache-dir --no-index --find-links /build/wheelhouse --target /build/output -r /build/packages.txt \\
 && cp /build/assets/lambda_router.py /build/output/ \\
 && cp /build/lambda_index_shim.py /build/output/index.py \\
 && if [ -f /build/assets/handlers_config.json ]; then cp /build/assets/handlers_config.json /build/output/; fi \\
 && if [ -d /build/assets/extras ]; then cp -a /build/assets/extras /build/output/extras; fi \\
 && cd /build/output \\
 && find . -type d -name '__pycache__' -exec rm -rf {{}} + 2>/dev/null || true \\
 && find . -type f -name '*.pyc' -delete 2>/dev/null || true \\
 && {zip_line}
{entry}
"""


def cmd_build(args: argparse.Namespace) -> int:
    unit = handlers_unit_name(args.env_name, args.peer_id)
    wheelhouse = Path(args.wheelhouse).resolve()
    assets = Path(args.assets).resolve()
    if not wheelhouse.is_dir():
        print(f"wheelhouse not found: {wheelhouse}", file=sys.stderr)
        return 1
    packages = [p.strip() for p in str(args.packages).split(",") if p.strip()]
    if not packages:
        print("ERROR: --packages is required", file=sys.stderr)
        return 1
    install_specs = pin_specs(packages, wheelhouse)
    print(f"pip install specs: {', '.join(install_specs)}")
    platform = "linux/arm64" if args.local else "linux/amd64"
    tag_suffix = "local" if args.local else "latest"
    kind = "ecs-builder" if args.large else "lambda-builder"
    image = f"{unit}-{kind}:{tag_suffix}"
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="peer-packager-") as raw:
        build_dir = Path(raw)
        shutil.copytree(wheelhouse, build_dir / "wheelhouse")
        shutil.copytree(assets, build_dir / "handlers-assets")
        (build_dir / "packages.txt").write_text(
            "\n".join(install_specs) + "\n", encoding="utf-8"
        )
        if not INDEX_SHIM.is_file():
            print(f"missing {INDEX_SHIM}", file=sys.stderr)
            return 1
        shutil.copy2(INDEX_SHIM, build_dir / "lambda_index_shim.py")
        entry = _SCRIPTS / "ecs_handler_entrypoint.py"
        if args.large:
            if not entry.is_file():
                print(f"missing {entry}", file=sys.stderr)
                return 1
            shutil.copy2(entry, build_dir / "ecs_handler_entrypoint.py")
        (build_dir / "Dockerfile").write_text(_dockerfile(large=args.large), encoding="utf-8")
        _run(
            [
                "docker",
                "build",
                "--platform",
                platform,
                "-f",
                str(build_dir / "Dockerfile"),
                "-t",
                image,
                str(build_dir),
            ]
        )
        if not args.large:
            zip_path = out_dir / "lambda_deployment.zip"
            _run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--platform",
                    platform,
                    "--entrypoint",
                    "/bin/sh",
                    "-v",
                    f"{out_dir}:/output",
                    image,
                    "-c",
                    "cp /build/lambda_deployment.zip /output/ && chmod 644 /output/lambda_deployment.zip",
                ]
            )
            if not zip_path.is_file():
                print("ERROR: zip not extracted", file=sys.stderr)
                return 1
            print(f"Wrote {zip_path}")
        print(f"Image {image}")
        (out_dir / "image.txt").write_text(image + "\n", encoding="utf-8")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    unit = handlers_unit_name(args.env_name, args.peer_id)
    zip_path = Path(args.zip).resolve()
    if not zip_path.is_file():
        print(f"zip not found: {zip_path}", file=sys.stderr)
        return 1
    region = args.region or os.environ.get("AWS_REGION") or "us-east-1"
    _run(
        [
            "aws",
            "lambda",
            "update-function-code",
            "--function-name",
            unit,
            "--zip-file",
            f"fileb://{zip_path}",
            "--region",
            region,
        ]
    )
    _wait_function_updated(unit, region)
    extra: dict[str, str] = {}
    env_json = str(getattr(args, "env_json", "") or "").strip()
    if env_json:
        extra = _load_env_json(env_json)
    try:
        runtime_env = merge_peer_lambda_env(args.env_name, extra, log=True)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: peer Lambda env: {exc}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="peer-publish-") as raw:
        cfg_path = Path(raw) / "update-config.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "FunctionName": unit,
                    "Handler": LAMBDA_HANDLER,
                    "Environment": {"Variables": runtime_env},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        _run(
            [
                "aws",
                "lambda",
                "update-function-configuration",
                "--cli-input-json",
                f"file://{cfg_path}",
                "--region",
                region,
            ]
        )
    _wait_function_updated(unit, region)
    print(
        f"Published {unit} handler={LAMBDA_HANDLER} "
        f"env={len(runtime_env)} keys"
    )
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    unit = handlers_unit_name(args.env_name, args.peer_id)
    region = args.region or os.environ.get("AWS_REGION") or "us-east-1"
    account = args.account.strip()
    if not account:
        account = subprocess.check_output(
            ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
            text=True,
        ).strip()
    repo = f"{unit}-ecs"
    image = args.image.strip() or f"{unit}-ecs-builder:latest"
    ecr = f"{account}.dkr.ecr.{region}.amazonaws.com/{repo}:latest"
    login = subprocess.check_output(
        ["aws", "ecr", "get-login-password", "--region", region],
        text=True,
    )
    subprocess.run(
        ["docker", "login", "--username", "AWS", "--password-stdin", f"{account}.dkr.ecr.{region}.amazonaws.com"],
        input=login,
        text=True,
        check=True,
    )
    _run(["docker", "tag", image, ecr])
    _run(["docker", "push", ecr])
    print(f"Pushed {ecr}")

    env_json = str(getattr(args, "env_json", "") or "").strip()
    if env_json:
        extra = _load_env_json(env_json)
        try:
            sync_peer_ecs_task_env(
                args.env_name,
                args.peer_id,
                extra,
                region=region,
                log=True,
            )
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"ERROR: peer ECS task env: {exc}", file=sys.stderr)
            return 1
    else:
        print(
            f"task definition family {unit}-ecs unchanged "
            "(pass --env-json to sync runtime env from deploy-input)"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Peer zip / ECS packager (bom-helper)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_identity(p: argparse.ArgumentParser) -> None:
        p.add_argument("--env-name", required=True, help="Tenant AWS prefix")
        p.add_argument("--peer-id", required=True, help="Catalog peer id")

    b = sub.add_parser("build", help="Docker-build zip and/or ECS image from wheelhouse")
    add_identity(b)
    b.add_argument("--wheelhouse", required=True)
    b.add_argument("--assets", required=True)
    b.add_argument("--packages", required=True, help="Comma-separated dist names to pip install")
    b.add_argument("--out", default=".peer-build")
    b.add_argument("--large", action="store_true", help="ECS image with ecs_handler_entrypoint")
    b.add_argument("--local", action="store_true", help="linux/arm64 :local tag")

    p = sub.add_parser("publish", help="Update peer Lambda code from zip")
    add_identity(p)
    p.add_argument("--zip", required=True)
    p.add_argument("--region", default="")
    p.add_argument(
        "--env-json",
        default="",
        help="SSM deploy-input VARS/SECRETS JSON; table names always come from --env-name",
    )

    s = sub.add_parser("sync-ecs-env", help="Register a new ECS task-def revision with deploy-input runtime env")
    add_identity(s)
    s.add_argument("--region", default="")
    s.add_argument(
        "--env-json",
        required=True,
        help="SSM deploy-input VARS/SECRETS JSON (same as publish --env-json)",
    )

    u = sub.add_parser("push", help="Tag/push ECS image to the peer ECR repo")
    add_identity(u)
    u.add_argument("--image", default="")
    u.add_argument("--region", default="")
    u.add_argument("--account", default="")
    u.add_argument(
        "--env-json",
        default="",
        help="SSM deploy-input VARS/SECRETS JSON; registers a new task-def revision with runtime env",
    )

    args = parser.parse_args()
    if args.cmd == "build":
        return cmd_build(args)
    if args.cmd == "publish":
        return cmd_publish(args)
    if args.cmd == "sync-ecs-env":
        region = args.region or os.environ.get("AWS_REGION") or "us-east-1"
        try:
            sync_peer_ecs_task_env(
                args.env_name,
                args.peer_id,
                _load_env_json(args.env_json),
                region=region,
                log=True,
            )
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"ERROR: peer ECS task env: {exc}", file=sys.stderr)
            return 1
        return 0
    return cmd_push(args)


if __name__ == "__main__":
    raise SystemExit(main())
