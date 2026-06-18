import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
import pytz
import requests
import os
import time
from rapidfuzz import fuzz

# --- Google Sheets Auth ---
SERVICE_ACCOUNT_FILE = "service_account.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

creds = Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=SCOPES
)

client = gspread.authorize(creds)

# --- Open Spreadsheet ---
SHEET_ID = os.environ["SHEET_ID"]
spreadsheet = client.open_by_key(SHEET_ID)

sheet = spreadsheet.worksheet("Bets")

rows = sheet.get_all_values()
header = rows[0]

print("Total rows:", len(rows))
print("Header:", header)

# --- Locate columns ---
date_col = header.index("Game Date")
time_col = header.index("Game Start Time")
sport_col = header.index("Sport")
team1_col = header.index("Team 1")
team2_col = header.index("Team 2")
selection_col = header.index("Selection")
closing_odds_col = header.index("ClosingOdds")
book_col = header.index("Book")

# --- Timezone setup ---
local_tz = pytz.timezone("America/Chicago")
utc = pytz.utc

utc_now = datetime.now(pytz.utc)
window = timedelta(minutes=7)

# --- Odds API setup ---
ODDS_API_KEY = os.environ["ODDS_API_KEY"]

# --- Retry helper ---
def api_call_with_retry(url, params, retries=3, delay=5):
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"API call failed (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return None

# --- Fuzzy match helper ---
MATCH_THRESHOLD = 85

def fuzzy_match_event(events, team1, team2):
    best_event = None
    best_score = 0

    for event in events:
        home = event["home_team"]
        away = event["away_team"]

        score_a = min(fuzz.ratio(home, team1), fuzz.ratio(away, team2))
        score_b = min(fuzz.ratio(home, team2), fuzz.ratio(away, team1))
        score = max(score_a, score_b)

        if score > best_score:
            best_score = score
            best_event = event

    if best_score >= MATCH_THRESHOLD:
        return best_event, best_score
    return None, best_score

def fuzzy_match_selection(outcomes, selection):
    best_outcome = None
    best_score = 0

    for o in outcomes:
        score = fuzz.ratio(o["name"], selection)
        if score > best_score:
            best_score = score
            best_outcome = o

    if best_score >= MATCH_THRESHOLD:
        return best_outcome, best_score
    return None, best_score

# --- Sheet write with retry ---
def write_to_sheet(row_index, col_index, value, retries=3, delay=5):
    for attempt in range(retries):
        try:
            sheet.update_cell(row_index, col_index, value)
            return True
        except Exception as e:
            print(f"Sheet write failed (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    print(f"Failed to write '{value}' to row {row_index} after {retries} attempts")
    return False

# --- Parse each row ---
for i, row in enumerate(rows[1:], start=2):
    game_date_str = row[date_col].strip()
    game_time_str = row[time_col].strip()

    if not game_date_str or not game_time_str:
        continue

    combined = f"{game_date_str} {game_time_str}"

    try:
        game_dt = datetime.strptime(combined, "%m/%d/%Y %I:%M:%S %p")

        local_dt = local_tz.localize(game_dt)
        utc_dt = local_dt.astimezone(utc)

        time_until = utc_dt - utc_now

        if timedelta(0) <= time_until <= window:
            print("Game starting soon:", row)
            print("Starts in:", time_until)

            sport_key = row[sport_col].strip()
            team1 = row[team1_col].strip()
            team2 = row[team2_col].strip()
            selection = row[selection_col].strip()
            book = row[book_col].strip()
            sheet_col = closing_odds_col + 1

            # --- Fetch supported sports (only when needed) ---
            supported_sports = api_call_with_retry(
                "https://api.the-odds-api.com/v4/sports",
                {"apiKey": ODDS_API_KEY}
            )
            if supported_sports is None:
                print("Could not fetch supported sports, skipping row")
                continue

            supported_keys = {sport["key"] for sport in supported_sports}

            if sport_key not in supported_keys:
                print(f"Skipping unsupported sport: {sport_key}")
                continue

            # --- Fetch odds ---
            events = api_call_with_retry(
                f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                {"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h"}
            )
            if events is None:
                print(f"Could not fetch odds for {sport_key}, skipping row")
                continue

            # --- Fuzzy match event ---
            matched_event, match_score = fuzzy_match_event(events, team1, team2)

            if not matched_event:
                print(f"No match found for {team1} vs {team2} (best score: {match_score})")
                write_to_sheet(i, sheet_col, "NAME MISMATCH")
                continue

            print(f"Matched event with score {match_score}: {matched_event['home_team']} vs {matched_event['away_team']}")

            # --- Find specified bookmaker ---
            bookmakers = matched_event.get("bookmakers", [])
            if not bookmakers:
                print("No bookmakers available for this event")
                write_to_sheet(i, sheet_col, "BOOK NOT FOUND")
                continue

            matched_book = next((b for b in bookmakers if b["key"] == book), None)

            if not matched_book:
                print(f"Book '{book}' not found for this event")
                write_to_sheet(i, sheet_col, "BOOK NOT FOUND")
                continue

            markets = matched_book.get("markets", [])
            if not markets:
                print("No markets available for this book")
                write_to_sheet(i, sheet_col, "BOOK NOT FOUND")
                continue

            outcomes = markets[0].get("outcomes", [])

            # --- Fuzzy match selection ---
            matched_outcome, selection_score = fuzzy_match_selection(outcomes, selection)

            if not matched_outcome:
                print(f"Selection '{selection}' not found in outcomes (best score: {selection_score})")
                write_to_sheet(i, sheet_col, "SELECTION NOT FOUND")
                continue

            print(f"Matched selection '{matched_outcome['name']}' with score {selection_score}")
            write_to_sheet(i, sheet_col, matched_outcome["price"])
            print(f"Wrote closing odds {matched_outcome['price']} to row {i}")

    except Exception as e:
        print("Error parsing:", combined, "|", e)
