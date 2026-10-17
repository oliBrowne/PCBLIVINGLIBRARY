# Code signing the Windows installer

Without a signature, Windows shows "Windows protected your PC" the first time someone runs the installer. The build
(`.github/workflows/windows.yml`) signs `ecadlib.exe`, `ecadlib-app.exe` and `ecadlib-setup-<version>.exe` with
**Azure Artifact Signing** (formerly called Trusted Signing) as soon as the repository has the settings below. Until
then, the signing steps are skipped and the installer is built unsigned.

The certificate is issued to a verified person or organisation, so the maintainer has to do the one-time sign-up.

## One-time setup (about 30 minutes, then a few days for identity validation)

1. **Azure account.** Sign in at <https://portal.azure.com> and create a subscription. The service costs about
   USD 10 a month (the Basic tier).
2. **Create an Artifact Signing account.** In the portal, search "Artifact Signing" (or "Trusted Signing") and choose
   Create. Pick a region close to you, the Basic tier, and a name such as `ecadlib-signing`. The region decides the
   endpoint, for example `https://weu.codesigning.azure.net/` for West Europe or `https://eus.codesigning.azure.net/`
   for East US.
3. **Prove who you are.** In the account, go to Identity validation and choose New, then **Public** and
   **Individual** (or Organization, if you have a company). Follow the steps. Microsoft checks your ID, which usually
   takes a few days.
4. **Certificate profile.** Once validation passes, go to Certificate profiles and choose Create > **Public Trust**.
   Name it, for example `ecadlib`, and pick the validation from step 3.
5. **An identity for GitHub Actions.** Go to Microsoft Entra ID > App registrations > New registration, named for
   example `ecadlib-github-signing`. Copy the **Application (client) ID** and the **Directory (tenant) ID**. Then go to
   Certificates & secrets > New client secret and copy its **Value**.
6. **Allow it to sign.** In the Artifact Signing account, go to Access control (IAM) > Add role assignment > **Artifact
   Signing Certificate Profile Signer** (or "Trusted Signing Certificate Profile Signer"), and pick the app from step 5.
7. **Tell GitHub.** In the repository, go to Settings > Secrets and variables > Actions.
   - **Secrets:** `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` (from step 5).
   - **Variables:** `AZURE_SIGNING_ENDPOINT` (step 2), `AZURE_SIGNING_ACCOUNT` (the account name),
     `AZURE_SIGNING_PROFILE` (the profile name from step 4).

The next Windows build then signs everything and fails if a signature is not valid. The client secret expires (the
default is 6 months). When it does, make a new one and update `AZURE_CLIENT_SECRET`.

## The free alternative

[SignPath Foundation](https://signpath.org) signs open-source projects for free. You apply, and they decide. Their
signing runs through their own GitHub action, so switching to it means replacing the two signing steps in
`windows.yml`.

## Notes

- Even with a valid signature, a new certificate starts with no SmartScreen reputation. A few early users may still see
  the warning until enough people have installed it. Microsoft's own service builds that reputation fastest.
- Pull requests from forks have no access to the secrets, so their builds stay unsigned. That is expected.
