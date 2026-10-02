"""Project-filtered help. Every command stays available; the project only chooses what to show."""

from __future__ import annotations

PATHS: dict[str, list[str]] = {
    "greenfield": [
        "renglo doctor",
        "renglo init",
        "renglo status",
        "renglo config check",
        "renglo catalog sync",
        "renglo stack deploy",
        "renglo state local-config --apply",
    ],
    "registry": [
        "renglo registry deploy",
        "renglo registry show",
        "renglo registry check PACKAGE VERSION",
        "renglo publish",
    ],
    "extensions": [
        "renglo state show",
        "renglo catalog sync",
        "renglo stack deploy",
        "renglo peer deploy --peer-id PEER",
    ],
    "pipeline": [
        "renglo config check",
        "renglo catalog sync",
        "renglo stack deploy",
        "renglo status",
    ],
    "catalog": [
        "renglo catalog sync",
        "renglo catalog sync --dry-run",
    ],
    "operate": [
        "renglo catalog sync",
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
    "check": "Validate renglo.yaml and compare github.repo with the BOM checkout.",
    "catalog": "Merge product.yaml from the white-label pack into renglo.yaml packages.",
    "publish": "Build and upload the package in the current directory.",
}

# Full invocations shown on renglo help (top-level BLURBS omit subcommands).
COMMAND_LINES: list[tuple[str, str]] = [
    ("init", BLURBS["init"]),
    ("doctor", BLURBS["doctor"]),
    ("status", BLURBS["status"]),
    ("catalog sync [--dry-run]", BLURBS["catalog"]),
    ("config check", BLURBS["check"]),
    ("stack deploy", BLURBS["deploy"]),
    ("state local-config --apply", BLURBS["local-config"]),
    ("publish", BLURBS["publish"]),
]


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
        "  renglo help catalog      Sync product.yaml into renglo.yaml",
        "",
        "Commands:",
    ]
    for name, blurb in COMMAND_LINES:
        lines.append(f"  {name:26} {blurb}")
    lines.append("")
    lines.append("  renglo help <topic>   Short list for one project (extensions, operate, catalog, …)")
    return "\n".join(lines) + "\n"
