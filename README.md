# CatchProbe Alarm Integration

Fetches alarms from the CatchProbe backoffice API, forwards each alarm's detail
to a configured webhook, and then marks the alarm as `passive`. It can run once
or poll continuously for new alarms.

## How it works

For every cycle the service:

1. Resolves which rules to follow (`FOLLOW_RULE_IDS` or `--rules`):
   - `*` → the `core_rule_id` query parameter is omitted (all alarms).
   - specific ids → joined with commas and sent as a single `core_rule_id`.
2. Fetches alarms of a given status (`ALARM_STATUS`, default `new`), page by page
   (`per_page`, default `20`).
3. For each alarm: fetches its detail, POSTs the detail to the webhook, then
   PATCHes its status to `passive`.

Because every processed alarm becomes `passive` and leaves the status filter,
the live run repeatedly fetches the **first page** until the queue drains. This
keeps memory flat even with tens of thousands of alarms, and avoids pagination
drift while the data set shrinks.

Requests retry automatically on HTTP 429 (`Retry-After` aware, exponential
backoff), and a small configurable delay is inserted between alarms.

## Project layout

```
alarm_integration/
  config.py           Settings loaded from environment / .env
  client.py           CatchProbeClient: HTTP calls, pagination, retry
  service.py          AlarmProcessor: fetch -> webhook -> passive workflow
  cli.py              argparse CLI (rules / run subcommands)
  logging_config.py   logging setup
  __main__.py         enables `python -m alarm_integration`
main.py               backwards-compatible shim
```

## Configuration

All configuration comes from environment variables, which can be supplied via a
`.env` file. Copy the template and fill it in:

```bash
cp .env.example .env
```

| Variable                     | Required | Default                          | Description                                        |
| ---------------------------- | :------: | -------------------------------- | -------------------------------------------------- |
| `CATCHPROBE_API_KEY`         |   yes    | —                                | `x-api-key` header value                           |
| `CATCHPROBE_ORGANIZATION_ID` |   yes    | —                                | `organizationid` header value                      |
| `WEBHOOK_URL`                |   yes    | —                                | Webhook that receives alarm detail payloads        |
| `CATCHPROBE_BASE_URL`        |    no    | `https://core-api.catchprobe.io` | API base URL                                       |
| `FOLLOW_RULE_IDS`            |    no    | `*`                              | `*` for all, or a comma-separated list of rule ids |
| `ALARM_STATUS`               |    no    | `new`                            | Alarm status to process                            |
| `POLL_INTERVAL_SECONDS`      |    no    | `30`                             | Delay between polling cycles                        |
| `PER_PAGE`                   |    no    | `20`                             | Page size for the alarm list                       |
| `REQUEST_TIMEOUT`            |    no    | `30`                             | Per-request timeout (seconds)                      |
| `DELAY_BETWEEN_ALARMS`       |    no    | `1.0`                            | Pause between alarms (seconds)                     |
| `MAX_RETRIES`                |    no    | `5`                              | Retry attempts on HTTP 429                          |
| `RETRY_BACKOFF_SECONDS`      |    no    | `2.0`                            | Base backoff for retries                            |
| `LOG_LEVEL`                  |    no    | `INFO`                           | `DEBUG` / `INFO` / `WARNING` / `ERROR`             |

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env

# List the available filter rules
python -m alarm_integration rules

# Dry run: fetch details but change nothing
python -m alarm_integration run --dry-run

# Process once and exit
python -m alarm_integration run --once

# Poll continuously (default)
python -m alarm_integration run
```

Installed as a package, the same commands are available via the
`alarm-integration` console script:

```bash
pip install -e .
alarm-integration run --once
```

### CLI reference

```
alarm-integration rules                 # list filter rules
alarm-integration run                    # process alarms, polling
alarm-integration run --rules '*'        # follow every rule
alarm-integration run --rules id1 id2    # follow only the given rules
alarm-integration run --status active    # process a different status
alarm-integration run --dry-run          # inspect without changing anything
alarm-integration run --interval 60      # poll every 60 seconds
alarm-integration run --once             # single cycle, then exit
```

## Running with Docker

```bash
# Build and start the polling service
docker compose up --build

# One-off single run
docker compose run --rm alarm-integration run --once

# List rules
docker compose run --rm alarm-integration rules
```

Or with plain Docker:

```bash
docker build -t alarm-integration .
docker run --rm --env-file .env alarm-integration run --once
```

## Safety notes

- The `run` command (without `--dry-run`) **mutates data**: it forwards alarm
  details to the webhook and sets alarms to `passive`. Use `--dry-run` first.
- `.env` holds secrets and is git-ignored. Never commit real credentials.
