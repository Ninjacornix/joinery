# Signing Joinery releases

Every release archive is signed in two independent ways. One works today with no
accounts; the other switches on by itself once the accounts are added. The design
is in [ADR 0083](adr/0083-updater-and-signing.md).

| What | Proves | Needs | State |
|---|---|---|---|
| Sigstore signature (`<archive>.sigstore.json`) | This repository's `release.yml`, run for this tag, built the archive | Nothing: GitHub's own OIDC identity | On for every tag |
| GitHub build attestation | Same, shown on the repository's Attestations page | Nothing | On for every tag |
| macOS Developer ID + notarization | Apple knows the developer; Gatekeeper opens the app without a warning | Apple Developer Program | Off until the secrets exist |
| Windows Authenticode | SmartScreen knows the publisher | A code signing certificate, or Azure Trusted Signing | Off until the secrets exist |

Without the platform accounts a release is what it was before: the macOS app is
ad-hoc signed and not notarized, and the Windows executables are unsigned. The
release workflow appends a **Signatures** section to the release notes that says
which of these a release has.

## Checking a download by hand

```bash
joinery verify Joinery-0.6.0-macos-arm64.zip          # needs the .sha256 and .sigstore.json beside it
joinery verify Joinery-0.6.0-macos-arm64.zip --offline  # Sigstore's built-in trust root, no network
gh attestation verify Joinery-0.6.0-macos-arm64.zip --repo irchiinnuss/joinery
```

Without Joinery, the Sigstore command line does the same check:

```bash
pipx run sigstore verify github Joinery-0.6.0-macos-arm64.zip \
  --cert-identity https://github.com/irchiinnuss/joinery/.github/workflows/release.yml@refs/tags/v0.6.0 \
  --repository irchiinnuss/joinery --ref refs/tags/v0.6.0
```

The updater (`joinery update`, and Check for updates in the Project Manager and the
editor) runs the same check before it unpacks anything, and refuses a release that
has no signature.

## Adding the accounts later

Add each value under **Settings → Secrets and variables → Actions → New repository
secret** of `irchiinnuss/joinery`. Nothing else changes: the next tag picks them up.
A platform is signed only when *all* of its secrets are present; a partly filled set
is treated as absent, so a half-finished setup never breaks a release.

### macOS: Developer ID, notarization, stapling

1. Join the Apple Developer Program (individual or organisation).
2. In Xcode → Settings → Accounts → Manage Certificates, or at
   developer.apple.com → Certificates, create a **Developer ID Application**
   certificate. Export it from Keychain Access as a `.p12` with a password.
3. At account.apple.com → Sign-In and Security → App-Specific Passwords, create a
   password for notarization.

| Secret | Value |
|---|---|
| `APPLE_CERT_P12` | The `.p12`, base64: `base64 -i DeveloperID.p12 \| pbcopy` |
| `APPLE_CERT_PASSWORD` | The `.p12` export password |
| `APPLE_ID` | The Apple ID email of the developer account |
| `APPLE_TEAM_ID` | The 10-character Team ID (developer.apple.com → Membership) |
| `APPLE_APP_PASSWORD` | The app-specific password from step 3 |

The `macos-sign` job (macOS runner, tags only) imports the certificate into a
throwaway keychain, signs `Contents/MacOS/Godot` and then the bundle with the
hardened runtime, a secure timestamp and `release/macos-entitlements.plist`
(Godot's editor entitlements, without the camera), submits the app to
`notarytool` and waits, staples the ticket, checks it with `spctl`, re-zips it with
`ditto` (which keeps the signature) and rewrites the `.sha256`. The Sigstore
signature is made afterwards, over the signed archive.

To rehearse the macOS step locally without an account (ad-hoc, no notarization):

```bash
python3 release/platform_sign.py macos Joinery-0.6.0-macos-arm64.zip --identity - --no-notarize
```

### Windows: Authenticode

Choose one. If both sets are present, Azure Trusted Signing is used.

**Azure Trusted Signing** (Microsoft's managed service; no hardware token):

1. Create a Trusted Signing account and a certificate profile (public trust) in the
   Azure portal, and complete identity validation.
2. Create an app registration (service principal) with a client secret, and give it
   the *Trusted Signing Certificate Profile Signer* role on the account.

| Secret | Value |
|---|---|
| `AZURE_TENANT_ID` | Directory (tenant) ID |
| `AZURE_CLIENT_ID` | The app registration's client ID |
| `AZURE_CLIENT_SECRET` | Its client secret |
| `AZURE_SIGNING_ENDPOINT` | The account's region endpoint, e.g. `https://weu.codesigning.azure.net/` |
| `AZURE_SIGNING_ACCOUNT` | The Trusted Signing account name |
| `AZURE_CERT_PROFILE` | The certificate profile name |

**A code signing certificate** (`.pfx`, from a certificate authority) used with
`signtool`:

| Secret | Value |
|---|---|
| `WINDOWS_CERT_PFX` | The `.pfx`, base64 (`[Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx"))`) |
| `WINDOWS_CERT_PASSWORD` | Its password |

Most certificate authorities now issue code signing keys only on hardware tokens
or in a cloud HSM, which a `.pfx` secret cannot hold; Azure Trusted Signing avoids
that. The `windows-sign` job signs `godot*.exe` (the editor and its console
wrapper) with SHA-256 and an RFC 3161 timestamp, checks them with
`Get-AuthenticodeSignature`, puts them back into the archive with their original
entry order and modes (`release/platform_sign.py windows`), and rewrites the
`.sha256`. The Microsoft DirectX DLLs are already signed by Microsoft;
`Joinery.cmd` is a batch file and cannot carry a signature.

## What stays true without any account

- The updater installs only archives whose SHA-256 matches the published `.sha256`
  *and* whose Sigstore bundle names this repository's release workflow at that tag.
- Gatekeeper and SmartScreen still warn on first launch; the release notes say how
  to open the app anyway.
- Joinery never stores or brokers these credentials: they live only in the
  repository's Actions secrets and are read by the tag's signing jobs.
