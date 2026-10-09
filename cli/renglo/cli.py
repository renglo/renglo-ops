"""renglo command. CDK is imported only by the stack modules, not here."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from renglo.help import render
from renglo_ops.model.errors import RengloOpsError
from renglo_ops.model.local import find_local, load_local, refuse_tool_output, workspace_root
from renglo_ops.model.registry import load_registry
from renglo_ops.model.tenant import (
    Account,
    Tenant,
    dump_tenant,
    find_tenant_file,
    load_tenant,
    tenant_from_dict,
    tenant_to_dict,
)


def _die(exc: RengloOpsError) -> None:
    print(str(exc), file=sys.stderr)
    raise SystemExit(2)


def _tenant_from_env():
    raw = os.environ.get("RENGLO_TENANT", "").strip()
    path = Path(raw).expanduser().resolve() if raw else find_tenant_file(Path.cwd())
    return load_tenant(path)


def _profile_region() -> tuple[str, str]:
    local_path = find_local(Path.cwd())
    local = load_local(local_path) if local_path else None
    profile = os.environ.get("AWS_PROFILE", "").strip() or (local.profile if local else "")
    region = os.environ.get("AWS_REGION", "").strip() or (local.region if local else "")
    return profile, region


def _cmd_doctor() -> int:
    from renglo.doctor import doctor_exit_code, format_doctor, run_doctor

    checks = run_doctor(Path.cwd())
    print(format_doctor(checks), end="")
    return doctor_exit_code(checks)


def _cmd_status(args: argparse.Namespace | None = None) -> int:
    tenant = _tenant_from_env()
    profile, region = _profile_region()
    print(f"name: {tenant.name}")
    print(f"github: {tenant.github_repo}")
    print(f"platform: {tenant.platform or '(unset)'}")
    print(f"release: bom {tenant.release_bom or '-'}  console {tenant.release_console or '-'}")
    for key, account in tenant.accounts.items():
        state = "enabled" if account.enabled else "disabled"
        print(f"account {key}: {account.id or '(no id)'} {account.region} {state}")
    print(f"profile: {profile or '(unset)'}")
    if profile and region:
        print(f"region: {region}")
    from renglo.operate import format_status_urls

    ssm_client = None
    if profile:
        session = _aws_session(
            (args.profile if args is not None else None) or profile,
            region or tenant.primary_account().region,
        )
        ssm_client = session.client("ssm")
    print(format_status_urls(ssm_client, tenant.name), end="")
    if args is not None and args.live:
        from renglo.operate import format_live_status

        session = _aws_session(args.profile or profile, region or tenant.primary_account().region)
        print(
            format_live_status(
                tenant,
                session.client("cloudformation"),
                session.client("ssm"),
            ),
            end="",
        )
    return 0


def _cmd_show() -> int:
    import json

    tenant = _tenant_from_env()
    print(json.dumps(tenant_to_dict(tenant), indent=2))
    return 0


def _cmd_local_config(args: argparse.Namespace) -> int:
    tenant = _tenant_from_env()
    profile, region = _profile_region()
    profile = args.profile or profile
    region = args.region or region
    written = __import__("renglo.state", fromlist=["local_config"]).local_config(
        tenant,
        profile=profile,
        region=region,
        apply=args.apply,
        dry_run=args.dry_run,
    )
    for key, value in written.items():
        print(f"{key}: {value}")
    return 0


def _ask(label: str, *, default: str, interactive: bool) -> str:
    if not interactive:
        return default
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{label}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def _platform_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    for dist in ("renglo-ops", "renglo"):
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
    return "0.1.0"


def _looks_like_bom(path: Path) -> bool:
    return (path / "renglo.yaml").is_file() or (path / "bom").is_dir()


def _bom_default(here: Path, repo: str) -> Path:
    """Where the BOM checkout is, guessed from the directory you ran the command in."""
    from renglo_ops.model.local import is_tool_checkout

    name = repo.split("/")[-1].strip() or "bom"
    if _looks_like_bom(here) and not is_tool_checkout(here):
        return here
    if is_tool_checkout(here):
        return here.parent / name
    if (here / name).is_dir():
        return here / name
    if (here / "ops").is_dir():
        return here / "ops" / name
    return here / name


def _clone_bom(repo: str, dest: Path) -> bool:
    """Best effort: the BOM repo usually exists on GitHub before this command runs."""
    url = f"https://github.com/{repo}.git"
    print(f"cloning {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cloned = subprocess.run(["git", "clone", "--quiet", url, str(dest)])
    if cloned.returncode == 0:
        return True
    print(f"could not clone {url}; creating {dest} locally instead")
    return False


def _is_placeholder(path: Path) -> bool:
    """The example-bom template ships a renglo.yaml nobody has filled in yet."""
    try:
        tenant = load_tenant(path)
    except RengloOpsError:
        return True
    return "CHANGE_ME" in f"{tenant.name} {tenant.github_repo}".upper()


def _local_workspace_for_bom(bom_root: Path) -> Path:
    """Where .renglo/local.yaml belongs: product workspace root, else BOM parent."""
    for candidate in [bom_root, *bom_root.parents]:
        if (candidate / "console").is_dir() and (candidate / "dev").is_dir():
            return candidate
    return bom_root.parent


def _ensure_ignored(workspace: Path) -> None:
    """.renglo/ holds machine state. Keep it out of whatever repo owns the workspace."""
    if not (workspace / ".git").exists():
        return
    path = workspace / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if any(line.strip().rstrip("/") == ".renglo" for line in text.splitlines()):
        return
    separator = "" if not text or text.endswith("\n") else "\n"
    path.write_text(f"{text}{separator}.renglo/\n", encoding="utf-8")
    print(f"added .renglo/ to {path}")


def _cmd_config_init(args: argparse.Namespace) -> int:
    interactive = not args.no_input and sys.stdin.isatty()
    here = Path.cwd().resolve()

    name = args.name or _ask("Environment name", default="", interactive=interactive)
    if not name:
        raise RengloOpsError("environment name is required (--name)")
    repo = args.github_repo or _ask(
        "BOM repository on GitHub (ORG/REPO)",
        default=_origin_slug(here),
        interactive=interactive,
    )
    owner, _, repo_name = repo.partition("/")
    if not owner.strip() or not repo_name.strip():
        raise RengloOpsError("github repo must be ORG/REPO (--github-repo)")
    email_from = args.email_from or _ask(
        "System email address", default="", interactive=interactive
    )
    if not email_from:
        raise RengloOpsError("sender address is required (--email-from)")
    identity = args.email_identity or _ask(
        "System email identity type, email or domain", default="email", interactive=interactive
    )
    account = args.account or _ask(
        "AWS account id, blank to fill in later", default="", interactive=interactive
    )
    if account and (len(account) != 12 or not account.isdigit()):
        raise RengloOpsError("aws account id must be 12 digits (--account)")
    region = args.region or _ask("AWS region", default="us-east-1", interactive=interactive)
    profile = args.profile or os.environ.get("AWS_PROFILE", "").strip()
    profile = _ask(
        "AWS CLI profile on this machine, blank to decide later",
        default=profile,
        interactive=interactive,
    )

    platform = _platform_version()
    tenant = Tenant(
        name=name,
        github_repo=repo,
        email_from=email_from,
        email_identity=identity,
        platform=platform,
        accounts={
            "staging": Account(id=account, region=region, enabled=True),
            "production": Account(id="", region=region, enabled=False),
        },
        release_bom="0.1.0",
        release_console="0.1.0",
    )
    document = tenant_to_dict(tenant)
    tenant_from_dict(document)

    bom_root = (
        Path(args.bom).expanduser().resolve() if args.bom else _bom_default(here, repo)
    )
    tenant_path = bom_root / "renglo.yaml"
    refuse_tool_output(tenant_path)
    if not bom_root.exists() and interactive:
        _clone_bom(repo, bom_root)
    if tenant_path.exists() and not args.force and not _is_placeholder(tenant_path):
        raise RengloOpsError(
            f"{tenant_path} already describes an environment. Pass --force to replace it"
        )
    bom_root.mkdir(parents=True, exist_ok=True)
    tenant_path.write_text(dump_tenant(tenant), encoding="utf-8")
    print(f"wrote {tenant_path}")

    local_workspace = _local_workspace_for_bom(bom_root)
    local_path = local_workspace / ".renglo" / "local.yaml"
    try:
        target = str(bom_root.relative_to(local_workspace))
    except ValueError:
        target = str(bom_root)
    if local_path.exists() and not args.force:
        print(f"kept {local_path}")
    else:
        refuse_tool_output(local_path)
        lines = [f"tenant: {target}"]
        if profile:
            lines.append(f"profile: {profile}")
        lines.append(f"region: {region}")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {local_path}")
        _ensure_ignored(local_workspace)

    if not account:
        print("accounts.staging.id is empty. Fill it in before you deploy.")
    print("")
    print("Done. This shell, and every new one:")
    print(f"  source {_venv_activate()}")
    print("  renglo status")
    return 0


def _venv_activate() -> str:
    """The activate script of the venv running this command, for the closing hint."""
    prefix = Path(sys.prefix)
    activate = prefix / "bin" / "activate"
    if not activate.is_file():
        return "<renglo-ops>/.venv/bin/activate"
    try:
        return str(activate.relative_to(Path.cwd()))
    except ValueError:
        return str(activate)


def _cmd_check() -> int:
    tenant = _tenant_from_env()
    if tenant.path is None:
        print("ok")
        return 0
    root = tenant.path.parent
    slug = _origin_slug(root)
    if slug and slug != tenant.github_repo:
        print(f"github.repo is {tenant.github_repo} but origin is {slug}", file=sys.stderr)
        return 1
    print(f"ok {tenant.name} {tenant.github_repo}")
    return 0


def _origin_slug(root: Path) -> str:
    if not (root / ".git").exists():
        return ""
    result = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    url = result.stdout.strip()
    if url.endswith(".git"):
        url = url[:-4]
    if ":" in url and "@" in url and "github.com" in url:
        return url.split(":", 1)[1]
    if "github.com/" in url:
        return url.split("github.com/", 1)[1]
    return ""


def _aws_session(profile: str, region: str):
    import boto3

    kwargs: dict[str, str] = {}
    if profile:
        kwargs["profile_name"] = profile
    if region:
        kwargs["region_name"] = region
    return boto3.Session(**kwargs)


def _run_cdk(
    module: str,
    *,
    profile: str,
    extra_env: dict[str, str],
    dry_run: bool,
    stacks: list[str] | None = None,
) -> int:
    workspace = workspace_root(Path.cwd())
    outdir = workspace / ".renglo" / "cdk.out"
    refuse_tool_output(outdir)
    env = os.environ.copy()
    if profile:
        env["AWS_PROFILE"] = profile
    env.setdefault("RENGLO_WORKSPACE", str(workspace))
    env["CDK_OUTDIR"] = str(outdir)
    env.update(extra_env)
    synth = [sys.executable, "-m", module]
    cdk = shutil.which("cdk")
    deploy = [
        cdk or "cdk",
        "deploy",
        "--app",
        str(outdir),
        "--require-approval",
        "broadening",
    ]
    if stacks:
        deploy.extend(stacks)
    else:
        deploy.append("--all")
    print(" ".join(synth))
    print(" ".join(deploy))
    if dry_run:
        return 0
    outdir.mkdir(parents=True, exist_ok=True)
    synthesized = subprocess.run(synth, env=env, cwd=workspace)
    if synthesized.returncode != 0:
        return synthesized.returncode
    if not cdk:
        print(f"cdk CLI is not on PATH; templates are in {outdir}", file=sys.stderr)
        return 2
    return subprocess.run(deploy, env=env, cwd=workspace).returncode


def _cmd_stack(args: argparse.Namespace) -> int:
    from renglo.operate import hub_stack_name, parse_stacks

    tenant = _tenant_from_env()
    profile, _region = _profile_region()
    names = [hub_stack_name(tenant.name, part) for part in parse_stacks(args.stack)]
    return _run_cdk(
        "renglo_ops.cdk.hub",
        profile=args.profile or profile,
        extra_env={"RENGLO_TENANT": str(tenant.path)},
        dry_run=args.dry_run,
        stacks=names,
    )


def _cmd_peer(args: argparse.Namespace) -> int:
    tenant = _tenant_from_env()
    profile, _region = _profile_region()
    extra = {"RENGLO_TENANT": str(tenant.path)}
    if args.peer_id:
        extra["PEER_ID"] = args.peer_id
    return _run_cdk(
        "renglo_ops.cdk.peer",
        profile=args.profile or profile,
        extra_env=extra,
        dry_run=args.dry_run,
    )


def _resolve_registry_path(raw: str) -> Path:
    registry = (raw or "").strip() or os.environ.get("RENGLO_REGISTRY", "").strip()
    if not registry:
        local_path = find_local(Path.cwd())
        if local_path:
            registry = load_local(local_path).registry.strip()
    if not registry:
        raise RengloOpsError(
            "pass --registry PATH or set registry in .renglo/local.yaml"
        )
    return Path(registry).expanduser().resolve()


def _require_named_aws_profile(profile: str, *, verb: str) -> str:
    chosen = profile.strip()
    if not chosen or chosen == "default":
        raise RengloOpsError(
            f"Refusing {verb} without a named AWS profile.\n"
            "  export AWS_PROFILE=<registry-profile>\n"
            f"  renglo registry {verb} --profile <registry-profile>"
        )
    return chosen


def _cmd_registry_deploy(args: argparse.Namespace) -> int:
    profile, _region = _profile_region()
    registry_path = _resolve_registry_path(args.registry)
    profile = _require_named_aws_profile(args.profile or profile, verb="deploy")
    return _run_cdk(
        "renglo_ops.cdk.registry",
        profile=profile,
        extra_env={"RENGLO_REGISTRY": str(registry_path)},
        dry_run=args.dry_run,
    )


def _cmd_registry_show(args: argparse.Namespace) -> int:
    from renglo.operate import format_registry_show

    registry = load_registry(_resolve_registry_path(args.registry))
    profile, region = _profile_region()
    profile = _require_named_aws_profile(args.profile or profile, verb="show")
    region = (args.region or region or os.environ.get("AWS_REGION", "")).strip()
    session = _aws_session(profile, region)
    if not region:
        region = session.region_name or "us-east-1"
    account = session.client("sts").get_caller_identity()["Account"]
    print(
        format_registry_show(
            registry,
            session.client("cloudformation"),
            account_id=str(account),
            region=region,
            profile=profile,
        ),
        end="",
    )
    return 0


def _cmd_registry_check(args: argparse.Namespace) -> int:
    from renglo.operate import format_registry_check

    registry = load_registry(_resolve_registry_path(args.registry))
    profile, region = _profile_region()
    profile = _require_named_aws_profile(args.profile or profile, verb="check")
    region = (args.region or region or os.environ.get("AWS_REGION", "")).strip()
    session = _aws_session(profile, region)
    if not region:
        region = session.region_name or "us-east-1"
    account = session.client("sts").get_caller_identity()["Account"]
    text, published = format_registry_check(
        registry,
        session.client("codeartifact"),
        package=args.package,
        version=args.version,
        fmt=args.format,
        account_id=str(account),
        region=region,
    )
    print(text, end="")
    return 0 if published else 1


def _cmd_publish(args: argparse.Namespace) -> int:
    package = Path(args.path).resolve()
    command = [sys.executable, "-m", "build", str(package)]
    print(" ".join(command))
    if args.dry_run:
        print("twine upload dist/*")
        return 0
    built = subprocess.run(command)
    if built.returncode != 0:
        return built.returncode
    twine = shutil.which("twine")
    if not twine:
        print("twine is not on PATH", file=sys.stderr)
        return 2
    return subprocess.run([twine, "upload", *sorted((package / "dist").glob("*"))]).returncode


def _operator_clients(args: argparse.Namespace):
    tenant = _tenant_from_env()
    profile, region = _profile_region()
    profile = args.profile or profile
    region = getattr(args, "region", "") or region or tenant.primary_account().region
    session = _aws_session(profile, region)
    return tenant, profile, region, session


def _cmd_stack_status(args: argparse.Namespace) -> int:
    from renglo.operate import format_hub_status, parse_stacks

    tenant, _profile, _region, session = _operator_clients(args)
    which = parse_stacks(args.stack)
    print(format_hub_status(tenant, session.client("cloudformation"), which), end="")
    return 0


def _cmd_stack_destroy(args: argparse.Namespace) -> int:
    from renglo.operate import delete_stacks, hub_stack_name, parse_stacks, refuse_destroy_a_while_b_exists

    if not args.yes and not args.dry_run:
        raise RengloOpsError("pass --yes to destroy a stack")
    tenant, _profile, _region, session = _operator_clients(args)
    which = parse_stacks(args.stack, default_both=False)
    cfn = session.client("cloudformation")
    refuse_destroy_a_while_b_exists(cfn, tenant.name, which)
    names = [hub_stack_name(tenant.name, part) for part in reversed(which)]
    if args.dry_run:
        for name in names:
            print(f"delete {name}")
        return 0
    for line in delete_stacks(cfn, names):
        print(line)
    return 0


def _cmd_account_bootstrap(args: argparse.Namespace) -> int:
    tenant = _tenant_from_env()
    profile, region = _profile_region()
    profile = args.profile or profile
    account = tenant.primary_account()
    if not account.id:
        raise RengloOpsError("accounts.staging.id is empty")
    region = args.region or region or account.region
    cdk = shutil.which("cdk") or "cdk"
    command = [cdk, "bootstrap", f"aws://{account.id}/{region}"]
    if profile:
        command.extend(["--profile", profile])
    print(" ".join(command))
    if args.dry_run:
        return 0
    return subprocess.run(command).returncode


def _cmd_peer_list() -> int:
    from renglo.operate import format_peer_list

    print(format_peer_list(_tenant_from_env()), end="")
    return 0


def _cmd_peer_show(args: argparse.Namespace) -> int:
    from renglo.operate import format_peer_show

    print(format_peer_show(_tenant_from_env(), args.peer), end="")
    return 0


def _cmd_peer_status(args: argparse.Namespace) -> int:
    from renglo.operate import format_peer_status

    tenant, _profile, _region, session = _operator_clients(args)
    print(
        format_peer_status(tenant, session.client("cloudformation"), args.peer_id),
        end="",
    )
    return 0


def _cmd_peer_destroy(args: argparse.Namespace) -> int:
    from renglo.operate import delete_stacks
    from renglo_ops.release.peers import peer_stack_name

    if not args.yes and not args.dry_run:
        raise RengloOpsError("pass --yes to destroy a peer")
    if not args.peer_id:
        raise RengloOpsError("pass --peer-id")
    tenant, _profile, _region, session = _operator_clients(args)
    if args.peer_id not in tenant.placement_peers:
        raise RengloOpsError(f"no peer {args.peer_id} in placement.peers")
    name = peer_stack_name(tenant.name, args.peer_id)
    if args.dry_run:
        print(f"delete {name}")
        return 0
    for line in delete_stacks(session.client("cloudformation"), [name]):
        print(line)
    return 0


def _cmd_extension_tree() -> int:
    from renglo.operate import format_extension_tree

    print(format_extension_tree(_tenant_from_env()), end="")
    return 0


def _cmd_extension_show(args: argparse.Namespace) -> int:
    from renglo.operate import format_extension_show

    print(format_extension_show(_tenant_from_env(), args.handle), end="")
    return 0


def _cmd_state_live(args: argparse.Namespace) -> int:
    from renglo.operate import format_state_live

    tenant, _profile, _region, session = _operator_clients(args)
    print(format_state_live(session.client("ssm"), tenant.name), end="")
    return 0


def _cmd_email_sender(args: argparse.Namespace) -> int:
    from renglo.operate import identity_status, ses_account

    tenant, _profile, _region, session = _operator_clients(args)
    identity = tenant.email_from
    if tenant.email_identity == "domain":
        identity = tenant.email_from.split("@", 1)[-1]
    sandbox, mode = ses_account(session.client("sesv2"))
    status = identity_status(session.client("ses"), identity)
    print(
        f"from: {tenant.email_from}  identity: {tenant.email_identity}  "
        f"verification: {status}  sandbox: {sandbox}  mode: {mode}"
    )
    return 0


def _cmd_email_verify(args: argparse.Namespace) -> int:
    from renglo.operate import verify_sender

    tenant, _profile, _region, session = _operator_clients(args)
    print(verify_sender(session.client("ses"), tenant))
    return 0


def _cmd_email_allow(args: argparse.Namespace) -> int:
    from renglo.operate import allow_recipient

    _tenant, _profile, _region, session = _operator_clients(args)
    print(allow_recipient(session.client("ses"), session.client("sesv2"), args.address))
    return 0


def _cmd_email_allow_status(args: argparse.Namespace) -> int:
    from renglo.operate import list_identities, ses_account

    _tenant, _profile, _region, session = _operator_clients(args)
    sandbox, mode = ses_account(session.client("sesv2"))
    print(f"sandbox: {sandbox}  mode: {mode}")
    for name, kind, status in list_identities(session.client("ses")):
        print(f"  {name:<40} {kind:<8} {status}")
    return 0


def _admin_pool(args: argparse.Namespace, tenant, session) -> tuple[str, str]:
    from renglo.operate import console_base, platform_vars

    console = args.console or "production"
    if console not in {"local", "staging", "production"}:
        raise RengloOpsError("--console must be local, staging, or production")
    stage = "staging" if console == "staging" else "production"
    vars_block = platform_vars(session.client("ssm"), tenant.name, stage)
    pool = vars_block.get("COGNITO_USERPOOL_ID") or ""
    if not pool:
        raise RengloOpsError(f"COGNITO_USERPOOL_ID is missing from platform-vars/{stage}")
    return pool, console_base(vars_block, console)


def _cmd_admin_create(args: argparse.Namespace) -> int:
    from renglo.operate import admin_setup_url, cognito_admin_create

    tenant, _profile, _region, session = _operator_clients(args)
    pool, base = _admin_pool(args, tenant, session)
    action = cognito_admin_create(session.client("cognito-idp"), pool, args.email)
    print(action)
    print(admin_setup_url(base, args.email))
    return 0


def _cmd_admin_show(args: argparse.Namespace) -> int:
    from renglo.operate import cognito_admin_show

    tenant, _profile, _region, session = _operator_clients(args)
    pool, _base = _admin_pool(args, tenant, session)
    print(cognito_admin_show(session.client("cognito-idp"), pool, args.email))
    return 0


def _webhook_clients(args: argparse.Namespace):
    tenant, _profile, _region, session = _operator_clients(args)
    return tenant, session.client("events"), session.client("ssm")


def _cmd_webhook_status(args: argparse.Namespace) -> int:
    from renglo.webhook import format_webhook_report

    tenant, _profile, _region, session = _operator_clients(args)
    print(
        format_webhook_report(
            session.client("events"),
            tenant.name,
            lambda_client=session.client("lambda"),
            apigw=session.client("apigatewayv2"),
        )
    )
    return 0


def _cmd_webhook_stage(args: argparse.Namespace) -> int:
    from renglo.operate import platform_vars
    from renglo.webhook import stage_portfolio

    tenant, events, ssm = _webhook_clients(args)
    staging = platform_vars(ssm, tenant.name, "staging")
    production = platform_vars(ssm, tenant.name, "production")
    print(
        stage_portfolio(
            events,
            env_name=tenant.name,
            portfolio=args.portfolio,
            staging_base_url=staging.get("BASE_URL") or "",
            role_arn=production.get("ROLE_ARN") or staging.get("ROLE_ARN") or "",
        )
    )
    return 0


def _cmd_webhook_unstage(args: argparse.Namespace) -> int:
    from renglo.webhook import unstage_portfolio

    tenant, events, _ssm = _webhook_clients(args)
    print(unstage_portfolio(events, env_name=tenant.name, portfolio=args.portfolio))
    return 0


def _cmd_user_invite(args: argparse.Namespace) -> int:
    from renglo.operate import cognito_id_token, invite_user, platform_vars

    tenant, _profile, _region, session = _operator_clients(args)
    stage = "production"
    vars_block = platform_vars(session.client("ssm"), tenant.name, stage)
    api = args.api_url or vars_block.get("BASE_URL") or ""
    if not api:
        raise RengloOpsError("pass --api-url or deploy so BASE_URL is in platform-vars")
    token = args.token
    if not token:
        if not args.admin_email or not args.admin_password:
            raise RengloOpsError("pass --token, or --admin-email and --admin-password")
        client_id = vars_block.get("COGNITO_APP_CLIENT_ID") or ""
        if not client_id:
            raise RengloOpsError("COGNITO_APP_CLIENT_ID is missing from platform-vars/production")
        token = cognito_id_token(
            session.client("cognito-idp"),
            client_id,
            args.admin_email,
            args.admin_password,
        )
    print(invite_user(api, token, args.email, args.team, args.portfolio))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="renglo")
    parser.add_argument("--profile", default="")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("help").add_argument("topic", nargs="?", default="")
    sub.add_parser("doctor")
    status = sub.add_parser("status")
    status.add_argument("--live", action="store_true")
    state = sub.add_parser("state")
    state_sub = state.add_subparsers(dest="state_cmd")
    state_sub.add_parser("show")
    state_sub.add_parser("live")
    local = state_sub.add_parser("local-config")
    local.add_argument("--apply", action="store_true")
    local.add_argument("--dry-run", action="store_true")
    local.add_argument("--region", default="")

    def _init_flags(parser_: argparse.ArgumentParser) -> None:
        parser_.add_argument("--bom", default="")
        parser_.add_argument("--name", default="")
        parser_.add_argument("--github-repo", default="")
        parser_.add_argument("--email-from", default="")
        parser_.add_argument("--email-identity", default="")
        parser_.add_argument("--account", default="")
        parser_.add_argument("--region", default="")
        parser_.add_argument("--no-input", action="store_true")
        parser_.add_argument("--force", action="store_true")

    _init_flags(sub.add_parser("init"))

    config = sub.add_parser("config")
    config_sub = config.add_subparsers(dest="config_cmd")
    _init_flags(config_sub.add_parser("init"))
    config_sub.add_parser("check")

    stack = sub.add_parser("stack")
    stack_sub = stack.add_subparsers(dest="stack_cmd")
    deploy = stack_sub.add_parser("deploy")
    deploy.add_argument("--stack", default="")
    deploy.add_argument("--dry-run", action="store_true")
    stack_status = stack_sub.add_parser("status")
    stack_status.add_argument("stack", nargs="?", default="")
    stack_destroy = stack_sub.add_parser("destroy")
    stack_destroy.add_argument("--stack", required=True)
    stack_destroy.add_argument("--yes", action="store_true")
    stack_destroy.add_argument("--dry-run", action="store_true")

    account = sub.add_parser("account")
    account_sub = account.add_subparsers(dest="account_cmd")
    bootstrap = account_sub.add_parser("bootstrap")
    bootstrap.add_argument("--region", default="")
    bootstrap.add_argument("--dry-run", action="store_true")

    peer = sub.add_parser("peer")
    peer_sub = peer.add_subparsers(dest="peer_cmd")
    peer_deploy = peer_sub.add_parser("deploy")
    peer_deploy.add_argument("--peer-id", default="")
    peer_deploy.add_argument("--dry-run", action="store_true")
    peer_sub.add_parser("list")
    peer_show = peer_sub.add_parser("show")
    peer_show.add_argument("peer")
    peer_status = peer_sub.add_parser("status")
    peer_status.add_argument("--peer-id", default="")
    peer_destroy = peer_sub.add_parser("destroy")
    peer_destroy.add_argument("--peer-id", default="")
    peer_destroy.add_argument("--yes", action="store_true")
    peer_destroy.add_argument("--dry-run", action="store_true")

    extension = sub.add_parser("extension")
    extension_sub = extension.add_subparsers(dest="extension_cmd")
    extension_sub.add_parser("tree")
    extension_show = extension_sub.add_parser("show")
    extension_show.add_argument("handle")

    email = sub.add_parser("email")
    email_sub = email.add_subparsers(dest="email_cmd")
    email_sub.add_parser("sender-status")
    email_sub.add_parser("verify-sender")
    email_allow = email_sub.add_parser("allow")
    email_allow.add_argument("address")
    email_sub.add_parser("allow-status")

    webhook = sub.add_parser("webhook")
    webhook_sub = webhook.add_subparsers(dest="webhook_cmd")
    webhook_sub.add_parser("status")
    webhook_stage = webhook_sub.add_parser("stage")
    webhook_stage.add_argument("portfolio")
    webhook_unstage = webhook_sub.add_parser("unstage")
    webhook_unstage.add_argument("portfolio")

    admin = sub.add_parser("admin")
    admin_sub = admin.add_subparsers(dest="admin_cmd")
    admin_create = admin_sub.add_parser("create")
    admin_create.add_argument("email")
    admin_create.add_argument("--console", default="production")
    admin_show = admin_sub.add_parser("show")
    admin_show.add_argument("email")
    admin_show.add_argument("--console", default="production")

    user = sub.add_parser("user")
    user_sub = user.add_subparsers(dest="user_cmd")
    invite = user_sub.add_parser("invite")
    invite.add_argument("email")
    invite.add_argument("--team", required=True)
    invite.add_argument("--portfolio", required=True)
    invite.add_argument("--api-url", default="")
    invite.add_argument("--token", default="")
    invite.add_argument("--admin-email", default="")
    invite.add_argument("--admin-password", default="")

    registry = sub.add_parser("registry")
    registry_sub = registry.add_subparsers(dest="registry_cmd")
    registry_deploy = registry_sub.add_parser("deploy")
    registry_deploy.add_argument("--registry", default="")
    registry_deploy.add_argument("--dry-run", action="store_true")
    registry_show = registry_sub.add_parser(
        "show",
        help="Publisher stack outputs (same AWS profile as deploy)",
    )
    registry_show.add_argument("--registry", default="")
    registry_show.add_argument("--region", default="")
    registry_check = registry_sub.add_parser(
        "check",
        help="See whether a package version is published in this registry",
    )
    registry_check.add_argument(
        "package",
        help="Python name, or an npm name such as @scope/pkg",
    )
    registry_check.add_argument(
        "version",
        help="Version in pyproject.toml or package.json; a leading v is ignored",
    )
    registry_check.add_argument(
        "--format",
        default="",
        choices=("python", "npm"),
        help="python or npm; a leading @ is npm",
    )
    registry_check.add_argument("--registry", default="")
    registry_check.add_argument("--region", default="")

    publish = sub.add_parser("publish")
    publish.add_argument("--path", default=".")
    publish.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.cmd in {None, "help"}:
            topic = getattr(args, "topic", "") if args.cmd == "help" else ""
            print(render(topic), end="")
            return 0
        if args.cmd == "doctor":
            return _cmd_doctor()
        if args.cmd == "status":
            return _cmd_status(args)
        if args.cmd == "state" and args.state_cmd == "show":
            return _cmd_show()
        if args.cmd == "state" and args.state_cmd == "live":
            return _cmd_state_live(args)
        if args.cmd == "state" and args.state_cmd == "local-config":
            return _cmd_local_config(args)
        if args.cmd == "init":
            return _cmd_config_init(args)
        if args.cmd == "config" and args.config_cmd == "init":
            return _cmd_config_init(args)
        if args.cmd == "config" and args.config_cmd == "check":
            return _cmd_check()
        if args.cmd == "stack" and args.stack_cmd == "deploy":
            return _cmd_stack(args)
        if args.cmd == "stack" and args.stack_cmd == "status":
            return _cmd_stack_status(args)
        if args.cmd == "stack" and args.stack_cmd == "destroy":
            return _cmd_stack_destroy(args)
        if args.cmd == "account" and args.account_cmd == "bootstrap":
            return _cmd_account_bootstrap(args)
        if args.cmd == "peer" and args.peer_cmd == "deploy":
            return _cmd_peer(args)
        if args.cmd == "peer" and args.peer_cmd == "list":
            return _cmd_peer_list()
        if args.cmd == "peer" and args.peer_cmd == "show":
            return _cmd_peer_show(args)
        if args.cmd == "peer" and args.peer_cmd == "status":
            return _cmd_peer_status(args)
        if args.cmd == "peer" and args.peer_cmd == "destroy":
            return _cmd_peer_destroy(args)
        if args.cmd == "extension" and args.extension_cmd == "tree":
            return _cmd_extension_tree()
        if args.cmd == "extension" and args.extension_cmd == "show":
            return _cmd_extension_show(args)
        if args.cmd == "email" and args.email_cmd == "sender-status":
            return _cmd_email_sender(args)
        if args.cmd == "email" and args.email_cmd == "verify-sender":
            return _cmd_email_verify(args)
        if args.cmd == "email" and args.email_cmd == "allow":
            return _cmd_email_allow(args)
        if args.cmd == "email" and args.email_cmd == "allow-status":
            return _cmd_email_allow_status(args)
        if args.cmd == "webhook" and args.webhook_cmd == "status":
            return _cmd_webhook_status(args)
        if args.cmd == "webhook" and args.webhook_cmd == "stage":
            return _cmd_webhook_stage(args)
        if args.cmd == "webhook" and args.webhook_cmd == "unstage":
            return _cmd_webhook_unstage(args)
        if args.cmd == "webhook":
            raise RengloOpsError("pass status, stage PORTFOLIO, or unstage PORTFOLIO")
        if args.cmd == "admin" and args.admin_cmd == "create":
            return _cmd_admin_create(args)
        if args.cmd == "admin" and args.admin_cmd == "show":
            return _cmd_admin_show(args)
        if args.cmd == "user" and args.user_cmd == "invite":
            return _cmd_user_invite(args)
        if args.cmd == "registry" and args.registry_cmd == "deploy":
            return _cmd_registry_deploy(args)
        if args.cmd == "registry" and args.registry_cmd == "show":
            return _cmd_registry_show(args)
        if args.cmd == "registry" and args.registry_cmd == "check":
            return _cmd_registry_check(args)
        if args.cmd == "publish":
            return _cmd_publish(args)
    except RengloOpsError as exc:
        _die(exc)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
