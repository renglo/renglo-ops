"""Job-filtered help. Every command stays available; a section only chooses what to show."""

from __future__ import annotations

from dataclasses import dataclass, field

_ONLY = "Only the commands in this section."


@dataclass(frozen=True)
class Section:
    topic: str
    title: str
    commands: list[tuple[str, str]] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)

    def walk(self) -> list[Section]:
        found = [self]
        for child in self.sections:
            found.extend(child.walk())
        return found


# Sequences of work. A command can appear in more than one sequence.
SECTIONS: list[Section] = [
    Section(
        "start",
        "Get this checkout ready",
        [
            (
                "renglo doctor",
                "Check the BOM, renglo.yaml, CDK, the AWS profile, and which jobs are ready.",
            ),
            (
                "renglo init",
                "Answer a few questions and write renglo.yaml and .renglo/local.yaml.",
            ),
            (
                "renglo config init",
                "Write renglo.yaml and .renglo/local.yaml, the same way as renglo init.",
            ),
            (
                "renglo config check",
                "Validate renglo.yaml and compare github.repo with this checkout.",
            ),
            (
                "renglo state show",
                "Print the tenant document this checkout resolved.",
            ),
            (
                "renglo status",
                "Show the tenant file, accounts, release pointer, and AWS profile.",
            ),
        ],
    ),
    Section(
        "greenfield",
        "Stand up AWS for one environment",
        [
            (
                "renglo doctor",
                "Check the BOM, renglo.yaml, CDK, the AWS profile, and which jobs are ready.",
            ),
            (
                "renglo init",
                "Answer a few questions and write renglo.yaml and .renglo/local.yaml.",
            ),
            (
                "renglo config check",
                "Validate renglo.yaml and compare github.repo with this checkout.",
            ),
            (
                "renglo status",
                "Show the tenant file, accounts, release pointer, and AWS profile.",
            ),
            (
                "renglo account bootstrap --dry-run",
                "Print the CDK bootstrap command for this account.",
            ),
            (
                "renglo account bootstrap",
                "Run CDK bootstrap in the account named by renglo.yaml.",
            ),
            (
                "renglo stack deploy --dry-run",
                "Synth the hub stacks and stop before deploy.",
            ),
            (
                "renglo stack deploy",
                "Synth and deploy the hub stacks.",
            ),
            (
                "renglo state local-config",
                "Preview local API and console files under .renglo/local-dev.",
            ),
            (
                "renglo state local-config --dry-run",
                "Show those local files without writing them.",
            ),
            (
                "renglo state local-config --apply",
                "Write env_config.py, run.sh, and the console env into the product workspace.",
            ),
        ],
    ),
    Section(
        "registry",
        "Host a registry and publish a package",
        [
            (
                "renglo registry deploy --dry-run",
                "Synth the publisher stack and stop before deploy.",
            ),
            (
                "renglo registry deploy",
                "Deploy the CodeArtifact publisher stack.",
            ),
            (
                "renglo registry show",
                "Print publisher stack outputs for the same AWS profile as deploy.",
            ),
            (
                "renglo registry check PACKAGE VERSION",
                "See whether that package version is already in this registry.",
            ),
            (
                "renglo publish --dry-run",
                "Print the build and twine upload for the package in this directory.",
            ),
            (
                "renglo publish",
                "Build the package in this directory and upload it.",
            ),
        ],
    ),
    Section(
        "extensions",
        "Place extensions and deploy their peers",
        [
            (
                "renglo state show",
                "Print the tenant document, including placement.",
            ),
            (
                "renglo extension tree",
                "List each extension handle, where it is placed, and its package.",
            ),
            (
                "renglo extension show HANDLE",
                "Show one handle: hub or peer, Python package, and npm name.",
            ),
            (
                "renglo peer list",
                "List peers from placement, with compute type and extension handles.",
            ),
            (
                "renglo peer show PEER",
                "Show one peer's compute type, stack name, and extension handles.",
            ),
            (
                "renglo peer deploy --peer-id PEER --dry-run",
                "Synth that peer stack and stop before deploy.",
            ),
            (
                "renglo peer deploy --peer-id PEER",
                "Synth and deploy that peer stack.",
            ),
            (
                "renglo stack deploy",
                "Deploy the hub after placement changes that the hub must pick up.",
            ),
        ],
    ),
    Section(
        "pipeline",
        "Deploy what the BOM repository describes",
        [
            (
                "renglo config check",
                "Validate renglo.yaml and compare github.repo with this checkout.",
            ),
            (
                "renglo stack deploy",
                "Synth and deploy the hub stacks.",
            ),
            (
                "renglo peer deploy --peer-id PEER",
                "Synth and deploy that peer stack.",
            ),
            (
                "renglo status",
                "Show the tenant file, accounts, release pointer, and AWS profile.",
            ),
        ],
    ),
    Section(
        "operate",
        "Run a live environment",
        sections=[
            Section(
                "look",
                "See what is running",
                [
                    (
                        "renglo status",
                        "Show the tenant file, accounts, release pointer, and AWS profile.",
                    ),
                    (
                        "renglo status --live",
                        "Add CloudFormation stack states and the URLs stored in SSM.",
                    ),
                    (
                        "renglo state show",
                        "Print the tenant document this checkout resolved.",
                    ),
                    (
                        "renglo state live",
                        "Print the live platform-vars document from SSM.",
                    ),
                    (
                        "renglo stack status",
                        "Show CloudFormation state for hub stacks A and B.",
                    ),
                    (
                        "renglo stack status STACK",
                        "Show CloudFormation state for hub stack a or b.",
                    ),
                    (
                        "renglo extension tree",
                        "List each extension handle, where it is placed, and its package.",
                    ),
                    (
                        "renglo extension show HANDLE",
                        "Show one handle: hub or peer, Python package, and npm name.",
                    ),
                ],
            ),
            Section(
                "hub",
                "Change the hub",
                [
                    (
                        "renglo account bootstrap",
                        "Run CDK bootstrap in the account named by renglo.yaml.",
                    ),
                    (
                        "renglo stack deploy --dry-run",
                        "Synth the hub stacks and stop before deploy.",
                    ),
                    (
                        "renglo stack deploy",
                        "Synth and deploy hub stacks A and B.",
                    ),
                    (
                        "renglo stack deploy --stack STACK",
                        "Deploy hub stack a, b, or a,b.",
                    ),
                    (
                        "renglo stack destroy --stack STACK --dry-run",
                        "Print the hub stacks that would be deleted.",
                    ),
                    (
                        "renglo stack destroy --stack STACK --yes",
                        "Delete hub stack a, b, or both.",
                    ),
                ],
            ),
            Section(
                "peers",
                "Check and change peers",
                [
                    (
                        "renglo peer list",
                        "List peers from placement, with compute type and extension handles.",
                    ),
                    (
                        "renglo peer show PEER",
                        "Show one peer's compute type, stack name, and extension handles.",
                    ),
                    (
                        "renglo peer status",
                        "Show CloudFormation state for every peer stack.",
                    ),
                    (
                        "renglo peer status --peer-id PEER",
                        "Show CloudFormation state for one peer stack.",
                    ),
                    (
                        "renglo peer deploy --peer-id PEER --dry-run",
                        "Synth that peer stack and stop before deploy.",
                    ),
                    (
                        "renglo peer deploy --peer-id PEER",
                        "Synth and deploy that peer stack.",
                    ),
                    (
                        "renglo peer destroy --peer-id PEER --dry-run",
                        "Print the peer stack that would be deleted.",
                    ),
                    (
                        "renglo peer destroy --peer-id PEER --yes",
                        "Delete that peer stack.",
                    ),
                ],
            ),
            Section(
                "mail",
                "Turn on mail",
                [
                    (
                        "renglo email sender-status",
                        "Show sender verification and whether SES is still in the sandbox.",
                    ),
                    (
                        "renglo email verify-sender",
                        "Start SES verification for the From address or its domain.",
                    ),
                    (
                        "renglo email allow ADDRESS",
                        "Verify a recipient so sandbox SES can deliver to that address.",
                    ),
                    (
                        "renglo email allow-status",
                        "List verified identities and whether the account is still sandboxed.",
                    ),
                ],
            ),
            Section(
                "webhook",
                "Send a portfolio's webhooks to staging",
                [
                    (
                        "renglo webhook status",
                        "Show the webhook edge, the callback URL, and whether any portfolio is on staging.",
                    ),
                    (
                        "renglo webhook stage PORTFOLIO",
                        "Deliver that portfolio's webhooks to the staging API.",
                    ),
                    (
                        "renglo webhook unstage PORTFOLIO",
                        "Deliver that portfolio's webhooks to production again.",
                    ),
                ],
            ),
            Section(
                "people",
                "Add an admin and invite a user",
                [
                    (
                        "renglo admin create EMAIL",
                        "Create that Cognito admin and print the console setup link.",
                    ),
                    (
                        "renglo admin show EMAIL",
                        "Show that Cognito admin's status.",
                    ),
                    (
                        "renglo user invite EMAIL --team TEAM --portfolio PORTFOLIO",
                        "Invite that person onto a team in a portfolio.",
                    ),
                ],
            ),
        ],
    ),
]


def _index() -> dict[str, Section]:
    found: dict[str, Section] = {}
    for section in SECTIONS:
        for item in section.walk():
            found[item.topic] = item
    return found


_BY_TOPIC = _index()

PATHS: dict[str, list[str]] = {
    topic: [command for section in item.walk() for command, _blurb in section.commands]
    for topic, item in _BY_TOPIC.items()
}


def _width(sections: list[Section], *, include_root_help: bool) -> int:
    names = ["renglo help"] if include_root_help else []
    for section in sections:
        for item in section.walk():
            names.append(f"renglo help {item.topic}")
            names.extend(command for command, _blurb in item.commands)
    return max(len(name) for name in names)


def _row(command: str, blurb: str, width: int) -> str:
    return f"  {command:<{width}}  {blurb}"


def _render_section(section: Section, width: int, depth: int = 0) -> list[str]:
    lines = [
        f"{'  ' * depth}{section.title}",
        _row(f"renglo help {section.topic}", _ONLY, width),
    ]
    for command, blurb in section.commands:
        lines.append(_row(command, blurb, width))
    for child in section.sections:
        lines.append("")
        lines.extend(_render_section(child, width, depth + 1))
    return lines


def _shortcuts() -> list[str]:
    width = _width(SECTIONS, include_root_help=False)
    lines = [
        _row("renglo help", "Every command, grouped by the job you are doing.", width),
    ]
    for section in SECTIONS:
        for item in section.walk():
            lines.append(_row(f"renglo help {item.topic}", item.title, width))
    return lines


def render(topic: str = "") -> str:
    topic = (topic or "").strip().lower()
    if not topic:
        width = _width(SECTIONS, include_root_help=True)
        lines = [
            "renglo — environment control plane",
            "",
            _row("renglo help", "Every command, grouped by the job you are doing.", width),
            "",
        ]
        for index, section in enumerate(SECTIONS):
            if index:
                lines.append("")
            lines.extend(_render_section(section, width))
        return "\n".join(lines) + "\n"
    section = _BY_TOPIC.get(topic)
    if section is None:
        lines = [f"There is no section named {topic}.", ""]
        lines.extend(_shortcuts())
        return "\n".join(lines) + "\n"
    width = _width([section], include_root_help=False)
    return "\n".join(_render_section(section, width)) + "\n"
