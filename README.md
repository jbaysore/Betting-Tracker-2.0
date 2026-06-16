# Betting Tracker

Automatically fetches and records closing odds for upcoming bets using the Odds API and writes them back to a Google Sheet. Runs on a 15-minute schedule via GitHub Actions.

## What It Does

- Reads open bets from a Google Sheet ("Bets" tab)
- Checks if any games are starting within the next 5 minutes
- If no games are found in that window, exits without making any API calls
- Fetches live closing odds from the [Odds API](https://the-odds-api.com) for those games, using the bookmaker specified per row
- Uses fuzzy name matching to handle minor discrepancies between team names in the sheet and the Odds API
- Writes the closing odds back to the `ClosingOdds` column in the sheet, or a failure flag if something goes wrong

## Google Sheet Structure

The script expects the following columns in the "Bets" tab:

| Column | Description |
|---|---|
| `Game Date` | Date of the game (`MM/DD/YYYY`) |
| `Game Start Time` | Start time in Chicago time (`HH:MM:SS AM/PM`) |
| `Sport` | Odds API sport key (e.g. `americanfootball_nfl`) |
| `Team 1` | Home team name (should match Odds API, fuzzy matched) |
| `Team 2` | Away team name (should match Odds API, fuzzy matched) |
| `Selection` | The team or outcome you bet on (fuzzy matched) |
| `Book` | Odds API bookmaker key (e.g. `draftkings`, `fanduel`) |
| `ClosingOdds` | Populated automatically by this script |

## Failure Flags

If something goes wrong, the script writes one of the following values to the `ClosingOdds` column instead of a number:

| Flag | Meaning |
|---|---|
| `NAME MISMATCH` | Team names in the sheet could not be matched to any event in the Odds API, even with fuzzy matching |
| `BOOK NOT FOUND` | The bookmaker specified in the `Book` column was not available for that event |
| `SELECTION NOT FOUND` | The selection could not be matched to any outcome for that event |

## Fuzzy Matching

Team names and selections are matched using fuzzy string comparison with an 85% confidence threshold. This means minor differences in formatting, abbreviations, or spelling between your sheet and the Odds API will still match correctly. If the confidence is below 85%, the row is flagged as `NAME MISMATCH` or `SELECTION NOT FOUND`.

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

The script runs automatically every 5 minutes via GitHub Actions. You can also trigger a manual run anytime from the **Actions** tab in GitHub by clicking **Run workflow**.

Note: GitHub Actions' minimum cron interval is 5 minutes, but runs may occasionally be delayed by a few minutes during high traffic periods.

## Files

```
betting-tracker/
├── main.py                        # Main script
├── requirements.txt               # Python dependencies
└── .github/
    └── workflows/
        └── run_script.yml         # GitHub Actions workflow
```
