# Project 2 — Registry

Goal: the private packages this environment runs can be installed **by version**, the way `requests` or `lodash` are, instead of cloned from a git repository at a pinned commit.

Prerequisite: [configuration.md](configuration.md).

Two separate things happen in this project, and you may need only one of them:

| Side | File you edit | Command |
| --- | --- | --- |
| **Host** a registry for code your org owns | `registry.yaml` | `renglo registry deploy` |
| **Read** from a registry somebody else owns, including Renglo's | `registries` in `renglo.yaml` | `renglo stack deploy` picks it up |

Skip this project entirely when every package the environment installs comes from public PyPI and npmjs. Nothing in Project 1 or Project 3 requires a registry you host.

---

## Why a registry instead of git

Cloning a private repository breaks down as soon as:

- The consuming CI token cannot read that repository's history
- Several orgs each ship extensions from their own GitHub organisation
- You want semver and rollback (`mail==1.4.0`) instead of a commit SHA
- The people writing an extension should not hard-code who runs it, or in which account

Cloning git is how code is written. The registry is how it is distributed.

| Artifact | Repository | Example |
| --- | --- | --- |
| Python package: handlers, APIs, blueprints inside the wheel | CodeArtifact `python-store` | `renglo-data` |
| UI package: console screens and widgets | CodeArtifact `npm-store` | `@renglo/data` |

Platform libraries use the same pipeline: tag, package, registry. A running application is never published; an environment installs packages into itself.

---

## Read from a registry you do not own

Add a row to `registries` in `renglo.yaml`. A 12-digit `account` marks the CodeArtifact domain owner, which is what makes the row foreign.

```yaml
registries:
  - domain: renglo
    python: python-store
    npm: npm-store
    account: '339713094352'
    scopes:
      - '@renglo'
```

This is how an environment installs Renglo's own packages. Two things must both be true:

1. The row exists here, so hub and peer deploys log in to that domain.
2. The owner of that domain lists this environment's account in their `reader_accounts`, and redeploys their registry. Ask them for it.

A row **without** `account` is a domain in this environment's own account; same-account read is already granted.

`registries` is what this environment pulls from. `registry.yaml` is a registry an org hosts. They are different files because one registry serves many environments.

```text
  GitHub: renglo/data
       │  git tag v1.4.0
       ▼
  Account that owns the packages
  CodeArtifact domain "renglo"
       │  pip install renglo-data==1.4.0
       ▼
  Any account listed in reader_accounts
```

---

## Host a registry

Use the AWS account and region that will **own** the packages. That is frequently not the account running the hub.

### What the stack creates

| Resource | Name |
| --- | --- |
| CloudFormation stack | `<name>-publisher` |
| CodeArtifact domain | sanitized `name` (letters, digits, hyphens) |
| Repositories | `python-store` (upstream public PyPI), `npm-store` (upstream public npmjs) |
| IAM role | `GitHubActionsPublishRole-<name>` |
| SSM | `/publisher/<name>/config` — domain and repo names the workflows read |

`reader_accounts` adds a CodeArtifact resource policy so those accounts may call `GetAuthorizationToken` and `ReadFromRepository`. Each reading account still needs its own IAM for `codeartifact:GetAuthorizationToken`, `sts:GetServiceBearerToken`, and `ReadFromRepository`; on the environment side that is the `registries` row above.

Several registries can share one AWS account. Each `name` gets its own stack, domain, role, and SSM path, from its own `registry.yaml`. The GitHub OIDC provider is shared, and the stack parameter `CreateGitHubOIDC` defaults to `false`. Set it to `true` only on the first deploy into an account that does not already have `token.actions.githubusercontent.com`.

### `registry.yaml`

Fill this in before the first deploy. Later changes take effect when you redeploy.

```yaml
name: renglo
github_org: renglo
publish_repos:
  - renglo-lib
  - data
reader_accounts:
  - "858045071584"
python: python-store
npm: npm-store
```

| Field | What to set |
| --- | --- |
| `name` | Short name of this registry. The stack is `<name>-publisher`, and the CodeArtifact domain is a sanitized form of it |
| `github_org` | GitHub org that owns the product repos |
| `publish_repos` | Repo short names allowed to assume the publish role. `["*"]` trusts any repo in the org. The role trusts both the classic subject (`repo:org/name`) and GitHub's immutable subject (`repo:org@id/name@id`); repos created after 15 Jul 2026 use the latter |
| `reader_accounts` | AWS account ids allowed to `pip` / `npm` install. Empty means same-account readers only |
| `python` / `npm` | Repository names. Leave them as `python-store` and `npm-store` without a reason to change |

Commit it in a repository owned by the org that owns this AWS account, not inside an environment's BOM repo — one registry serves many environments. There is no default path: every command that touches the file is told where it is, as described in [configuration.md](configuration.md#registryyaml).

### Deploy it

The profile must be a named profile. The command refuses `default`.

```bash
source ops/renglo-ops/.venv/bin/activate

export AWS_PROFILE=<registry-profile>
aws sts get-caller-identity --profile "$AWS_PROFILE"

renglo registry deploy --registry registry.yaml --dry-run
renglo registry deploy --registry registry.yaml
```

`--dry-run` prints the synth and the `cdk deploy` and does not call AWS. A real deploy writes the assembly to `<workspace>/.renglo/cdk.out` and then runs `cdk deploy --all`, so `cdk` must be on `PATH`. Deploying the stack does not publish anything; packages arrive through the GitHub workflow below.

You can store the path so later commands omit `--registry`:

```yaml
# .renglo/local.yaml
tenant: ops/acme-bom
profile: your-hub-profile
region: us-east-1
registry: /absolute/path/to/registry.yaml
```

`renglo registry deploy` still takes its account from `AWS_PROFILE` or `--profile`. When the registry account differs from the hub account, pass `--profile` on this command.

---

## Connect a product repository

Publishing is enabled per repository, and two gates must both allow it:

1. **AWS.** The repo's short name is in `publish_repos`. Add it before deploy; if you add one later, redeploy.
2. **GitHub.** That repo has a workflow file and three Actions variables. Variables are repository-level, so other repos in the org inherit nothing.

Product repos never hard-code the CodeArtifact domain or an environment name. The workflow reads `/publisher/<name>/config` from SSM.

### 1. Region

Workflows call AWS in the same region as `<name>-publisher`.

```bash
aws configure get region --profile "$AWS_PROFILE"
```

### 2. Stack outputs

Run once. The same values go into every repo you connect.

```bash
export STACK=<name>-publisher

aws cloudformation describe-stacks \
  --stack-name "$STACK" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`OidcPublishRoleArn` || OutputKey==`PublisherName`].[OutputKey,OutputValue]' \
  --output table
```

### 3. Repository variables

In GitHub: that repo → Settings → Secrets and variables → Actions → Variables.

| Variable | Value |
| --- | --- |
| `AWS_PUBLISH_ROLE_ARN` | `OidcPublishRoleArn` from the stack |
| `PUBLISHER_NAME` | `PublisherName`, the `name` in `registry.yaml` |
| `AWS_REGION` | The region from step 1 |

### 4. Workflow file

`renglo registry connect` adds the repo name to `publish_repos` and writes a caller that uses the reusable workflow in this repo. It does not copy the workflow body into the product repo.

```bash
renglo registry connect /path/to/data --registry registry.yaml
```

That writes `.github/workflows/publish-extension.yml` in the product repo:

```yaml
name: Publish
on:
  push:
    tags: ["v*"]
  workflow_dispatch:
jobs:
  publish:
    uses: renglo/renglo-ops/.github/workflows/publish-extension.yml@v0.1.0
    secrets: inherit
```

The `@v0.1.0` ref is the `renglo-ops` release that contains the workflow. `--version` pins a different tag, `--name` overrides the short name taken from the directory. If the name was not already in `publish_repos`, redeploy the registry so the role trusts it, then commit the workflow on the product repo's default branch.

The reusable workflow publishes whichever trees exist:

| Tree | Result |
| --- | --- |
| `package/pyproject.toml` | Python wheel to `python-store` |
| `ui/package.json` | npm package to `npm-store` |

A missing tree skips that job, and one tag publishes both when both exist. Repo-root `blueprints/*.json` are copied into the Python package before the build, so the wheel carries the blueprints of that tag. Python-only and npm-only repos that are not extensions can call `publish-python.yml` or `publish-npm.yml` the same way (`workflow_call`, `secrets: inherit`).

### Also publishing to public PyPI or npmjs

Tags publish only to CodeArtifact unless a repo asks for a public index. On the caller, pass `publish_public: true` and set the `PYPI_API_TOKEN` and `NPM_TOKEN` secrets on that repo. A public release is not something you take back; publish a newer version instead. Leave it off for proprietary code.

---

## Release a version

The version installed comes from the manifest (`pyproject.toml`, `package.json`), not from the tag string. Keep them aligned.

1. On the branch with the code, bump `version` in `package/pyproject.toml` and, for an extension, `ui/package.json` to the same semver. Commit.
2. Merge into `main` and push.
3. Tag that commit and push the tag.

```bash
git checkout main
git pull origin main
git merge your-branch
git push origin main
git tag v1.0.1
git push origin v1.0.1
```

The tag starts the publish workflow. Confirm the run, then confirm the version is in CodeArtifact before any environment pins it:

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text --profile "$AWS_PROFILE")

aws codeartifact list-package-versions \
  --domain renglo \
  --domain-owner "$ACCOUNT" \
  --repository python-store \
  --format pypi \
  --package renglo-data \
  --query 'versions[?version==`1.0.1`].version' \
  --output text \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

Empty output means that version is not in the store.

`renglo publish --path .` builds with `python -m build` and uploads with `twine` from this machine, and `--dry-run` prints those commands. The tag on the product repo is the path CI takes; the local command is for a package you are pushing by hand.

---

## How an environment installs it

The environment needs its account in `reader_accounts` and a pin in its BOM. The deploy logs in and installs:

```bash
aws codeartifact login --tool pip \
  --domain renglo \
  --domain-owner <owner-account-id> \
  --repository python-store

pip install "renglo-data==1.4.0"
```

Moving to 1.5.0 is a bump of the pin, not a re-clone.

---

## Next

Decide where each of those packages runs: [project-3-extensions.md](project-3-extensions.md).
