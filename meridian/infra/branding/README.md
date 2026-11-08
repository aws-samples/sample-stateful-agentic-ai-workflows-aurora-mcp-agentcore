# Meridian sign-in branding

The application welcome screen uses `frontend/src/auth/SignInScreen.tsx`. Its inline
form sends credentials directly to Cognito's regional API. The hosted UI remains a
fallback for additional verification. These assets give that existing classic UI
the same Meridian mark, dark palette and blue primary action. They do not change
authentication, callback URLs, users or token validation.

`meridian-login.svg` is the editable source for the PNG logo. Regenerate it with
`rsvg-convert` from librsvg, from `meridian/`:

```bash
rsvg-convert -o infra/branding/meridian-login.png infra/branding/meridian-login.svg
```

After configuring the identity stack and `.env`, inspect the plan before applying:

```bash
venv/bin/python scripts/publish_signin_branding.py
venv/bin/python scripts/publish_signin_branding.py --apply --i-understand-this-changes-aws
```

The publisher verifies the AWS account and that the deployed identity stack matches
the configured pool and app client. It targets that client only, saves the original
customization response in private `.local/signin-branding/before.json`, and verifies
the returned CSS and logo URL. The response stores the previous logo URL, not a
copy of that image; retain any original artwork before replacing a custom logo.
An apply failure requires reading back the customization before retrying.

Cognito classic permits only a fixed set of CSS selectors and properties. The
publisher limits the logo to 100 KB and CSS to 3 KB to stay within the request
budget. See the [Cognito classic branding documentation](https://docs.aws.amazon.com/cognito/latest/developerguide/hosted-ui-classic-branding.html).
Allow up to one minute for updated styling to appear, then refresh the sign-in page.
