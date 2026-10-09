# Project 4 — CI/CD

Goal: the BOM repository deploys the environment. A push to `main` — usually made by a git-convoy release train — builds the images, installs the pinned packages, and updates the stacks, with no laptop in the path.

Prerequisite: [configuration.md](configuration.md), a deployed hub, and the placement decisions from [project-3-extensions.md](project-3-extensions.md).

The BOM repository controls both the infrastructure and the code that runs in it: `renglo.yaml` is the infrastructure, the BOM manifests are the versions installed into it. Skip this project for a proof of concept that is only ever deployed by hand.

---

## What the BOM repository holds

| Path | Role |
| --- | --- |
| `renglo.yaml` | Desired state of the environment. Same file every project edits |
| `bom/vX.Y.Z.json` | Backend package and git pins |
| `console_bom/vX.Y.Z.json` | Console pins |
| `peers_bom/<peer-id>/vX.Y.Z.json` | Pins for one peer, when the environment has peers |
| `gitconvoy.toml` | Which repos belong to this system's release trains |
| `.github/actions/setup-renglo-ops/` | Composite action that installs the library pinned by `platform` |
| `.github/workflows/` | `deploy.yml`, `deploy_console.yml` (add `deploy_peers.yml` only when `placement.peers` is non-empty) |

The public template [renglo/example-bom](https://github.com/renglo/example-bom) ships that layout, including a placeholder `renglo.yaml` at the repo root.

---

## 1. Finish the OIDC trust

CI authenticates to AWS with GitHub OIDC, and the trust comes from `renglo.yaml`, not from a remote URL. Fill both ids:

```yaml
github:
  repo: acmeco/acme-bom
  owner_id: '327216445'
  repo_id: '1392981742'
```

`owner_id` and `repo_id` are GitHub's immutable numeric ids. Repositories created after 15 Jul 2026 present the immutable subject (`repo:org@id/name@id`) to AWS, so a role built without them will reject the run.

```bash
renglo config check
renglo stack deploy
```

`config check` compares `github.repo` with the checkout's `origin`. The deploy is what teaches the roles to trust the repository.

## 2. Set the pins the pipeline deploys

```yaml
release:
  bom: 0.1.0
  console: 0.1.0
```

Those versions must have matching manifests in the repo: `bom/v0.1.0.json`, `console_bom/v0.1.0.json`, and `peers_bom/<id>/v0.1.0.json` for each peer. git-convoy moves these pins when a train rolls; `renglo` only reads them.

`platform` in the same file pins the `renglo-ops` version CI uses. The composite action reads it and checks out `renglo/renglo-ops` at `v<platform>`, so the pipeline and this command stay on the same library.

## 3. How the workflows call this library

Workflows install the **library**, not the `renglo` command:

```bash
pip install "renglo-ops==0.1.0"
```

Before a registry exists, install that version from a git tag of this repository. After [Project 2](project-2-registry.md), install it from CodeArtifact like any other package.

Release jobs call a script by name. When an argument is a path to `renglo.yaml`, it is projected into the catalog shape those scripts already read:

```bash
python -m renglo_ops.release render_deploy_matrix renglo.yaml --pipeline backend --stage staging
```

`python -m renglo_ops.release` with no arguments lists the script names.

Image builds need the Dockerfile and the two installers on disk. They ship as package data, so materialize them into the BOM workspace before `docker build`:

```bash
python -m renglo_ops.image
```

That writes `Dockerfile` in the current directory and the installers under `scripts/`, the layout the Dockerfile copies.

Jobs that synth peer compute install the CDK extra:

```bash
pip install "renglo-ops[cdk]==0.1.0"
python -m renglo_ops.cdk.peer
```

There is no machine file in CI. Set `RENGLO_TENANT` to the `renglo.yaml` path, `RENGLO_WORKSPACE` to the product checkout, and `PEER_ID` when the job targets one peer.

Product repositories do not install this package to publish. They call the reusable workflow:

```yaml
jobs:
  publish:
    uses: renglo/renglo-ops/.github/workflows/publish-extension.yml@v0.1.0
    secrets: inherit
```

The tag matches the package version. Connecting a repo and its Actions variables is in [project-2-registry.md](project-2-registry.md#process-2--giving-a-repository-a-slot).

## 4. Trigger it

The workflows watch the files that describe desired state. `deploy.yml` runs on a push to `main` touching `renglo.yaml`, `bom/**`, or the workflow itself; `deploy_console.yml` watches `console_bom/**`. Environments with peers add `deploy_peers.yml` for `peers_bom/**`. Each workflow also offers `workflow_dispatch` for a narrow rerun.

A release train is therefore an ordinary commit on the BOM repository:

```bash
git convoy adopt --bom path/to/acme-bom
# review the pin changes, then commit and push the BOM repo
```

Pushing `main` starts the deploy. Nothing in the pipeline regenerates a BOM — git-convoy owns that, and `renglo` owns the infrastructure the BOM lands on.

## 5. Confirm the first run

Watch the Actions run and check, in order: the OIDC step assumed a role in the environment's account, the matrix step resolved the tenants you expect, the package install reached the registries in `renglo.yaml`, and the CDK step updated the stacks rather than creating parallel ones. A failed OIDC step almost always means `github.owner_id` or `github.repo_id` is missing and stack A needs redeploying.

---

## Coming back to this project

| Change | Do this |
| --- | --- |
| Another account becomes a deploy target | Enable it in `accounts`, redeploy stack A there, rerun the pipeline |
| A new peer appears | Add its BOM under `peers_bom/<id>/`, push, or dispatch `deploy_peers.yml` with that peer |
| `renglo-ops` moves forward | Bump `platform`, so CI and this command upgrade together |
| A registry changes hands | Update `registries` in `renglo.yaml` and confirm the reader grant, then push |
