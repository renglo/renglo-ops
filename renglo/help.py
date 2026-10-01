"""Project-filtered help. Every command stays available; the project only chooses what to show."""

from __future__ import annotations

PATHS: dict[str, list[str]] = {
    "greenfield": [
        "renglo doctor",
        "renglo init",
        "renglo status",
        "renglo config check",
        "renglo stack deploy",
        "renglo state local-config --apply",
    ],
    "registry": [
        "renglo registry deploy",
        "renglo registry show",
        "renglo publish",
        "renglo config import",
    ],
    "extensions": [
        "renglo state show",
        "renglo stack deploy",
        "renglo peer deploy --peer-id PEER",
    ],
    "pipeline": [
        "renglo config check",
        "renglo stack deploy",
        "renglo status",
    ],
    "operate": [
        "renglo status --live",
        "renglo stack status",
        "renglo stack deploy --stack a,b",
        "renglo stack destroy --stack b --yes",
        "renglo account bootstrap",
        "renglo peer list",
        "renglo peer status",
        "renglo peer show PEER",
        "renglo peer destroy --peer-id PEER --yes",
        "renglo extension tree",
        "renglo extension show HANDLE",
        "renglo state live",
        "renglo email sender-status",
        "renglo email verify-sender",
        "renglo email allow ADDRESS",
        "renglo email allow-status",
        "renglo admin create EMAIL",
        "renglo admin show EMAIL",
        "renglo user invite EMAIL --team TEAM --portfolio PORTFOLIO",
    ],
}

BLURBS = {
    "init": "Answer a few questions; writes renglo.yaml and .renglo/local.yaml.",
    "doctor": "Checklist: BOM, renglo.yaml, CDK/AWS tooling, profile, project readiness.",
    "status": "Tenant file, accounts, release pointer, and the AWS profile in use.",
    "show": "Print the loaded tenant document.",
    "local-config": "Write env_config.py and console/.env.development in the product workspace.",
    "deploy": "Synth (and, without --dry-run, cdk deploy) hub, peer, or registry stacks.",
    "import": "Write renglo.yaml and registry.yaml from the old config files. One time.",
    "check": "Validate renglo.yaml and compare github.repo with the BOM checkout.",
    "publish": "Build and upload the package in the current directory.",
}


def render(topic: str = "") -> str:
    topic = (topic or "").strip().lower()
    if topic in PATHS:
        lines = [f"renglo {topic}", ""]
        for command in PATHS[topic]:
            lines.append(f"  {command}")
        lines.append("")
        lines.append("Every other command is still available. renglo help lists the projects.")
        return "\n".join(lines) + "\n"
    if topic in BLURBS:
        return f"{topic}: {BLURBS[topic]}\n"
    lines = [
        "renglo — environment control plane",
        "",
        "New environment: renglo init  (see docs/configuration.md for every field)",
        "",
        "Operator projects (revisited, not finished in order):",
        "  renglo help greenfield   Stand up every AWS service for one environment",
        "  renglo help registry     Host a package registry, or read from one",
        "  renglo help extensions   Place extensions on the hub or on peers",
        "  renglo help pipeline     Deploy from the BOM repository",
        "  renglo help operate      Run a live environment (stacks, mail, users, peers)",
        "",
        "Commands:",
    ]
    for name, blurb in BLURBS.items():
        lines.append(f"  {name:14} {blurb}")
    return "\n".join(lines) + "\n"
