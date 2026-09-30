# Configuration — read this first

Every `renglo` command reads a **tenant file**, `renglo.yaml`. Most also need a **machine file**, `.renglo/local.yaml`. Nothing works until those exist, so creating them is the first thing you do.

| File | Committed? | Exact path |
| --- | --- | --- |
| `renglo.yaml` | Yes, in the BOM repository for the environment | Top level of the BOM checkout: `ops/acme-bom/renglo.yaml` |
| `.renglo/local.yaml` | No, gitignored | Product workspace root, else the directory holding the BOM checkout: `ops/.renglo/local.yaml` |
| `registry.yaml` | Yes, in a repository of the org that owns the registry | Project 2 only. No fixed path; you pass one. Start from [renglo/example-registry](https://github.com/renglo/example-registry). [Below](#registryyaml) |

A **BOM repository** (bill of materials) is the repository that describes one environment: `renglo.yaml` plus the pinned versions and the deploy workflows, and no application code. You create it from the public template [renglo/example-bom](https://github.com/renglo/example-bom), as described in the [README](../README.md#first-time-setup).

The same `renglo.yaml` serves all four [operator projects](../README.md#operator-projects). It starts small and grows: Project 1 needs the identity of the environment and one AWS account, Project 2 adds registries, Project 3 adds packages and placement, Project 4 adds the GitHub ids and release pins.

---

## Create both files

One command writes both, as described in the [README](../README.md#first-time-setup):

```bash
renglo init
```

| Question | Goes to | Notes |
| --- | --- | --- |
| Environment name | `name` | Short id, e.g. `acme1`. Appears in stack and resource names |
| BOM repository on GitHub | `github.repo` | `ORG/REPO`. Defaults to the checkout's `origin` when you run from one |
| System email address | `email.from` | The SES sender for this environment |
| System email identity type | `email.identity` | `email` verifies one address, `domain` verifies a domain |
| AWS account id | `accounts.staging.id` | Blank is allowed; fill it in before you deploy |
| AWS region | `accounts.*.region` and `region` in the machine file | Defaults to `us-east-1` |
| AWS CLI profile | `profile` in the machine file | The profile for the account above. Blank leaves it to `AWS_PROFILE` or `--profile` |

The BOM directory is not a question. `init` uses the checkout you are standing in, a sibling of the `renglo-ops` clone, or a folder named after the repository, and clones the repository from GitHub when that folder does not exist yet. Override it with `--bom PATH`.

`renglo.yaml` is written at the top level of that directory. `.renglo/local.yaml` goes one level up, in the directory that holds the BOM checkout, unless some directory above it is a product workspace — one holding both `console/` and `dev/` — in which case it goes at that workspace root and `tenant:` spans the difference. Either way `.renglo/` is added to the `.gitignore` of the repository owning that directory, so machine state is never committed. The placeholder `renglo.yaml` that ships in the template is replaced silently; a file that already describes an environment is kept unless you pass `--force`.

Every answer is also a flag, so a setup script can skip the prompts:

```bash
renglo init --no-input \
  --name acme1 \
  --github-repo acmeco/acme-bom \
  --email-from noreply@acme.com \
  --account 123456789012 \
  --region us-east-1
```

Then read the result back and commit it:

```bash
renglo status
renglo config check
git add renglo.yaml && git commit -m "Describe the acme1 environment"
```

`status` prints the environment name, the accounts, and the profile in use. `config check` compares `github.repo` with the checkout's `origin`; the file is what OIDC will trust, the remote is only a cross-check.

If `renglo doctor` reports that the tenant file was not found, you are neither inside a directory tree that contains `renglo.yaml` nor under one that contains `.renglo/local.yaml`.

### An environment that already has a tenant file

When the BOM directory is already in your workspace — a monorepo that keeps it under `ops/`, or a colleague's checkout — do not run `init`. Write only the machine file, at the workspace root, pointing at that directory:

```bash
mkdir -p .renglo
cat > .renglo/local.yaml <<'YAML'
tenant: ops/acme-bom
profile: your-aws-cli-profile
region: us-east-1
YAML
```

---

## How the command finds configuration

From the current directory, `renglo` walks **up** the tree and takes the first match:

1. `renglo.yaml` in that directory → that is the tenant file.
2. `.renglo/local.yaml` in that directory → read its `tenant` path, then load `<workspace>/<tenant>/renglo.yaml`.

The **workspace root** is the directory that holds `.renglo/local.yaml`, or the directory that holds both `console/` and `dev/`, or the directory that holds `renglo.yaml`. Synth output and generated previews land under `<workspace>/.renglo/`.

Standing inside the BOM checkout is therefore enough on its own. The machine file matters when you work from a product workspace whose root is somewhere above the BOM directory, and it is where the AWS profile is remembered either way.

In CI there is no machine file. Set `RENGLO_TENANT` to the absolute path of `renglo.yaml`, and `RENGLO_WORKSPACE` to the checkout. See [project-4-pipeline.md](project-4-pipeline.md).

`registry.yaml` is outside this search. No walk up will ever find it; the registry commands take its path from you.

---

## `.renglo/local.yaml`

One per machine, never committed. It says which environment this machine operates and which AWS profile to use.

```yaml
# <workspace-root>/.renglo/local.yaml
tenant: ops/acme-bom
profile: your-aws-cli-profile
region: us-east-1
```

| Field | Required | Meaning |
| --- | --- | --- |
| `tenant` | Yes | Path from the workspace root to the directory holding `renglo.yaml`. `.` when they are the same directory |
| `profile` | Recommended | AWS CLI profile for the account that runs the hub |
| `region` | Recommended | Default region when a command needs one and `AWS_REGION` is unset |
| `registry` | Project 2 only | Absolute path to `registry.yaml`, so `renglo registry deploy` can omit `--registry` |

Profile resolution order: `--profile` on the command, then `AWS_PROFILE`, then `profile` here.

A registry usually lives in a different AWS account from the hub. When those profiles differ, keep the hub profile in this file and pass `--profile` on the registry command.

---

## `renglo.yaml`

Desired state of one environment, committed in its BOM repository. CDK reads it to build stacks, OIDC trust comes from it, and the release scripts project it into the catalog shape the pipeline consumes. It is the file you edit in every project.

### The four fields the loader demands

The file is rejected without these, which is why `init` asks for all four:

| Field | Rules |
| --- | --- |
| `name` | Short environment id, e.g. `acme1` |
| `github.repo` | `ORG/REPO` of the BOM repository |
| `email.from` | SES sender address |
| `email.identity` | Exactly `email` or `domain` |

`registries[].domain` is required on every registry row you add. `defaults.cognito_token_hours` must be between 1 and 24.

`github.repo` is needed from the start even though the pipeline itself is Project 4: stack A creates the OIDC trust for that repository. Name the BOM repo you intend to use, not the remote your machine happens to have.

### Which project fills which section

| Section | Project | Purpose |
| --- | --- | --- |
| `name`, `github.repo`, `email` | 1 | Identity of the environment, sender address and SES identity type |
| `accounts.staging`, `accounts.production` | 1 | `id`, `region`, `enabled` for the accounts that run the hub |
| `platform` | 1 | Version line of the platform package the deploy context uses |
| `defaults`, `env` | 1 | `architecture`, `cognito_token_hours`, extra deploy context strings |
| `registries` | 2 | Every CodeArtifact domain this environment installs from, including foreign ones |
| `packages` | 3 | Handle to package names: Python in CodeArtifact, npm scope package |
| `placement.hub`, `placement.peers` | 3 | Whether each handle runs on the hub or on a named peer |
| `github.owner_id`, `github.repo_id` | 4 | Immutable GitHub subjects for OIDC trust |
| `release.bom`, `release.console` | 4 | Pins the pipeline deploys |

### What `init` writes

This is the whole file for a new environment, and what you would type by hand without the command. Sections left empty are filled in by later projects.

```yaml
name: acme1
github:
  repo: acmeco/acme-bom
email:
  from: noreply@acme.com
  identity: email
platform: 0.1.0
accounts:
  staging:
    id: '123456789012'
    region: us-east-1
    enabled: true
  production:
    id: ''
    region: us-east-1
    enabled: false
registries: []
placement:
  hub: []
  peers: {}
packages: {}
release:
  bom: 0.1.0
  console: 0.1.0
env: {}
defaults:
  architecture: x86_64
  cognito_token_hours: 24
```

`platform` is set to the `renglo-ops` version that wrote the file, which is what CI later installs. `production` is a placeholder until a second account is real.

### Section notes

**`email`.** `identity: email` verifies one address. `identity: domain` verifies a domain; add `hosted_zone_id` when Route53 holds the records.

**`accounts`.** Keys are `staging` and `production`. A row with `enabled: false` and an empty `id` is a placeholder; the deploy skips it. Both can point at the same account id when one account hosts both.

**`registries`.** A row without `account` is a CodeArtifact domain in this environment's own account. A row with a 12-digit `account` is a domain owned elsewhere, and that owner must list this account as a reader. `scopes` are the npm scopes served by that domain. Details in [project-2-registry.md](project-2-registry.md).

**`packages`.** Maps a handle to what gets installed: `python:` for the wheel name, `npm:` for the scoped package. A handle can have one or both.

**`placement`.** `hub` is a list of package names whose infrastructure belongs to stack B. `peers` is a map of peer id to that peer's definition. `peers: {}` means this environment has no peers. See [project-3-extensions.md](project-3-extensions.md).

**`release`.** `bom` and `console` are the versions the pipeline deploys, kept in step with the manifests in the BOM repo. git-convoy moves them; `renglo` only reads them.

---

## `registry.yaml`

Desired state of one CodeArtifact stack: the domain, the two repositories, the GitHub org allowed to publish into it, and the AWS accounts allowed to install from it. Only the `renglo registry` commands read it, so environments that install nothing private never have one.

It is a separate file from `renglo.yaml`, and deliberately not a section inside it, because one registry serves many environments. The `registries` list in `renglo.yaml` is the reading side — which domains this environment pulls from; `registry.yaml` is the hosting side.

### Where to keep it

Commit it in a repository belonging to the org that owns the registry's AWS account, one file per registry, and not inside an environment's BOM repo. The public template [renglo/example-registry](https://github.com/renglo/example-registry) is that repository: copy it with GitHub's **Use this template**, then edit `registry.yaml`. If the checkout sits in a git-convoy workspace, keep `role = "registry"` in `gitconvoy.toml` so it is not treated as product, ops, or a BOM. Beyond that the location is yours: `renglo` has no default path for this file and never walks the tree looking for it.

Two commands need it, and the path comes from the first of these that is set:

| Source | Notes |
| --- | --- |
| `--registry PATH` | Optional on `renglo registry deploy`, required on `renglo registry connect` |
| `RENGLO_REGISTRY` | Absolute path in the environment. The CDK app reads this variable, so it is also what `--registry` ends up setting |
| `registry:` in `.renglo/local.yaml` | `deploy` only. Make it absolute: a relative value resolves against the current directory, not the workspace root |

`renglo registry deploy` exits with `pass --registry PATH or set registry in .renglo/local.yaml` when all three are empty.

The file is also written to, not only read: `renglo registry connect REPO --registry PATH` appends the repo's short name to `publish_repos` in place. Commit that change, then redeploy the registry so the publish role trusts the new repo.

### Template

```yaml
name: acme
github_org: acmeco
publish_repos:
  - data
  - schd
reader_accounts:
  - '123456789012'
python: python-store
npm: npm-store
```

`name` and `github_org` are required; the loader rejects the file without them. `publish_repos` and `reader_accounts` default to empty lists, `python` to `python-store` and `npm` to `npm-store`.

| Field | Meaning |
| --- | --- |
| `name` | Short id. The stack is `<name>-publisher`; the CodeArtifact domain is a sanitized form of it |
| `github_org` | GitHub org whose repos may publish |
| `publish_repos` | Repo short names allowed to assume the publish role. `["*"]` trusts any repo in the org |
| `reader_accounts` | AWS account ids allowed to install. Empty means same-account readers only |
| `python` / `npm` | Repository names. Leave as `python-store` and `npm-store` unless you have a reason to change them |

What each value creates in AWS is in [project-2-registry.md](project-2-registry.md#registryyaml).

---

## Verify what loaded

```bash
renglo doctor          # Checklist: BOM, tenant file, CDK/AWS, profile, projects 1–4
renglo status          # Name, GitHub repo, accounts, pins, profile in use
renglo state show      # The whole tenant document as JSON
renglo config check    # github.repo against the BOM checkout's origin
```

`renglo state show` is the fastest way to confirm a hand edit parsed the way you meant, because it prints the document after defaults are applied.

---

## Next

| Goal | Doc |
| --- | --- |
| Operate a running environment | [operator.md](operator.md) |
| Stand up a new environment in AWS | [project-1-greenfield.md](project-1-greenfield.md) |
| Host a registry, or read from Renglo's | [project-2-registry.md](project-2-registry.md) |
| Place extensions on the hub or on peers | [project-3-extensions.md](project-3-extensions.md) |
| Deploy from the BOM repository | [project-4-pipeline.md](project-4-pipeline.md) |
