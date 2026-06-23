# Betting Tracker
Automatically fetches and records closing odds for upcoming bets using the Odds API and writes them back to a Google Sheet. Runs on a 5-minute schedule via cron-job.org triggering GitHub Actions.

## What It Does
- Reads open bets from a Google Sheet ("Bets" tab)
- Checks if any games are starting within the next 7 minutes
- If no games are found in that window, exits without making any API calls
- Fetches live closing odds (moneyline, spread, and total markets) from the [Odds API](https://the-odds-api.com) for those games, using the bookmaker specified per row, requesting the Odds API region that bookmaker belongs to (see Book Regions below)
- Selects the correct market and parses the selection based on the row's `Bet Type` — Moneyline and Draw bets match against the moneyline market, Spread bets match against the spread market at the exact line, and Total bets match against the total market at the exact line and direction
- Uses fuzzy name matching to handle minor discrepancies between team names in the sheet and the Odds API (exact matching is still required for spread/total lines — see Bet Type Handling below)
- Writes the closing odds back to the `ClosingOdds` column in the sheet, or a failure flag if something goes wrong
- Skips rows with a `Bet Type` of `Prop`, `Parlay`, or any value outside the four supported types — these are intentionally left for manual closing odds entry

## Google Sheet Structure
The script expects the following columns in the "Bets" tab:

| Column | Description |
|---|---|
| `Game Date` | Date of the game (`MM/DD/YYYY`) |
| `Game Start Time` | Start time in Chicago time (`HH:MM:SS AM/PM`) |
| `Sport` | Odds API sport key (e.g. `americanfootball_nfl`) |
| `Team 1` | Home team name (should match Odds API, fuzzy matched) |
| `Team 2` | Away team name (should match Odds API, fuzzy matched) |
| `Selection` | The team or outcome you bet on — format depends on `Bet Type`, see below |
| `Bet Type` | One of `Moneyline`, `Spread`, `Total`, or `Draw`. Any other value (e.g. `Prop`, `Parlay`) is skipped |
| `Book` | Odds API bookmaker key (e.g. `draftkings`, `fanduel`) |
| `ClosingOdds` | Populated automatically by this script |

## Bet Type Handling

The `Selection` column's expected format depends on `Bet Type`:

| Bet Type | Selection format | Matching |
|---|---|---|
| `Moneyline` | Team name, e.g. `Kansas City Chiefs` | Fuzzy match (85% threshold) against the moneyline market |
| `Draw` | Not used — matches the `Draw` outcome directly | Fuzzy match against the moneyline market's `Draw` outcome |
| `Spread` | `Team -3.5` or `Team +7` | Fuzzy team name match, **plus an exact match on the point value**. If the line has moved since the bet was placed, no match will be found — this is intentional |
| `Total` | `Over 47.5` or `Under 47.5` | Exact match on direction and point value |

Spread and Total selections require an exact line match by design, consistent with how the companion Closing Odds Backfill Tool handles the same case. A bet logged at one line will not be matched against a different line at game time even if the team or direction is otherwise correct.

## Failure Flags
If something goes wrong, the script writes one of the following values to the `ClosingOdds` column instead of a number:

| Flag | Meaning |
|---|---|
| `BOOK NOT FOUND` | The bookmaker specified in the `Book` column was not available for that event |
| `SELECTION NOT FOUND` | The selection could not be matched to any outcome for that event — including spread/total selections that couldn't be parsed, or whose line no longer matches what the market is currently offering |

### No event match — `ClosingOdds` left blank, no flag written

If the team names in the sheet can't be matched (even fuzzily) to any event in the Odds API's live odds feed, `ClosingOdds` is **left blank** rather than flagged. This case used to write `NAME MISMATCH`, but that flag was misleading: a missing event in this feed is genuinely ambiguous between two very different situations —

1. The game was **voided or postponed** and dropped out of the live odds feed entirely (books pull lines once a game won't be played), or
2. The team names really don't match well enough — a real formatting issue worth investigating.

This script has no way to distinguish the two from the live odds feed alone, and previously labeling both cases `NAME MISMATCH` falsely implied case 2 every time, even when the game was simply voided. Writing nothing is more honest, and it has two practical benefits:

- A blank `ClosingOdds` is exactly what the **Historical Odds Backfill Tool** (in the main app's Bets page) looks for — running it on demand re-attempts the match and reports a specific, current reason if it still fails, which is a better diagnostic than a stale flag written once at game time.
- If the bet later resolves to `Result = VOID` (written by the separate Bet Result Automation Tool, hours after game time once it confirms the game was cancelled), that confirms retroactively that the blank `ClosingOdds` was correct and expected — no further action needed. If `Result` instead settles to `WIN`/`LOSS`/`PUSH`, a blank `ClosingOdds` on that row is worth investigating as a genuine name-matching issue.

This script only ever evaluates a given row once, in the 7-minute window before its game starts — it does not retry blank rows on later runs. Resolving the ambiguity is the Backfill Tool's job, not this script's.

## Book Regions
The Odds API splits bookmakers across regions, and a book only appears in the response for its own region. The script looks up each row's `Book` key against three groups before requesting odds:

| Region | Books |
|---|---|
| `us_ex` | `polymarket`, `kalshi`, `novig`, `betopenly`, `prophetx` |
| `us2` | `ballybet`, `betanysports`, `betparx`, `espnbet`, `fliff`, `hardrockbet`, `hardrockbet_az`, `hardrockbet_fl`, `hardrockbet_oh`, `rebet` |
| `us` | everything else |

This list is kept in sync with `bookConstants.js` in the companion odds-tool app — if a book is added there, add it here too, or this script will wrongly write `BOOK NOT FOUND` for bets on that book.

## Fuzzy Matching
Team names (for Moneyline, Draw, and the team portion of Spread selections) are matched using fuzzy string comparison with an 85% confidence threshold. This means minor differences in formatting, abbreviations, or spelling between your sheet and the Odds API will still match correctly. If the confidence is below 85%, the row is left blank (no event match) or flagged `SELECTION NOT FOUND`, depending on which stage failed — see Failure Flags above.

Spread and Total point values are matched exactly, not fuzzily — see Bet Type Handling above.

## Setup

### 1. Google Sheets API
- Create a Google Cloud service account with access to the Google Sheets API
- Share your spreadsheet with the service account email
- Download the `service_account.json` credentials file

### 2. Odds API
- Sign up at [the-odds-api.com](https://the-odds-api.com) to get an API key

### 3. GitHub Secrets
Go to your repo → **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|---|---|
| `SERVICE_ACCOUNT_JSON` | Full contents of your `service_account.json` file |
| `ODDS_API_KEY` | Your Odds API key |

## Running Locally
Install dependencies:
```bash
pip install -r requirements.txt
```

Add your `service_account.json` to the project root and set your API key as an environment variable:
```bash
export ODDS_API_KEY=your_key_here
python main.py
```

## Scheduling
The workflow is triggered every 5 minutes by [cron-job.org](https://cron-job.org) via a `workflow_dispatch` webhook call to the GitHub API. GitHub's native cron scheduling was replaced with this approach due to reliability issues — GitHub may delay or skip scheduled runs during high traffic periods, which would result in missing closing odds. cron-job.org guarantees execution at the specified interval.

You can also trigger a manual run anytime from the **Actions** tab in GitHub by clicking **Run workflow**.

> **Note:** If you fork or clone this repo, the cron-job.org job is not included. You will need to recreate it pointing at your own repo using a personal access token with Actions read/write permission.

> **Note on API usage:** Each triggered run (i.e. one where a game is starting within the 7-minute window) now requests all three markets (`h2h,spreads,totals`) in a single call, which costs more Odds API credits per call than the moneyline-only version. Idle 5-minute checks where no game is in the window still make no API calls and cost nothing.

## Files
```
betting-tracker/
├── main.py                        # Main script
├── requirements.txt               # Python dependencies
└── .github/
    └── workflows/
        └── run_script.yml         # GitHub Actions workflow (triggered via workflow_dispatch)
```
