# Operating a running environment

These commands are for an environment that already exists. They are not part of Project 1–4. Desired state stays in `renglo.yaml`. The commands below either read AWS, change a stack that the file already describes, or manage mail and users on the account that file names.

Prerequisite: [configuration.md](configuration.md). The AWS profile is `--profile`, then `AWS_PROFILE`, then `profile` in `.renglo/local.yaml`. The region is the primary account in `renglo.yaml` when nothing else is set.

`renglo help operate` prints the list.

---

## See what is running

### `renglo status --live`

The command without `--live` prints the tenant file: name, GitHub repo, pins, accounts, profile.

`--live` adds three lines from the account:

```text
stacks: a=UPDATE_COMPLETE b=UPDATE_COMPLETE
peers: lab=UPDATE_COMPLETE
ssm: production FROM_EMAIL=noreply@acme.com BASE_URL=https://....amazonaws.com/production
```

`ABSENT` means that stack is not in CloudFormation. A peer line appears only when `placement.peers` is non-empty.

### `renglo stack status [a|b]`

CloudFormation status for `{env}-stack-a`, `{env}-stack-b`, or both when you omit the letter.

### `renglo state live`

Prints `VARS` from `/{env}/bootstrap/platform-vars/staging` and `.../production`. URLs and the sender come first; the remaining keys are alphabetical.

`renglo state show` is a different command. It prints `renglo.yaml` as JSON and does not call AWS.

Stack B writes those SSM parameters during deploy. There is no `state publish` command.

### `renglo extension tree`

Reads `packages` and `placement` only.

```text
acme1
  data                 hub              renglo-data
  billing              peer:lab         acme-billing
```

Only placed extensions appear. A `packages` entry with no hub or peer row is a library the environment installs (`renglo-lib`, `renglo-api`, the console), not an extension, so it is left out. Hub rows match either the handle or the `python` / `npm` package name, because `placement.hub` stores package names.

### `renglo extension show HANDLE`

One handle: where it runs, and the Python and npm package names.

### `renglo peer list`

Every peer in `placement.peers`: id, compute, handles.

### `renglo peer status [--peer-id PEER]`

CloudFormation status of `{env}-peer-{peerId}`. Without `--peer-id`, every peer in the file.

### `renglo peer show PEER`

Compute, task size, extensions, BOM pin, and the stack name. This does not call AWS; pair it with `peer status` when you need the live stack.

---



## Change infrastructure



### `renglo stack deploy --stack a|b|a,b`

Same synth as today. `--stack` limits `cdk deploy` to `{env}-stack-a`, `{env}-stack-b`, or both. The default is `a,b`.

Use `--stack b` after a hub placement edit. Use `--stack a` for a core change (auth, mail, storage, OIDC). A peer deploy does not update stack A.

`--dry-run` prints the synth and the deploy and does not call AWS.

### `renglo account bootstrap [--dry-run]`

Runs `cdk bootstrap aws://<account>/<region>` for the primary account in `renglo.yaml`. Safe to re-run. Needed once before the first deploy into an account that has no CDK toolkit stack.

### `renglo stack destroy --stack a|b|a,b --yes`

Starts `DeleteStack` and prints `DELETE_IN_PROGRESS`. It does not wait for `DELETE_COMPLETE`; run `renglo stack status` to watch.

`--stack` is required. Destroying A while B still exists is refused. Destroy B first, or pass `--stack a,b`, which deletes B and then A. `--dry-run` prints the stack names and does not call AWS. Peer stacks are not included.

### `renglo peer destroy --peer-id PEER --yes`

Deletes `{env}-peer-{peerId}`. The hub is untouched. The peer must be named in `placement.peers`. Removing it from the file is a separate edit; this command only deletes the CloudFormation stack.

### `renglo peer deploy --peer-id PEER`

Unchanged. Synth and deploy one peer, or every peer when `--peer-id` is omitted. `--dry-run` prints the commands.

Moving a handle between the hub and a peer is still an edit to `renglo.yaml` plus the two deploys in [project-3-extensions.md](project-3-extensions.md). This CLI does not keep an install sheet, does not create BOM pins, and does not commit the BOM repository.

---



## Mail

SES starts in the sandbox. Until the account has production access, mail can go only to identities you have verified. These commands do not file the production-access request.

### `renglo email sender-status`

The from-address in `renglo.yaml`, whether the identity is an address or a domain, the SES verification status, and whether the account is still in the sandbox.

### `renglo email verify-sender`

When `email.identity` is `email`, SES sends the verification message to `email.from` again. When it is `domain`, SES starts domain verification and prints the verification token.

### `renglo email allow ADDRESS`

Sends a verification message to that recipient so the sandbox can deliver to them. If the account already has production access, the command prints that and does nothing.

### `renglo email allow-status`

Every SES email and domain identity in the region, with verification status. `Pending` means the confirmation message has not been confirmed yet.

---



## Admins and collaborators

`admin` creates a Cognito user before the application invite flow exists. `user invite` calls the running API as an existing admin. They are not interchangeable.

Both read the user pool id from `/{env}/bootstrap/platform-vars/`. Staging uses the staging parameter. `local` and `production` use the production parameter.

### `renglo admin create EMAIL [--console local|staging|production]`

Creates the user and asks Cognito to email a temporary password. If the user already exists, a pending invite (`FORCE_CHANGE_PASSWORD`) is resent; a confirmed user gets a password reset.

The command prints the setup URL:


| `--console`  | Link base                                   |
| ------------ | ------------------------------------------- |
| `production` | `FE_BASE_URL` from production platform-vars |
| `staging`    | `FE_BASE_URL` from staging platform-vars    |
| `local`      | `http://127.0.0.1:5174/`                    |


The message body Cognito sends is the invitation template deployed with stack A. `--console` chooses the URL this command prints. It does not rewrite that template.

### `renglo admin show EMAIL [--console local|staging|production]`

Pool lookup: user status and whether the user is enabled. `absent` when the pool has no such user.

### `renglo user invite EMAIL --team TEAM --portfolio PORTFOLIO`

`POST {BASE_URL}/_auth/user/invite` with `email`, `team_id`, and `portfolio_id`. `BASE_URL` comes from production platform-vars unless you pass `--api-url`.

Authenticate with `--token` (a Cognito id token) or with `--admin-email` and `--admin-password`. The password path uses `USER_PASSWORD_AUTH` against `COGNITO_APP_CLIENT_ID`. The app client must allow that flow.

