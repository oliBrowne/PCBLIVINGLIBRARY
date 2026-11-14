# DigiKey, Mouser and Nexar keys: a step-by-step guide

ecadlib can ask DigiKey, Mouser and Nexar (Octopart) about each part in your BOM: who makes it, what it is, its
package and where its datasheet is. Each of them only answers programs that bring **your own free key**. This page
walks you through getting each key, where to paste it, and how to check it works.

**All of this is optional.** Without any key, ecadlib still builds parts from KiCad's libraries, EasyEDA/LCSC and its
own standard packages, and a KiCad library works with no keys at all. A key just lets ecadlib identify more parts and
find their datasheets. You can set up one, two or all three.

The keys are yours. ecadlib keeps them on your computer (encrypted for your Windows account on Windows), sends each one
only to the company it belongs to, and never sends them to us. See [API keys and AI costs](api-keys.md) for where they
are stored.

## Where to paste a key

Either way works, and both store the key in the same place:

* **The setup page (easiest).** Run `ecadlib setup` (or open the desktop app), find the card
  **Part lookups: DigiKey, Mouser, Nexar (optional)**, open the distributor, paste the key, press **Save**, then press
  **Check it works**. The card shows the same steps as this page.
* **The command line.** `ecadlib keys set NAME` asks for the key and hides what you type. The names are listed under
  each distributor below.

## How to check it works

On the setup page, press **Check it works**. On the command line:

```
ecadlib keys check            # tests every distributor whose keys are saved
ecadlib keys check digikey    # or just one: digikey, mouser or nexar
```

The check does one real search for a common part (NE555DR) with your saved key and tells you in plain words whether
it worked, and if not, what to fix. It is free for DigiKey and Mouser. Nexar counts every part it returns against your
allowance, so its check asks for a single result and uses one part.

## DigiKey

Free. Sign up at <https://developer.digikey.com>.

1. Go to developer.digikey.com and click **Register / Login**. New here? Click **Register now** and make a free
   account (DigiKey emails you to confirm the address). The first time, accept the **API User Agreement**.
2. On **Organizations**, click **+ Create Organization**. Give it a name no other DigiKey user has (for example your
   name plus "ecadlib") and click **Add organization**.
3. Open the organization's **Production Apps** tab and click **+ Create Production App**.
4. Fill in the form:
   * **Production App name**: `ecadlib`
   * **OAuth Callback**: must be filled in and start with https. Type `https://localhost` (ecadlib never uses it).
   * **Description**: anything short.
   * **Products**: set **Product Information V4** to **ProductInformation V4** and leave the rest on **None**.

   Click **Add production app**. It is approved at once; there is no waiting.
5. Click the app's name. Its page shows the **Client ID** and **Client Secret**.

Paste them into the two DigiKey boxes on the setup page, or:

```
ecadlib keys set DIGIKEY_CLIENT_ID
ecadlib keys set DIGIKEY_CLIENT_SECRET
```

Use a **production** app inside an organization, not one from "Sandbox Apps" in the user menu. If the check says
DigiKey knows the app but refused the search, open the app and make sure Products has Product Information V4 set to
ProductInformation V4.

## Mouser

Free, up to 30 searches a minute and 1,000 a day. Sign up at <https://www.mouser.com/api-search/>.

1. Open mouser.com/api-search and click **Sign Up for Search API**. Sign in, or click **Create a My Mouser Account**
   first (free).
2. Fill in the request form:
   * Contact information: your name and email come from the account. **Phone** and **Company Name** are required (a
     hobbyist can write "Personal"). **Company Type**: Personal - Hobbyist, Student, Etc.
   * API data preferences: pick the **Country** first. The Language and Currency lists fill in only after it.
   * About your application: **Application Name** `ecadlib`, a one-line description, **Application Type** Internal
     desktop application, **Estimated Userbase** 1-10 Users, users charged **No**, advertising **No**.
3. Tick **I agree to Mouser's Web API Terms of Service** and click **Submit Request**.
4. Mouser emails you the **Search API key**.

Paste it into the Mouser box on the setup page, or:

```
ecadlib keys set MOUSER_API_KEY
```

## Nexar (Octopart)

Nexar gives you free parts to start with. It counts every part it returns, and more parts need a Nexar plan. Sign up at
<https://portal.nexar.com>.

1. Sign in or sign up at portal.nexar.com.
2. Click **Apps**, then **Create App**. Tick **Supply** for part lookups. If you also want to send parts to Altium 365,
   tick **Design** as well: an app's scope cannot be changed later.
3. A new app can look up 0 parts until it has a plan: click **Manage supply plan** and pick one. Or use the
   **Evaluation** app Nexar puts on your dashboard, which comes with free parts.
4. Open the app and copy its **Client ID** and **Client Secret**.

Paste them into the two Nexar boxes on the setup page, or:

```
ecadlib keys set NEXAR_CLIENT_ID
ecadlib keys set NEXAR_CLIENT_SECRET
```

### Altium 365 (only if you release parts there)

Sending parts to an Altium 365 workspace uses the same kind of Nexar app, with **Design** ticked, and a browser sign-in
instead of a pasted key:

1. In the app's settings on portal.nexar.com, add the redirect address `http://localhost:3000/login`.
2. On the setup page's **Altium 365** card, paste the app's Client ID and Client Secret and press
   **Sign in to Altium 365**, or run `ecadlib nexar signin`. A browser tab opens: sign in and allow access.
   ecadlib then keeps you signed in.

One app with both **Supply** and **Design** ticked does both jobs with one Client ID and Client Secret. If something
else already uses port 3000 on your computer, `ecadlib nexar signin` says so; see `ecadlib nexar signin --help`.

## If a check fails

| What the check says | What to do |
|---|---|
| "save ... first" | That key is not saved yet. Paste it and press Save (or run `ecadlib keys set NAME`). |
| refused the Client ID / Client Secret / key | Copy it again. Watch for a missing first or last character. |
| DigiKey knows the app but answered 401/403 | Set Products > Product Information V4 on the app; use a production app. |
| Nexar refused the app | Check the app has the Supply scope. A Design-only app cannot look up parts. |
| Nexar: part limit / no parts left | Pick a plan under Manage supply plan, or use the Evaluation app. |
| could not reach | Check the internet connection or a company proxy, then try again. |

`ecadlib doctor` also lists which part lookups have keys saved.
