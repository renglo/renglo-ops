# Project 5 — Federated sign-in

Goal: people can sign in through external identity providers (Google, Okta, SAML, and Cognito’s built-in social providers) while Cognito remains the only token issuer. The Console starts one authorization-code redirect per enabled provider; the API keeps validating Cognito JWTs.

Prerequisite: [configuration.md](configuration.md), and a deployed hub from [project-1-greenfield.md](project-1-greenfield.md).

You can run this project any time after Project 1. Email and password from greenfield does not change. Projects 2–4 are unrelated unless CI deploys `auth/providers.yaml` for you ([project-4-pipeline.md](project-4-pipeline.md)).

Cognito stays the only token issuer. The Console starts one authorization-code redirect for every enabled provider. The API keeps validating Cognito JWTs.

---

## Set up Google for Stanley

The login screen can show **Continue with Google** before Google is connected. In local development that button comes from the Console reference list, so it appears even when Cognito has no Google provider yet. Clicking it then fails. The button works after the four steps below.

You need two values from Google, and you put them in AWS, not in git:


| What                | Where you get it     | Where it goes                         |
| ------------------- | -------------------- | ------------------------------------- |
| OAuth client id     | Google Cloud Console | Secrets Manager field `client_id`     |
| OAuth client secret | Google Cloud Console | Secrets Manager field `client_secret` |


Nothing else is typed into the Console or the API. `ops/skbrk-bom/auth/providers.yaml` already points at the secret name `stanley0731/renglo/auth/google`.

### 1. Create the Google OAuth client

In [Google Cloud Console](https://console.cloud.google.com/):

1. Pick or create a project.
2. Open **APIs & Services → OAuth consent screen**. Set the app name, a support email, and the scopes `openid`, `email`, and `profile`. While the app is in **Testing**, add the Google accounts that will sign in as test users.
3. Open **APIs & Services → Credentials → Create credentials → OAuth client ID**.
4. Application type: **Web application**.
5. Authorized redirect URI (this is Cognito, not the Console):
  `https://{env}.auth.{region}.amazoncognito.com/oauth2/idpresponse`
6. Copy the **Client ID** and **Client secret**.



### 2. Store them in Secrets Manager

Use the AWS account in `ops/skbrk-bom/renglo.yaml` (`339713094352`, `us-east-1`) and the same CLI profile you use for `renglo`:

```bash
aws secretsmanager create-secret \
  --name PASTE_ENV/renglo/auth/google \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --secret-string '{"client_id":"PASTE_CLIENT_ID","client_secret":"PASTE_CLIENT_SECRET"}'
```

If the secret already exists, update it with `aws secretsmanager put-secret-value` using the same `--profile`, `--region`, and `--secret-string`. Do not commit those two values.

### 3. Deploy the hub auth stack

From the workspace:

```bash
renglo stack deploy --stack a,b
```

That keeps the existing user pool and registers Google on it. It also allows the local Console callback `http://127.0.0.1:5174/callback`. Deploy fails if the secret from step 2 is missing. Stack B does not register identity providers.

### 4. Restart the local Console

`VITE_COGNITO_DOMAIN` is read when Vite starts. Restart `./renglo --here`, then open `http://127.0.0.1:5174/login` and choose **Continue with Google**.

**Continue with Okta** is the other reference button. It fails until an Okta issuer and a secret `{ENV}/renglo/auth/okta` exist and that entry in `auth/providers.yaml` is `enabled: true`. Leave it disabled until then.

Email and password sign-in does not use this setup.

Which providers exist is a file in the tenant BOM repository, `auth/providers.yaml`, next to `renglo.yaml`. Package pin files are not the place for it. A missing file leaves email and password as the only sign-in.

## Secrets

Put each provider's credentials in AWS Secrets Manager in the tenant account:

`{env}/renglo/auth/{provider_id}`

The JSON holds `client_id` and `client_secret`. Sign in with Apple uses `client_id`, `team_id`, `key_id`, and `private_key`. SAML metadata, when it is a document rather than a URL, uses `metadata`.

`auth/providers.yaml` stores that secret name. Issuer URLs and the Cognito provider name stay in the file. The hub deploy reads the secret when it creates the Cognito identity provider. The API and the Console never receive it.

The external application's redirect URI is the Cognito endpoint:

`https://{env}.auth.{region}.amazoncognito.com/oauth2/idpresponse`

The Console callback (`/callback`) is registered on the Cognito app client, not on the external provider.

## Enable another provider

Add an entry and create the secret. No change to `renglo-lib`, `renglo-api`, or the Console login code.

Okta, or any other OIDC issuer:

```yaml
- id: okta
  name: Okta
  type: oidc
  cognito_provider_name: Okta
  enabled: true
  display_name: Continue with Okta
  icon: okta
  display_order: 20
  secret: example/renglo/auth/okta
  oidc:
    issuer: https://your-tenant.okta.com
    attribute_mapping:
      email: email
      email_verified: email_verified
      given_name: given_name
      family_name: family_name
      username: sub
```

A second Okta tenant is another entry with its own `id`, `cognito_provider_name`, issuer, and secret.

Meta uses Cognito's built-in Facebook provider:

```yaml
- id: facebook
  name: Meta
  type: social
  cognito_provider_name: Facebook
  enabled: true
  display_name: Continue with Meta
  icon: facebook
  display_order: 30
  secret: example/renglo/auth/facebook
```

Other built-in social names are `Google`, `LoginWithAmazon`, and `SignInWithApple`. SAML uses `type: saml` and either `saml.metadata_url` or a `metadata` field in the secret.

`enabled: true` with a missing secret name, an unknown type, or a social name Cognito does not support fails the deploy. Disabled entries stay out of the login screen and off the app client.

Deploy both hub stacks after the secret exists (`renglo stack deploy --stack a,b`, or the default deploy with no `--stack`). Stack B writes the public list to SSM `platform-vars` as `AUTH_PROVIDERS`. The Console reads `GET /_auth/providers` on the deployed API.

## Reference login

The Console ships `src/auth/reference-providers.json` with email and password, Google, and Okta. Local development uses that list when the API has no providers, so the login screen shows both external options. Production shows only what the tenant file enables.

## Existing account

Continue with Google signs in to the user who already has that email. Google has already verified the address, so the Pre Sign-up function links that Google identity to the existing user. Cognito stops that first attempt so it does not create a second profile. The Console starts Google sign-in once more, and that attempt opens the existing account. Another provider is linked only when it reports the email as verified. Map `email_verified` on the provider so a later Google sign-in does not clear the existing user's verified email.

## Invite id

An invite link may include `?invite=<uuid>`. Sign-in keeps that value through password login and through every provider redirect. It is stored beside the session and inside the OAuth `state` value. Comparing it with an invitation is a later authorization step. This flow does not do that check.

---

## Coming back to this project

| Change | Do this |
| --- | --- |
| Enable or disable a provider in `auth/providers.yaml` | Create or update the Secrets Manager secret, then `renglo stack deploy --stack a,b` |
| Rotate an OAuth client secret | `aws secretsmanager put-secret-value`, then redeploy stack A so Cognito picks up the new values |
| Hosted login shows the wrong provider buttons | Redeploy stack B (or the full hub) so `AUTH_PROVIDERS` and `GET /_auth/providers` match the file |
| Local Console after a hub deploy | `renglo state local-config --apply` if Cognito domain or pool ids changed, then restart `./renglo --here` |

---

## Next

Day-to-day deploy flags and stack status: [operator.md](operator.md). When [Project 4](project-4-pipeline.md) is live, pushing `auth/providers.yaml` triggers the same hub deploy on the next train; until then, deploy by hand from this project.
