# Project 1 — Greenfield infrastructure

Goal: every AWS service for one environment is operational, and the config files for running the product outside the cloud exist. Nothing here involves CI/CD, registries you host, or extensions.

Prerequisite: [configuration.md](configuration.md).

When this project is done you have a hub in one AWS account — auth, mail, storage, identity, the API — and three generated files that let the API and console run against it from anywhere.

---

## What gets created

| Stack | Contents |
| --- | --- |
| `{env}` stack A | Core platform: Cognito, SES identity, storage, identity tables, the API |
| `{env}` stack B | Hub-side extension infrastructure. Empty of extensions until Project 3 |

Stack A also creates the GitHub OIDC provider in that account when the account does not already have one. That trust names `github.repo` from `renglo.yaml`, which is why the BOM repository is decided now even though the pipeline is Project 4.

---

## 1. Prepare the machine and the account

Install the command as the [README](../README.md#first-time-setup) describes, activate its venv, and check it:

```bash
renglo doctor
```

`doctor` must report both the CDK libraries and the CDK CLI. `setup_venv.sh` installs the CLI into the venv; if it reports the CLI missing, install Node.js and re-run `setup_venv.sh`.

Confirm the profile points at the account you intend to change, then bootstrap that account and region once. A greenfield account has no CDK asset bucket.

```bash
export AWS_PROFILE=<your-profile>
aws sts get-caller-identity --profile "$AWS_PROFILE"
cdk bootstrap aws://<account-id>/<region> --profile "$AWS_PROFILE"
```

SES starts in the sandbox. Verify the address or domain in `email.from`, and request production access when the environment will mail people outside the verified identities.

## 2. Describe the environment

`renglo init` already wrote `renglo.yaml` during setup. Run it again whenever you need to start over, or to describe a second environment:

```bash
renglo init --bom path/to/<env>-bom
```

It asks for the environment name, the BOM repository, the system email address and identity type, the AWS account and region, and the profile on this machine, then writes `renglo.yaml` beside the manifests and `.renglo/local.yaml` for this machine. Every question is also a flag, and the field-by-field reference is in [configuration.md](configuration.md).

For this project that file is complete as written: `registries`, `packages`, and `placement` stay empty. A hub with no extensions deploys, and Projects 2 and 3 fill those in later.

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

`--dry-run` prints the two commands and calls nothing:

1. `python -m renglo_ops.cdk.hub` synths stack A and stack B from `renglo.yaml`.
2. `cdk deploy --app <workspace>/.renglo/cdk.out --all` applies them.

Templates land in `<workspace>/.renglo/cdk.out`, which is gitignored. Without `cdk` on `PATH` the synth still finishes and the command stops before AWS, which is a useful way to inspect templates.

`--profile NAME` overrides the profile in `.renglo/local.yaml` for one run.

## 5. Confirm the environment answers

```bash
aws cloudformation describe-stacks \
  --stack-name <env-stack-name> \
  --profile "$AWS_PROFILE" \
  --query 'Stacks[0].{Status:StackStatus}' \
  --output table
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

---

## Coming back to this project

| Change | Do this |
| --- | --- |
| Core platform settings changed in `renglo.yaml` | `renglo stack deploy` |
| API URL or user pool changed | `renglo state local-config --apply`, then redistribute the files |
| A second account becomes real | Fill `accounts.production`, set `enabled: true`, bootstrap it, deploy with that profile |

---

## Next

Packages have to come from somewhere before extensions can run: [project-2-registry.md](project-2-registry.md). If every package this environment installs is public or already hosted elsewhere, skip to [project-3-extensions.md](project-3-extensions.md).
