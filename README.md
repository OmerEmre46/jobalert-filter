# jobalert-filter

*[Türkçe](README.tr.md)*

Job-alert emails pile up fast, and most of them are the wrong level, the wrong field
or the wrong city. **jobalert-filter** reads those emails from your mailbox (read-only),
pulls out every posting, filters it against *your* profile with keyword rules and a
**local** LLM, and gives you:

- **`report.html`**: every posting ranked by fit, with the reasons and a link to apply
- **`tracker.csv`**: a spreadsheet for your applications (applied / interview / offer…)
- **a daily digest email** with only the new matches (optional)

It runs on your computer, costs nothing, and your mail never leaves your machine:
the LLM runs locally through [Ollama](https://ollama.com).

> Not affiliated with, endorsed by or connected to LinkedIn. It only reads the
> notification emails LinkedIn already sends you.

## How it works

```
mailbox (IMAP, read-only) ─► parse alert emails ─► dedupe by job id (SQLite)
   ─► keyword rules  (drop "Senior", "5+ years", wrong city …; score level + field)
   ─► local LLM      (compare with your profile and dealbreakers, explain why)
   ─► report.html · tracker.csv · daily digest
```

- Nothing in your mailbox changes: folders are opened read-only and messages are
  fetched with `BODY.PEEK`, so they are not even marked as read.
- The same job sent in five emails shows up once.
- The LLM is optional. Without Ollama, jobs get the rule score only.
- LLM answers are cached; they are refreshed only when your profile or the model changes.

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/OmerEmre46/jobalert-filter.git
cd jobalert-filter
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -e .
jobalert init                 # creates config.yaml, profile.yaml and .env
```

Then:

1. **Mailbox**: fill in `mail:` in `config.yaml` (see below for Gmail and Outlook).
2. **Profile**: describe yourself in `profile.yaml`: summary, skills, target roles,
   dealbreakers, and the keyword rules. The example is a computer-engineering student
   looking for an internship in Türkiye.
3. **LLM (recommended)**: install [Ollama](https://ollama.com/download), then
   ```bash
   ollama pull qwen2.5:7b-instruct
   ```
   This runs well on an 8 GB GPU. On a CPU-only laptop use `qwen2.5:3b-instruct` and
   set `llm.model` to match.
4. Test everything:
   ```bash
   jobalert auth     # signs in (Outlook) / tests the password (Gmail)
   jobalert check
   jobalert run --since 14d --open
   ```

### Gmail

Turn on 2-Step Verification, then create an **App Password**
(Google Account → Security → App passwords) and put it in `.env`:

```
JOBALERT_MAIL_PASSWORD=abcd efgh ijkl mnop
```

```yaml
mail:
  provider: gmail
  address: you@gmail.com
```

If your alerts skip the inbox because of a filter, add `"[Gmail]/All Mail"` to `folders`.

### Outlook / Hotmail / Live

Microsoft no longer accepts passwords over IMAP for personal accounts, so OAuth2 is
used. This needs a free one-time app registration of your own:

1. Open the [Azure portal → App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
   and choose **New registration**. (If Azure asks you to create a directory or a
   free account first, do that.)
2. Name: `jobalert-filter`. Supported account types: **Personal Microsoft accounts only**.
   No redirect URI. Register.
3. **Authentication** → *Allow public client flows* → **Yes** → Save.
4. **API permissions** → Add a permission → Microsoft Graph → Delegated →
   `IMAP.AccessAsUser.All`, `SMTP.Send`, `offline_access` → Add.
5. Copy the **Application (client) ID** into `config.yaml`:

```yaml
mail:
  provider: outlook
  address: you@hotmail.com
  ms_client_id: 00000000-0000-0000-0000-000000000000
```

**"The selected user account does not exist in tenant 'Microsoft Services'…"** —
a personal account cannot open the Azure portal until it has its own directory. Either:

- create one by signing up for [Azure for Students](https://azure.microsoft.com/free/students)
  (school email, no credit card) or the regular [Azure free account](https://azure.microsoft.com/free),
  then register the app as above; or
- register the app with any work/school account that is allowed to, choosing
  **Accounts in any organizational directory and personal Microsoft accounts**. The app
  does not need to live in the same account as the mailbox; or
- skip OAuth: in Outlook.com add an inbox rule *From contains linkedin.com →
  **Redirect to** your Gmail address* and use the Gmail setup. Use *Redirect*, not
  *Forward*: a forward replaces the sender with your own address, so the
  `sender_filter` no longer finds the alerts.

Run `jobalert auth`, open the link it prints and enter the code. The refresh token is
stored in `.jobalert/` (git-ignored), so later runs sign in silently.

### Any other provider

```yaml
mail:
  provider: imap
  address: you@example.com
  imap_host: imap.example.com
  smtp_host: smtp.example.com
```

## Daily use

| Command | What it does |
| --- | --- |
| `jobalert run [--open]` | fetch, score, rebuild the report and tracker, send the digest |
| `jobalert schedule install --times 09:00,18:00` | run automatically every day (Windows Task Scheduler; prints a cron line elsewhere) |
| `jobalert status 4012345678 applied --note "CV sent"` | update a job's status |
| `jobalert score --rescore` | ask the LLM again after big profile edits |
| `jobalert digest --dry-run` | preview the digest email |
| `jobalert fetch --since 30d --save-samples data/samples` | keep raw emails for writing parser tests |

You can also edit the `status` and `note` columns in `tracker.csv` (Excel works). Your
changes are read back on the next run. Valid statuses: `new, applied, interview, offer,
rejected, ignored`. The Turkish words (`başvuruldu`, `mülakat`, `ilgilenmiyorum`, …)
work too. `ignored` jobs move to the *Filtered out* tab.

The digest is off by default; set `digest.enabled: true`. It is sent to yourself at
most once a day, and a job is never sent twice.

Windows note: scheduled tasks only run while you are logged in and the computer is
awake. Missed runs are fine, because the next run picks up everything since the last one.

## Tuning the filter

- **Too many wrong jobs?** Add terms to `rules.reject_title`, or set
  `rules.require_level: true` to keep only intern/junior titles.
- **Good jobs filtered out?** Check the reason under the job in the *Filtered out* tab.
  It names the rule or the LLM's objection. Lower `min_score` or relax that rule.
- Rules are regexes matched against the title in folded form: lower case, Turkish
  letters normalised (`ı/İ → i`, `ş → s`, …). So `kıdemli` also matches `KIDEMLİ`.
- `llm.rule_weight` sets how much the keyword score counts against the LLM score.
- The LLM does not grade "fit" itself; small local models are unreliable at that.
  It only files each job under a field (`software`, `ai_data`, `web`, `it`,
  `other_technical`, `non_technical` or `unspecified`), and the score follows from
  `target_categories` in your profile. Experience requirements and dealbreakers are
  only checked when a full description is available, so the model can't invent them
  from a bare title.

## Reading full job descriptions (optional, off by default)

Alert emails usually contain only the title, company and location. The real
requirements are on the posting page. With `enrich.enabled: true` the tool fetches
the **public, logged-out** page of jobs that already passed the rules and gives the
description to the LLM. It is rate-limited (`delay_seconds`, `max_per_run`) and stops
at the first refusal.

**LinkedIn's User Agreement restricts automated access to its site.** This feature
is disabled by default; turn it on only if you accept that yourself.

## Privacy

`config.yaml`, `profile.yaml`, `.env`, `.jobalert/`, `data/` and `output/` are all
git-ignored. Job links are stored without LinkedIn's tracking parameters. Nothing
is sent anywhere except to your own mail server and your local Ollama.

## Contributing

LinkedIn changes its email templates now and then. If a field comes out wrong:

1. `jobalert fetch --since 7d --save-samples data/samples`
2. `python scripts/anonymize_fixture.py data/samples/0001.eml tests/fixtures/my_case.eml`.
   It strips your name, address and tracking links. **Check the output before you commit it.**
3. Add `tests/fixtures/my_case.expected.json` with the jobs you expect, then fix
   `src/jobalert_filter/parse/linkedin.py` until `pytest` passes.

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

### Roadmap

- Parsers for other alert senders (Kariyer.net, Indeed, Glassdoor)
- An employer-side mode that ranks incoming applications against a job description
  with the same rules + LLM core

## License

MIT
