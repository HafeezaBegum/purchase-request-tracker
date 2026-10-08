# Purchase Request Tracker

A small internal tool that turns messy production and purchasing requests into
structured, trackable work.

At a manufacturing plant, requests usually arrive as quick messages:

> "Production needs 200 meters of copper wire by Friday."

> "need more wire asap"

Someone has to work out what's being asked, chase whatever is missing, and
track the request until it's delivered. This app handles the first pass:

1. **Reads the request** and extracts the item, quantity, unit, deadline, and department.
2. **Flags missing details instead of guessing**, and drafts the follow-up question for the requester.
3. **Tracks each request** through a clear workflow: `needs_info → new → approved → ordered → done` (or `rejected`).

![Dashboard](docs/dashboard.png)

**Stack:** Python · FastAPI · SQLite · Pydantic · pytest · Claude API (optional)

---

## Getting started

Requires Python 3.10+.

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open **http://localhost:8000** for the dashboard, or **http://localhost:8000/docs**
for the interactive API docs.

The app runs fully offline using a rules-based extractor. To use Claude for
better extraction, set an API key before starting:

```bash
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "your-key"
# macOS / Linux
export ANTHROPIC_API_KEY="your-key"
```

### Run the tests and the evaluation

```bash
pytest                          # 25 unit and API tests
python -m eval.run_eval         # accuracy of the rules extractor
python -m eval.run_eval --llm   # accuracy of the Claude extractor (needs API key)
```

---

## Example

**Input:** `Production needs 200 meters of copper wire by Friday`

| item | quantity | unit | deadline | department | status |
|---|---|---|---|---|---|
| copper wire | 200 | m | 2026-10-09 | Production | `new` |

**Input:** `need more wire asap`

Every field is missing, so the request goes to `needs_info` and the app drafts:

```
Thanks for the request. Before we can order, could you confirm:
- What exactly is needed (material, gauge/size, any spec)?
- How much is needed?
- What date is it needed by?
- Which department is this for?
```

Once someone fills in the missing details, the request moves to `new`
automatically.

---

## How it works

```
request text
     │
     ▼
 extract() ──► Claude (JSON schema)  ── fails? ──► rules extractor
     │
     ▼
 validated fields (Pydantic)
     │
     ├── nothing missing ──► status: new
     └── fields missing  ──► status: needs_info + drafted clarification
     │
     ▼
 SQLite (requests + status history)
```

| File | Responsibility |
|---|---|
| `app/extract.py` | Rules extractor, Claude extractor, fallback logic, clarification drafts |
| `app/models.py` | Data shapes and validation (Pydantic) |
| `app/main.py` | REST API and status workflow rules |
| `app/db.py` | SQLite storage and status-change history |
| `app/dashboard.html` | Single-page dashboard (plain HTML/JS, no build step) |
| `tests/` | Unit tests for extraction, API tests for the workflow |
| `eval/` | 24 labeled sample requests and an accuracy script |

### API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/requests` | Submit `{"text": "...", "department": "optional"}` |
| `GET` | `/requests?status=needs_info` | List requests, optionally filtered by status |
| `GET` | `/requests/{id}` | Get one request |
| `PATCH` | `/requests/{id}/fields` | Fill in missing details |
| `PATCH` | `/requests/{id}/status` | Move a request through the workflow |
| `GET` | `/health` | Health check |

---

## Design decisions

**A blank field is better than a wrong one.** "ASAP", "end of next week", and
"some wire" are left empty and flagged. A guessed deadline that looks real is
more dangerous than a missing one, because nobody double-checks it.

**The AI part is optional.** Claude extracts more accurately than the rules,
but nothing depends on it. If the API is down, rate-limited, or returns output
that fails validation, the request falls back to the rules extractor. Intake
keeps working either way.

**AI output is validated, not trusted.** Claude's response is constrained by a
JSON schema and then validated again with Pydantic. For example, a negative
quantity is rejected.

**The workflow is enforced.** Illegal status changes (like `new → done`) return
`409 Conflict`. A request can't be marked `new` while it still has missing
fields. Every status change is written to a history table for auditing.

**Inputs are validated at the edge.** Empty text, invalid quantities, and
unknown statuses return `422`. A missing ID returns `404`.

---

## Evaluation

`eval/samples.jsonl` contains 24 hand-labeled requests that mirror real
messages: clean ones, emails with greetings and sign-offs, mixed units
("1,500 lbs", "650 ft"), and deliberately vague requests. "Today" is fixed in
the eval so relative dates like "by Friday" are reproducible.

| Extractor | All 5 fields correct | Absent fields correctly left blank |
|---|---|---|
| Rules | 15 / 24 (62%) | 25 / 26 |
| Claude | not yet measured | not yet measured |

The rules extractor struggles with phrasings that have no unit word
("6 replacement barcode scanners") or units it doesn't know ("2 pallets").
That gap is why the Claude path exists.

**A bug the eval caught:** in "We're out of die lubricant, need **it** today",
the word "it" was tagged as the **IT** department. The fix was to match "IT"
only in uppercase, and a regression test now covers it.

---

## How I used AI tools

I built this with **Claude Code** as a pair programmer.

- **Problem and requirements:** I picked the problem (request intake for a
  copper conductor manufacturer) and the requirements: extract the key fields,
  flag missing information instead of guessing, track status, and include
  tests and logging.
- **Scaffolding:** Claude Code generated the first version of the FastAPI app,
  the SQLite layer, the dashboard, and the test suite.
- **Evaluation first:** instead of trusting that the extraction "looked right",
  I added a labeled eval set. Running it exposed the "need it today" → IT
  department bug, which was fixed and covered with a regression test.
- **Testing it by hand:** I ran the app locally, submitted requests, and moved
  them through the full workflow. Testing the dashboard turned up a
  double-submit issue, so the form is now disabled while a request is in
  flight.
- **What I took away:** AI tools make it fast to get a working first version.
  The real work is deciding what "correct" means, measuring it, and handling
  the failure cases.

---

## Security notes

- **No secrets in the repo.** The API key is read from an environment variable,
  and `.env` files and the local database are excluded by `.gitignore`.
- **SQL injection:** every query is parameterized, with no SQL built from strings.
- **XSS:** user-supplied text is HTML-escaped before the dashboard displays it.
- **Input limits:** request text, item, unit, and department have maximum
  lengths, and quantity must be a finite positive number.
- **Error responses don't echo input.** A validation error says which field is
  wrong but doesn't repeat the submitted value.
- **Logs contain no request text.** Only IDs, status changes, and which fields
  are missing.
- **Prompt injection:** request text is sent to Claude as data, and the reply
  must match a strict schema of five fields. The worst a malicious request can
  do is fill its own fields with wrong values, which the workflow still
  routes for human approval.
- **Data leaving the building:** with an API key set, request text is sent to
  Anthropic's API. Without a key, nothing leaves the machine.
- **Not production-ready as is:** there is no login. The server only listens on
  `localhost` by default and shouldn't be exposed to a network without adding
  authentication.

## Limitations and next steps

- **Multi-item requests** ("200 m of 12 AWG and 50 m of 10 AWG") are treated as one item.
- **Units** are limited to a fixed list (m, ft, lb, kg, spool, reel, pcs, box).
- **No authentication.** It's meant as an internal prototype.

Next steps I'd consider:

- Pull requests from a shared Outlook inbox or SharePoint list through Microsoft Graph
- Match items against an ERP part-number catalog
- Notify requesters in Microsoft Teams when a request needs info or ships
- Measure the Claude extractor against the same eval set and compare the cost per request
