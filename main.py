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
bet_type_col = header.index("Bet Type")
closing_odds_col = header.index("ClosingOdds")
book_col = header.index("Book")
decimal_closing_odds_col = header.index("DecimalClosingOdds")
clv_col = header.index("CLV")
odds_taken_col = header.index("OddsTaken")

# --- Timezone setup ---
local_tz = pytz.timezone("America/Chicago")
utc = pytz.utc

utc_now = datetime.now(pytz.utc)
window = timedelta(minutes=7)

# --- Odds API setup ---
ODDS_API_KEY = os.environ["ODDS_API_KEY"]

# --- Book region routing ---
# Mirrors bookConstants.js's regionForBookKey in odds-tool. The Odds API splits
# bookmakers across regions, and requesting the wrong region for a given book
# means that book is simply absent from the response — this previously caused
# main.py to wrongly write "BOOK NOT FOUND" for every bet on these books.
EXCHANGE_KEYS = {"polymarket", "kalshi", "novig", "betopenly", "prophetx"}
US2_KEYS = {
    "ballybet", "betanysports", "betparx", "espnbet", "fliff",
    "hardrockbet", "hardrockbet_az", "hardrockbet_fl",
    "hardrockbet_oh", "rebet",
}

def region_for_book_key(book_key):
    if book_key in EXCHANGE_KEYS:
        return "us_ex"
    if book_key in US2_KEYS:
        return "us2"
    return "us"

# --- Decimal odds / CLV ---
# DecimalClosingOdds and CLV used to be a dragged-down Sheets formula, which
# silently stopped calculating on new rows whenever dragging it down was
# forgotten. Computing both here means every row this script resolves gets
# them for free, with no manual sheet upkeep required.
def to_decimal_odds(american_odds):
    if american_odds is None:
        return None
    try:
        american_odds = float(american_odds)
    except (TypeError, ValueError):
        return None
    if american_odds > 0:
        return 1 + american_odds / 100
    return 1 + 100 / abs(american_odds)

def calc_clv(decimal_odds_taken, decimal_closing_odds):
    if not decimal_odds_taken or not decimal_closing_odds:
        return None
    return round((decimal_odds_taken / decimal_closing_odds - 1) * 100, 2)

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

def fuzzy_match_team(outcomes, team_name):
    """For h2h/spreads — matches outcome['name'] against a team name."""
    best_outcome = None
    best_score = 0

    for o in outcomes:
        score = fuzz.ratio(o["name"], team_name)
        if score > best_score:
            best_score = score
            best_outcome = o

    if best_score >= MATCH_THRESHOLD:
        return best_outcome, best_score
    return None, best_score

def get_market_outcomes(markets, market_key):
    """Pulls the outcomes list for a specific market key (h2h, spreads, totals)
    out of a bookmaker's markets array."""
    market = next((m for m in markets if m["key"] == market_key), None)
    return market["outcomes"] if market else []

def parse_spread_selection(selection):
    """'Chiefs -3.5' -> ('Chiefs', -3.5). Returns (None, None) if unparseable."""
    parts = selection.strip().rsplit(" ", 1)
    if len(parts) != 2:
        return None, None
    try:
        return parts[0].strip(), float(parts[1].strip())
    except ValueError:
        return None, None

def parse_total_selection(selection):
    """'Over 47.5' -> ('Over', 47.5). Returns (None, None) if unparseable."""
    parts = selection.strip().split(" ", 1)
    if len(parts) != 2:
        return None, None
    direction = parts[0].strip().capitalize()
    try:
        return direction, float(parts[1].strip())
    except ValueError:
        return None, None

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
            bet_type = row[bet_type_col].strip()
            book = row[book_col].strip()
            sheet_col = closing_odds_col + 1

            if bet_type not in ("Moneyline", "Spread", "Total", "Draw"):
                print(f"Skipping unsupported bet type for closing odds: '{bet_type}'")
                continue

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

            # NOTE 2026-06-20: oddsFormat=american is REQUIRED here. The Odds
            # API defaults to DECIMAL odds when this parameter is omitted
            # (confirmed directly from their V4 docs and error-code
            # reference), and every other odds-fetching call in this project
            # (backfillClosingOdds.js, odds_api.py) already sets this
            # explicitly. This call was the one place that didn't, which is
            # why ClosingOdds started showing decimal-shaped values like
            # 1.13/1.45/1.4/3.15 instead of American odds like -200/118/-453
            # for every other column in the same sheet.
            events = api_call_with_retry(
                f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
                {"apiKey": ODDS_API_KEY, "regions": region_for_book_key(book), "markets": "h2h,spreads,totals", "oddsFormat": "american"}
            )
            if events is None:
                print(f"Could not fetch odds for {sport_key}, skipping row")
                continue

            matched_event, match_score = fuzzy_match_event(events, team1, team2)

            if not matched_event:
                print(f"No match found for {team1} vs {team2} (best score: {match_score}) — "
                      f"leaving ClosingOdds blank for manual review via the Backfill Tool")
                continue

            print(f"Matched event with score {match_score}: {matched_event['home_team']} vs {matched_event['away_team']}")

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

            matched_outcome = None
            selection_score = 0

            if bet_type == "Moneyline":
                outcomes = get_market_outcomes(markets, "h2h")
                matched_outcome, selection_score = fuzzy_match_team(outcomes, selection)

            elif bet_type == "Draw":
                outcomes = get_market_outcomes(markets, "h2h")
                matched_outcome, selection_score = fuzzy_match_team(outcomes, "Draw")

            elif bet_type == "Spread":
                team_name, line = parse_spread_selection(selection)
                if team_name is None:
                    print(f"Could not parse spread selection: '{selection}'")
                    write_to_sheet(i, sheet_col, "SELECTION NOT FOUND")
                    continue
                outcomes = get_market_outcomes(markets, "spreads")
                candidate, score = fuzzy_match_team(outcomes, team_name)
                if candidate and candidate.get("point") == line and score >= MATCH_THRESHOLD:
                    matched_outcome, selection_score = candidate, score

            elif bet_type == "Total":
                direction, line = parse_total_selection(selection)
                if direction is None:
                    print(f"Could not parse total selection: '{selection}'")
                    write_to_sheet(i, sheet_col, "SELECTION NOT FOUND")
                    continue
                outcomes = get_market_outcomes(markets, "totals")
                candidate = next(
                    (o for o in outcomes if o["name"] == direction and o.get("point") == line),
                    None
                )
                if candidate:
                    matched_outcome, selection_score = candidate, 100

            if not matched_outcome:
                print(f"Selection '{selection}' not found in outcomes for bet type '{bet_type}' (best score: {selection_score})")
                write_to_sheet(i, sheet_col, "SELECTION NOT FOUND")
                continue

            print(f"Matched selection '{matched_outcome['name']}' ({bet_type}) with score {selection_score}")
            closing_price = matched_outcome["price"]
            write_to_sheet(i, sheet_col, closing_price)
            print(f"Wrote closing odds {closing_price} to row {i}")

            decimal_closing = to_decimal_odds(closing_price)
            if decimal_closing is not None:
                write_to_sheet(i, decimal_closing_odds_col + 1, decimal_closing)

                odds_taken_raw = row[odds_taken_col].strip() if len(row) > odds_taken_col else ""
                decimal_taken = to_decimal_odds(odds_taken_raw) if odds_taken_raw else None
                clv = calc_clv(decimal_taken, decimal_closing)
                if clv is not None:
                    write_to_sheet(i, clv_col + 1, clv)

    except Exception as e:
        print("Error parsing:", combined, "|", e)
