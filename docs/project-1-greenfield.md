# Project 1 — Greenfield infrastructure

Goal: every AWS service for one environment is operational, the config files for running the product outside the cloud exist, and the hub is running the current platform code. Nothing here involves a release train. The registry you host, and the white-label package that goes on it, are Project 2.

Prerequisite: [configuration.md](configuration.md).

When this project is done you have a hub in one AWS account — auth, mail, storage, identity, the API — and three generated files that let the API and console run against it from anywhere.

---

## What gets created

| Stack | Contents |
| --- | --- |
| `{env}` stack A | Core platform: Cognito, SES identity, storage, identity tables, the API |
| `{env}` stack B | Hub-side extension infrastructure. Empty of extensions until Project 3 |

Stack A also creates the GitHub OIDC provider in that account when the account does not already have one. That trust names `github.repo` from `renglo.yaml`, which is why the BOM repository is decided now even though the pipeline is Project 4. The deploy records `github.owner_id` and `github.repo_id` before it creates the role, so the trust includes the immutable subject GitHub sends for this repository.

---

## 1. Prepare the machine and the account

Install the command as the [README](../README.md#first-time-setup) describes, activate its venv, and check it:

```bash
renglo doctor
```

`doctor` must report both the CDK libraries and the CDK CLI. `setup_venv.sh` installs the CLI into the venv; if it reports the CLI missing, install Node.js and re-run `setup_venv.sh`.

Confirm the profile points at the account you intend to change, then bootstrap that account and region once. A greenfield account has no CDK asset bucket.

```bash
renglo account bootstrap
```

SES starts in the sandbox. Verify the address or domain in `email.from`, and request production access when the environment will mail people outside the verified identities.

## 2. Describe the environment

`renglo init` already wrote `renglo.yaml` during setup. Run it again whenever you need to start over, or to describe a second environment:

```bash
renglo init --bom path/to/<env>-bom
```

It asks for the environment name, the BOM repository, the system email address and identity type, the AWS account and region, and the profile on this machine, then writes `renglo.yaml` beside the manifests and `.renglo/local.yaml` for this machine. Every question is also a flag, and the field-by-field reference is in [configuration.md](configuration.md).

That file is enough to deploy the hub. `registries`, `packages`, and `placement` can stay empty until the stacks exist. Section 7 fills the platform packages and puts that code on the hub. Project 2 adds the white-label package. Project 3 adds anything beyond data and schd.

Commit `renglo.yaml` and push it. The pipeline reads it later; more immediately, it is the record of what this environment is supposed to be.

## 3. Check what loads before touching AWS

```bash
renglo status
renglo state show
renglo config check
```

`status` prints the environment name, the accounts and their ids, and the profile in use. Verify the account id in the file is the account behind that profile. `config check` compares `github.repo` with the BOM checkout's `origin`; the file is what OIDC trusts, the remote is only a cross-check.

## 4. Deploy the hub

```bash
renglo stack deploy --dry-run
renglo stack deploy
```

`--dry-run` prints the GitHub ids it would record and the `cdk deploy` command, and does not call AWS. A real deploy runs `cdk deploy --app "python -m renglo_ops.cdk.hub"`. The CDK CLI reads `cdk.context.json` from the workspace, looks up any key that is still missing, and then applies the stacks. Templates land in `<workspace>/.renglo/cdk.out`, which is gitignored. `cdk` must be on `PATH`.

`--profile NAME` overrides the profile in `.renglo/local.yaml` for one run.

Before the template is applied, the deploy looks up `github.owner_id` and `github.repo_id` when either is blank (`gh api repos/ORG/REPO`) and writes them into `renglo.yaml`. `gh` has to be logged in (`gh auth login`). The OIDC role is then created with both subjects: `repo:ORG/REPO:environment:staging` and `repo:ORG@OWNER/REPO@ID:environment:staging`. Repositories created after 15 Jul 2026 present the second one, so a role built without the ids rejects the later pipeline run. `renglo bom setup` runs after this deploy and leaves the ids in place.

## 5. Confirm the environment answers

```bash
renglo stack status
```

The outputs of stack A carry the API URL, the user pool, and the client ids that the next step reads.

## 6. Hand off the local config

The API and the console need generated values from the live environment: pool ids, URLs, and the sender. This is how someone runs the product locally without installing this command.

```bash
renglo state local-config
renglo state local-config --apply
```

Without `--apply` the files land in `<workspace>/.renglo/local-dev/` so you can read them first.

| Preview file | With `--apply`, copied to |
| --- | --- |
| `env_config.py` | `dev/renglo-api/env_config.py` |
| `run.sh` | `dev/renglo-api/run.sh` |
| `.env.development` | `console/.env.development` |

Secrets already present in `env_config.py` are kept, so re-running does not wipe hand-added values. `--apply` requires `dev/renglo-api/` and `console/` in the workspace, `--dry-run` prints the write without touching those trees, and `--region` overrides the region for one run. The command refuses to write inside the `renglo-ops` checkout or into `site-packages`.

Whoever writes application code takes these files into their own setup. They do not need this venv, an AWS profile, or the CDK.

## 7. Deploy the current platform code

Stack deploy leaves a seed image in the backend Lambda. The environment is up when that function runs the current platform packages instead: `renglo-ops` (the `platform` pin), `renglo-lib`, `renglo-api`, `renglo-data`, and `renglo-schd`.

`renglo bom setup` writes that into the BOM repository. It fills a still-empty catalog so `renglo.yaml` carries:

| Section | What it writes when the section is empty |
| --- | --- |
| `platform` | The checked-out `renglo-ops` version, from `lib/pyproject.toml`. The same value is written into each manifest |
| `release.bom`, `release.console` | `0.0.1` when this is the first BOM. These name the manifest files. They are not package versions |
| `registries` | This tenant's domain, and the `renglo` domain |
| `packages` | `renglo-lib`, `renglo-api`, `@renglo/console`, `<namespace>-wl`, `data`, `schd` |
| `placement.hub` | `data` and `schd` |

The AWS profile has to be able to read the registries in `renglo.yaml`. Sections you already filled are left as they are, including the GitHub ids stack deploy recorded. The command then writes `bom/vX.Y.Z.json` and `console_bom/vX.Y.Z.json`. Each manifest is `bom/vX.Y.Z.json` and `console_bom/vX.Y.Z.json`, and each one carries `platform` set to that checked-out `renglo-ops` version. A repository with no earlier manifest starts at `v0.0.1`. A file named only after the platform version does not count as an earlier BOM. Each package inside the file is pinned to the newest Published version that is not a release candidate (`0.0.20rc2` and `0.0.25-rc.2` are skipped). An npm scope selects the registry row, and a Python name selects the domain it starts with (`renglo-lib` is the `renglo` domain, `<namespace>-wl` is this tenant's domain). `--dry-run` prints those versions and does not write.

```bash
renglo bom setup --dry-run
renglo bom setup
```

A package with no stable Published version is left out of the pin file and named in the output. `<namespace>-wl` is published in Project 2; run `renglo bom setup` again after it is on the shelf and it will be pinned.

Push the BOM repository and run **Deploy Backend** (`deploy.yml`) from the Actions tab, or let the push start it. The workflow checks out this repo, installs `renglo-ops` at `platform`, downloads the pins, builds the image, and shifts it onto the Lambda alias. That is the code deploy. The seed image is no longer what answers the API.

```bash
git add renglo.yaml bom/ console_bom/ .github/
git commit -m "Pin the platform packages"
git push origin main
```

`.github/` has to be in that commit. `renglo doctor` fails while `.github/scripts/` exists on disk but is not tracked, and the workflow exits on the same check. A root `.gitignore` line `scripts` also hides `.github/scripts/`; the line that ignores only a top-level helper directory is `/scripts`.

Greenfield is done when that workflow succeeds. The API is running the platform version you just pinned.

---

## Coming back to this project

| Change | Do this |
| --- | --- |
| Core platform settings changed in `renglo.yaml` | `renglo stack deploy` |
| API URL or user pool changed | `renglo state local-config --apply`, then redistribute the files |
| A newer published package should be what the hub runs | `renglo bom setup`, push, and let Deploy Backend run |
| A second account becomes real | Fill `accounts.production`, set `enabled: true`, bootstrap it, deploy with that profile |

---

## Next

Packages have to come from somewhere before extensions can run: [project-2-registry.md](project-2-registry.md). If every package this environment installs is public or already hosted elsewhere, skip to [project-3-extensions.md](project-3-extensions.md).
