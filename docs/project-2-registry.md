# Project 2 — Registry

Goal: the private packages this environment runs can be installed **by version**  instead of cloned from a git repository at a pinned commit.

Prerequisite: [configuration.md](configuration.md).

Skip this project entirely when every package the environment installs comes from public PyPI and npmjs. Nothing in Project 1 or Project 3 requires a registry you host.

---

## Why a registry

Cloning a private repository is how code gets written. It is a poor way to distribute code, and it stops working as soon as any of this is true:

- The CI token of the consuming environment cannot read that repository's history.
- Several orgs each ship extensions from their own GitHub organisation.
- You want semver and rollback (e.g: `mail==1.4.0`) instead of a commit SHA nobody can read.
- The people writing an extension should not have to hard-code who runs it, or in which AWS account.

A registry solves all four by putting a versioned artifact between the repository and the environment. 

A registry can host and serve Python and NPM packages. This is useful for Extension repositories, two artifacts come out of it, and both go to the registry:


| Artifact                                                     | Where it lands              | Example name   |
| ------------------------------------------------------------ | --------------------------- | -------------- |
| Python package — handlers, APIs, blueprints inside the wheel | CodeArtifact `python-store` | `renglo-data`  |
| UI package — console screens and widgets                     | CodeArtifact `npm-store`    | `@renglo/data` |


But extensions are not the only repo that uses the registry. Also Platform libraries rely on it for distribution. 

The path is always the same: tag, package, registry. 

```text
  GitHub: renglo/data
       │  git tag v1.4.0
       ▼
  The account that owns the packages
  CodeArtifact domain "renglo"
       │  pip install renglo-data==1.4.0
       ▼
  Any account allowed to read that domain
```

---



## The registry, and who touches it

A registry is like a warehouse that contains packages for distribution: it exists, it has a fixed address, and it does not change much once built. 

Three things happen around packages and registries, and they belong to different people on different clocks. 


| #   | Process                                                                           | Persona                              | How often                      |
| --- | --------------------------------------------------------------------------------- | ------------------------------------ | ------------------------------ |
| 1   | **Build the Registry** — deploy the registry and edit its configuration           | Operator                             | Once per registry, then rarely |
| 2   | **Give a repository a slot on the registry**— let one GitHub repo publish into it | Operator **and** developer, together | Once per repository            |
| 3   | **Put packages on the registry** — publish a version                              | Developer, through CI/CD             | Constantly                     |


There is a fourth thing that is the goal of publishing a packate to a repository: Projects **reading from a registry** to get the package and use it. This is covered [at the end of this doc](#reading-from-a-registry).

### The operator is in charge of setting up the registry, not to publish code on it.

**The operator gives the developer** three GitHub Actions variables to setup the repository to publish to the registry:


| Variable               | Value                                               |
| ---------------------- | --------------------------------------------------- |
| `AWS_PUBLISH_ROLE_ARN` | ARN of the IAM role the workflow assumes            |
| `PUBLISHER_NAME`       | Short name of the registry, e.g. `apollo`           |
| `AWS_REGION`           | Region the registry stack runs in, e.g. `us-east-1` |


**The developer gives the operator** one value: the **short GitHub name of the repository** that needs to publish (e.g: `acme-data`, for `acmeorg/acme-data`. Until the operator adds that name to `publish_repos` and redeploys, the repo is not welcome on the registry and its tags will fail to assume the role.

After that single exchange the operator is out of the loop. The developer tags a release, GitHub Actions publishes, and nobody asks anyone for anything.

It is important to mention that the registry is linked to a single github org (this was setup on Process 1 when the Operator was setting up the registry) and that it can only publish repos that belong to that org. It is assumed that both the operator and the developer work for the same organization. In future version, it might be possible to have a registry from different github accounts but not at the moment. 

### Process 2 happens later than you think

A developer can build an extension for weeks without needing any of this. Local development installs from the working tree; nothing is published. The need appears when the extension **graduates** — when a staging or production environment has to install it by version rather than run it off a branch.

So treat Process 2 as part of that graduation, not as part of starting a new extension.

---



## Process 1 — the operator builds the Registry

Use the AWS account and region that will **own** the packages. There doesn't need to be a Renglo system running on that AWS Account. 

### Create the registry repository

Start from the public template **[renglo/example-registry](https://github.com/renglo/example-registry)**: on GitHub, click **Use this template**, and name the copy something like `<org>-registry` (for example `apollo-registry`). That repository holds `registry.yaml` and nothing else — no environment BOM, no application code.

The GitHub repo name and the `name` field are different things. `name` is the CodeArtifact domain and the CloudFormation prefix. For example, `name: apollo` deploys stack `apollo-publisher`. Setting `name: apollo-publisher` gives you `apollo-publisher-publisher`. Keep `name` short (`apollo`, `renglo`).

### `registry.yaml`

Fill this in before the first deploy. Later changes take effect when you redeploy.

```yaml
name: <publisher-name>
github_org: <publisher-gh-org>
publish_repos:
  - <repo-name-a>
  - <repo-name-b>
reader_accounts: []
python: python-store
npm: npm-store
```


| Field             | What to set                                                                                                                                                                   |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`            | Short name of this registry. The stack is `<name>-publisher`, and the CodeArtifact domain is a sanitized form of it                                                           |
| `github_org`      | GitHub org that owns the product repos                                                                                                                                        |
| `publish_repos`   | Repo short names allowed to assume the publish role — this is the gate from Process 2. `["*"]` trusts any repo in the org, which is convenient and worth thinking about first |
| `reader_accounts` | `[]` when only the registry account reads (typical). Add 12-digit AWS account ids when other accounts must install                                                            |
| `python` / `npm`  | Repository names. Leave them as `python-store` and `npm-store` without a reason to change                                                                                    |


`**reader_accounts: []`.** When the registry and every environment that installs from it share one AWS account, keep it empty.  When the hub or a peer runs in another account, list those account ids and redeploy:

```yaml
reader_accounts:
  - '123456789012'
  - '987654321098'
```

`**publish_repos` and immutable subjects.** The role trusts both the classic OIDC subject (`repo:org/name`) and GitHub's immutable subject (`repo:org@id/name@id`). Repos created after 15 Jul 2026 use the latter, and the stack already handles both, so you do not choose.

### Deploy it

Run from the **root of the registry repository** — the checkout that contains `registry.yaml`. The `--registry registry.yaml` below resolves against your shell's current directory, not the BOM or the product workspace. The profile must be a named profile; the command refuses `default`.

```bash
source ops/renglo-ops/.venv/bin/activate
cd ops/acme-registry   # your registry repo

export AWS_PROFILE=<registry-profile>
aws sts get-caller-identity --profile "$AWS_PROFILE"

renglo registry deploy --registry registry.yaml --dry-run
renglo registry deploy --registry registry.yaml
```

From anywhere else, pass a relative or absolute path instead of bare `registry.yaml`.

`--dry-run` prints the synth and the `cdk deploy` and does not call AWS. A real deploy writes the assembly to `<workspace>/.renglo/cdk.out` and then runs `cdk deploy --all`, so `cdk` must be on `PATH`.

`renglo registry deploy` takes its account from `AWS_PROFILE` or `--profile`. When the registry account differs from the hub account, pass `--profile` on this command. You can also store the path so later commands omit `--registry`:

```yaml
# .renglo/local.yaml
tenant: ops/acme-bom
profile: your-hub-profile
region: us-east-1
registry: /absolute/path/to/registry.yaml
```



### What the deploy created


| Resource             | Name                                                                       |
| -------------------- | -------------------------------------------------------------------------- |
| CloudFormation stack | `<name>-publisher`                                                         |
| CodeArtifact domain  | sanitized `name` (letters, digits, hyphens)                                |
| Repositories         | `python-store` (upstream public PyPI), `npm-store` (upstream public npmjs) |
| IAM role             | `GitHubActionsPublishRole-<name>`                                          |
| SSM                  | `/publisher/<name>/config` — domain and repo names the workflows read      |


Deploying the stack does not publish anything. The Registry is empty until Process 3 runs.

Several registries can share one AWS account. Each `name` gets its own stack, domain, role, and SSM path, from its own `registry.yaml`. The GitHub OIDC provider is shared, and the stack parameter `CreateGitHubOIDC` defaults to `false`. Set it to `true` only on the first deploy into an account that does not already have `token.actions.githubusercontent.com`.

### The registry repo goes quiet now

After this deploy, the `**acme-registry` repository is mostly idle**. It holds `registry.yaml` for when you change `publish_repos`, `reader_accounts`, or other registry settings and redeploy.

**Publishing a new package version does not use that repo.** Developers tag the product repository and Actions does the rest. They never need a clone of `acme-registry`, and the product clone does not need to live in the same folder tree as `renglo-ops` or the BOM.

---



## Process 2 — giving a repository a slot



### Operator: set the registry to accept the new repo

Add the repo short name to `publish_repos (in registry.yaml)`, commit, and redeploy the registry. Then print the values the developer needs:

```bash
cd ops/acme-registry
export AWS_PROFILE=<registry-profile>

renglo registry show --registry registry.yaml
```

That prints: `AWS_PUBLISH_ROLE_ARN` (stack output `OidcPublishRoleArn`), `PUBLISHER_NAME`, and `AWS_REGION`. The same three values are reused by every repo on this registry, so collect them once when you wire the first one.

Send them to the developer. That is the whole operator side.

### Developer: set the three variables

In GitHub, on **the product repo** → Settings → Secrets and variables → Actions → Variables. Variables are per repository.


| Variable               | Value                                                               |
| ---------------------- | ------------------------------------------------------------------- |
| `AWS_PUBLISH_ROLE_ARN` | From the operator                                                   |
| `PUBLISHER_NAME`       | From the operator — the registry's short name, e.g. `apollo`        |
| `AWS_REGION`           | From the operator — region of the publisher stack, e.g. `us-east-1` |




### Developer: add the workflow file to the repository

The workflow file goes at the **root** of the repository. It goes in /.github/workflows.  Its name is always publish-extension.yml . 

```text
<repo-root>/
  .github/
    workflows/
      publish-extension.yml    ← copy here
  
```

Copy the sample from **renglo-ops**:

```bash
# In any clone of the product repo — location on disk does not matter
mkdir -p .github/workflows
cp /path/to/renglo-ops/samples/publish-extension.yml .github/workflows/publish-extension.yml
```

Canonical file: `[samples/publish-extension.yml](../samples/publish-extension.yml)` in this repository (`[renglo/renglo-ops` on GitHub]([https://github.com/renglo/renglo-ops/blob/main/samples/publish-extension.yml](https://github.com/renglo/renglo-ops/blob/main/samples/publish-extension.yml))).

It runs on pushed `v*` tags and on manual dispatch, and publishes whichever trees exist in the repository:


| Tree                     | Result                         |
| ------------------------ | ------------------------------ |
| `package/pyproject.toml` | Python wheel to `python-store` |
| `ui/package.json`        | npm package to `npm-store`     |


A missing tree skips that job; one tag can publish both. Repo-root `blueprints/*.json` are copied into the Python package before the build.

### Optional: also publish to public PyPI or npmjs

Tags publish only to CodeArtifact unless you opt in. Set the repository variable `PUBLISH_PUBLIC=true` (or use the workflow dispatch checkbox) and add `PYPI_API_TOKEN` and `NPM_TOKEN` secrets on that repo. A public release is not something you take back; publish a newer version instead. Leave it off for proprietary code.

---



## Process 3 — the developer releases a version

From here on this is a developer loop with no operator in it. The version installed comes from the manifest (`pyproject.toml`, `package.json`), **not** from the tag string. Keep them aligned.

1. On the branch with the code, bump `version` in `package/pyproject.toml` and, for an extension, `ui/package.json` to the same semver. Commit.
2. Merge into `main` and push.
3. Tag that commit and push the tag.

```bash
git checkout main
git pull origin main
git merge your-branch
git push origin main
git tag v1.0.1 #This version should be aligned with pyproject.tom and package.json
git push origin v1.0.1
```

The tag starts the publish workflow. Confirm the run in the Actions tab in github, then confirm the version is really in CodeArtifact before any environment pins it:

```bash
cd ops/acme-registry
export AWS_PROFILE=<registry-profile>

renglo registry check renglo-data 1.0.1 --registry registry.yaml
```

Exit 0 and `result: published` means that version is on the shelf. `result: absent` means it is not. A name that starts with `@` is checked as npm (`renglo registry check @renglo/data 1.0.1`). Pass `--format python` or `--format npm` when the name does not make that obvious. A leading `v` is ignored, so `v1.0.1` checks `1.0.1`, the version in the manifest rather than the tag string. The command uses the same registry path and AWS profile as `renglo registry show`.

`renglo publish --path .` builds with `python -m build` and uploads with `twine` from this machine, and `--dry-run` prints those commands. The tag on the product repo is the path CI takes; the local command is for a package you are pushing by hand.

---



## Reading from a registry

Everything above is the hosting side. Reading is a separate thing, and an environment that only consumes packages does exactly this and nothing else.

Add a row to `registries` in `renglo.yaml`. A 12-digit `account` marks the CodeArtifact domain owner, which is what makes the row **foreign**:

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

`registries` and `registry.yaml` are two files rather than one section because a single registry serves many environments: `registries` is what *this* environment pulls from, `registry.yaml` is a shelf an org hosts.

On the hosting side, `reader_accounts` adds a CodeArtifact resource policy letting those accounts call `GetAuthorizationToken` and `ReadFromRepository`. Each reading account still needs its own IAM for `codeartifact:GetAuthorizationToken`, `sts:GetServiceBearerToken`, and `ReadFromRepository` — on the environment side, that is what the `registries` row produces.

### What the install looks like

With the account allowed to read and a pin in the BOM, the deploy logs in and installs:

```bash
aws codeartifact login --tool pip \
  --domain renglo \
  --domain-owner <owner-account-id> \
  --repository python-store

pip install "renglo-data==1.4.0"
```

Moving to 1.5.0 is a bump of the pin, not a re-clone. That is the whole point of the registry.

---



## Next

Decide where each of those packages runs: [project-3-extensions.md](project-3-extensions.md).