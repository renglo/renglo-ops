# Project 3 — Additional infrastructure

Goal: every extension this environment runs has a place to run and a registry to come from. Two decisions per extension, both recorded in `renglo.yaml`: **which package** it is, and **where it runs** — on the hub, or on a peer.

Prerequisite: [configuration.md](configuration.md), and a hub from [project-1-greenfield.md](project-1-greenfield.md).

An extension is identified by a **handle**, its short name (`data`, `schd`, `billing`). One handle runs in exactly one place.

---

## Hub or peer

Start on the hub. The hub is already deployed, shares the platform's IAM and networking, and needs no extra stack.

A peer exists because something did not fit on the hub:

| Reason | What a peer gives you |
| --- | --- |
| Work runs longer than a hub invocation allows | Its own ECS compute, Fargate or EC2 |
| It needs more CPU or memory than the hub grants | `task_size`, or an EC2 instance type you choose |
| It needs permissions the hub should not hold | Its own IAM role and instance profile |
| It must be isolated, or pinned to its own region | Its own stack, `{env}-peer-{peerId}`, and optional `aws_region` |
| Its release cadence differs from the hub's | Its own BOM file under `peers_bom/<id>/` |

Cost of a peer: another stack to deploy, another BOM to pin, another set of permissions to reason about. The hub is the default; a peer is what you reach for once the hub says no.

---

## 1. Name the packages

`packages` maps a handle to what gets installed. A handle can ship a Python wheel, an npm package, or both.

```yaml
packages:
  data:
    python: renglo-data
    npm: '@renglo/data'
  schd:
    python: renglo-schd
    npm: '@renglo/schd'
  billing:
    python: acme-billing
    npm: '@acme/billing'
```

Where those packages come from is `registries` — [project-2-registry.md](project-2-registry.md). A package in a foreign CodeArtifact domain needs a `registries` row carrying that domain's `account`, and the domain owner needs this environment's account in `reader_accounts`. Packages on public PyPI or npmjs need no row; the `python-store` and `npm-store` repositories have public upstreams.

Check the pairing before deploying: every package name under `packages` should resolve in one of the registries the environment can read.

## 2. Place them on the hub

`placement.hub` is a list of the package names whose infrastructure belongs to stack B.

```yaml
placement:
  hub:
    - renglo-data
    - renglo-schd
    - acme-billing
  peers: {}
```

Deploy the change:

```bash
renglo stack deploy --dry-run
renglo stack deploy
```

Stack A is untouched in practice; stack B picks up the hub-placed extensions, their blueprints, and the hub IAM they need.

## 3. Or place them on a peer

`placement.peers` is a map of peer id to that peer's definition. The id is lowercase letters, digits, and hyphens, up to 32 characters.

```yaml
placement:
  hub:
    - renglo-data
  peers:
    lab:
      compute: fargate
      task_size: medium
      extensions:
        - billing
      peers_bom: 0.1.0
```

| Field | Required | Meaning |
| --- | --- | --- |
| `extensions` | Yes | Handles this peer runs. A handle may appear on only one peer |
| `peers_bom` | Yes | BOM version this peer installs. Falls back to the catalog's `handlers_bom` |
| `compute` | No | `lambda_only`, `fargate` (default), or `ec2` |
| `task_size` | No | `small`, `medium` (default), or `large` |
| `bom_path` | No | Where its BOM lives. Defaults to `peers_bom/<id>` |
| `iam_profile` | No | Extra IAM profile to attach |
| `aws_region` | No | Region for this peer. Defaults to the environment's primary account region |
| `ec2_instance_type`, `ec2_min_instances`, `ec2_desired_instances`, `ec2_max_instances` | With `compute: ec2` | All four are required when compute is `ec2` |

Peer BOM files live in the BOM repository at `peers_bom/<id>/vX.Y.Z.json`. Deploying a peer before its BOM file exists fails the pin check.

```bash
renglo peer deploy --peer-id lab --dry-run
renglo peer deploy --peer-id lab
```

Omit `--peer-id` to synth every peer in the file. The hub is not part of this command, and updating one peer leaves stack A and the other peers alone.

Each peer gets a stack named `{env}-peer-{peerId}`, its own ECS cluster and task definition, its own results bucket, and its own role.

---

## Moving an extension after the fact

Placement is not permanent. Moving a handle from the hub to a peer is an edit and two deploys:

1. Remove its package from `placement.hub`, add the handle to that peer's `extensions`, and create `peers_bom/<id>/vX.Y.Z.json`.
2. `renglo peer deploy --peer-id <id>` to create or update the worker.
3. `renglo stack deploy` so stack B stops owning the hub-side infrastructure for it.

The reverse works the same way. Keep the handle on exactly one side at a time; listing it twice is rejected.

---

## Verify placement

```bash
renglo state show
```

The JSON echoes `packages`, `placement.hub`, and every peer row after defaults are applied — the same view the deploy and the release scripts read, which makes it the quickest check that a peer row parsed as intended.

---

## Next

Wire the BOM repository so these deploys happen without a laptop: [project-4-pipeline.md](project-4-pipeline.md). An environment that only ever deploys by hand can stop here.
