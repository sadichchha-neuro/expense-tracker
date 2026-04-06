"""
Expense Tracker - Business Logic Module
Handles database operations, impulse purchase detection, and burn rate forecasting.
"""

import sqlite3
import os
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expenses.db")

CATEGORIES = [
    "Food & Dining",
    "Transportation",
    "Shopping",
    "Entertainment",
    "Bills & Utilities",
    "Health",
    "Education",
    "Travel",
    "Subscriptions",
    "Other",
]

# Categories typically associated with impulse purchases
IMPULSE_CATEGORIES = {"Shopping", "Entertainment", "Food & Dining"}

# Default impulse threshold (can be overridden in the UI)
DEFAULT_IMPULSE_THRESHOLD = 50.0

# Cooling-off period in minutes for impulse interceptor
COOLING_OFF_MINUTES = 5


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            is_impulse INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS monthly_budget (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            amount REAL NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

def add_expense(
    date: str,
    category: str,
    description: str,
    amount: float,
    is_impulse: bool = False,
) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO expenses (date, category, description, amount, is_impulse, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (date, category, description, round(amount, 2), int(is_impulse), datetime.now().isoformat()),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def delete_expense(expense_id: int) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()


def get_all_expenses() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT id, date, category, description, amount, is_impulse FROM expenses ORDER BY date DESC",
        conn,
    )
    conn.close()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def get_expenses_for_month(year: int, month: int) -> pd.DataFrame:
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1}-01-01"
    else:
        end = f"{year}-{month + 1:02d}-01"
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT id, date, category, description, amount, is_impulse "
        "FROM expenses WHERE date >= ? AND date < ? ORDER BY date DESC",
        conn,
        params=(start, end),
    )
    conn.close()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


# ---------------------------------------------------------------------------
# Budget helpers
# ---------------------------------------------------------------------------

def set_monthly_budget(amount: float) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO monthly_budget (id, amount) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET amount = excluded.amount",
        (round(amount, 2),),
    )
    conn.commit()
    conn.close()


def get_monthly_budget() -> Optional[float]:
    conn = get_connection()
    row = conn.execute("SELECT amount FROM monthly_budget WHERE id = 1").fetchone()
    conn.close()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Impulse Purchase Interceptor
# ---------------------------------------------------------------------------

def evaluate_impulse_risk(amount: float, category: str, threshold: float = DEFAULT_IMPULSE_THRESHOLD) -> dict:
    """Evaluate whether a purchase is likely an impulse buy.

    Returns a dict with:
        is_risky: bool
        risk_score: int (0-100)
        reasons: list[str]
        suggestion: str
    """
    risk_score = 0
    reasons = []

    # Factor 1: amount exceeds personal threshold
    if amount >= threshold:
        risk_score += 35
        reasons.append(f"Amount (${amount:.2f}) exceeds your impulse threshold (${threshold:.2f})")

    # Factor 2: category is impulse-prone
    if category in IMPULSE_CATEGORIES:
        risk_score += 25
        reasons.append(f"'{category}' is a common impulse-spend category")

    # Factor 3: check recent spending in same category (last 7 days)
    recent = _recent_category_spend(category, days=7)
    if recent > 0:
        risk_score += 20
        reasons.append(f"You already spent ${recent:.2f} on '{category}' in the last 7 days")

    # Factor 4: time of day (late-night purchases are riskier)
    hour = datetime.now().hour
    if hour >= 22 or hour < 6:
        risk_score += 20
        reasons.append("Late-night purchases are more likely to be impulsive")

    risk_score = min(risk_score, 100)
    is_risky = risk_score >= 40

    if is_risky:
        suggestion = (
            f"Consider waiting {COOLING_OFF_MINUTES} minutes before buying. "
            "Ask yourself: 'Would I still want this tomorrow?'"
        )
    else:
        suggestion = "This looks like a planned purchase. Go ahead!"

    return {
        "is_risky": is_risky,
        "risk_score": risk_score,
        "reasons": reasons,
        "suggestion": suggestion,
    }


def _recent_category_spend(category: str, days: int = 7) -> float:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE category = ? AND date >= ?",
        (category, cutoff),
    ).fetchone()
    conn.close()
    return row[0]


def get_impulse_stats() -> dict:
    """Return aggregate impulse purchase statistics."""
    conn = get_connection()
    total_impulse = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE is_impulse = 1"
    ).fetchone()[0]
    total_all = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses"
    ).fetchone()[0]
    impulse_count = conn.execute(
        "SELECT COUNT(*) FROM expenses WHERE is_impulse = 1"
    ).fetchone()[0]
    conn.close()

    return {
        "total_impulse": total_impulse,
        "total_all": total_all,
        "impulse_pct": (total_impulse / total_all * 100) if total_all > 0 else 0,
        "impulse_count": impulse_count,
    }


# ---------------------------------------------------------------------------
# Burn Rate Forecaster
# ---------------------------------------------------------------------------

def calculate_burn_rate(budget: Optional[float] = None) -> dict:
    """Calculate daily burn rate and forecast when budget runs out.

    Returns a dict with:
        daily_rate: average daily spending
        monthly_projection: projected monthly total
        days_data_span: number of days of data
        budget: the monthly budget (if set)
        budget_remaining: remaining budget this month
        days_remaining_in_month: calendar days left
        burnout_date: estimated date budget runs out (or None)
        on_track: whether spending is within budget
        weekly_rates: list of (week_label, rate) for trend chart
    """
    today = datetime.now().date()
    year, month = today.year, today.month

    df = get_expenses_for_month(year, month)

    if budget is None:
        budget = get_monthly_budget()

    # Days elapsed in the current month (at least 1)
    days_elapsed = max(today.day, 1)

    # Total spent this month
    total_spent = df["amount"].sum() if not df.empty else 0.0
    daily_rate = total_spent / days_elapsed

    # Days remaining
    if month == 12:
        next_month = datetime(year + 1, 1, 1).date()
    else:
        next_month = datetime(year, month + 1, 1).date()
    days_in_month = (next_month - datetime(year, month, 1).date()).days
    days_remaining = (next_month - today).days

    monthly_projection = daily_rate * days_in_month

    result = {
        "daily_rate": round(daily_rate, 2),
        "monthly_projection": round(monthly_projection, 2),
        "total_spent": round(total_spent, 2),
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "days_remaining": days_remaining,
        "budget": budget,
        "budget_remaining": None,
        "burnout_date": None,
        "on_track": True,
    }

    if budget and budget > 0:
        remaining = budget - total_spent
        result["budget_remaining"] = round(remaining, 2)
        result["on_track"] = monthly_projection <= budget

        if daily_rate > 0 and remaining > 0:
            days_until_burnout = remaining / daily_rate
            burnout_date = today + timedelta(days=int(days_until_burnout))
            result["burnout_date"] = burnout_date.strftime("%Y-%m-%d")
        elif remaining <= 0:
            result["burnout_date"] = "Already exceeded"

    # Weekly trend (last 4 weeks)
    result["weekly_rates"] = _weekly_spending_trend()

    return result


def _weekly_spending_trend(weeks: int = 4) -> list[tuple[str, float]]:
    """Return (week_label, total_spent) for the last N weeks."""
    today = datetime.now().date()
    rates = []
    for i in range(weeks - 1, -1, -1):
        week_end = today - timedelta(weeks=i)
        week_start = week_end - timedelta(days=6)
        label = f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}"
        conn = get_connection()
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE date >= ? AND date <= ?",
            (week_start.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d")),
        ).fetchone()
        conn.close()
        rates.append((label, row[0]))
    return rates


def get_category_breakdown(year: int, month: int) -> pd.DataFrame:
    df = get_expenses_for_month(year, month)
    if df.empty:
        return pd.DataFrame(columns=["category", "amount"])
    return df.groupby("category")["amount"].sum().reset_index().sort_values("amount", ascending=False)


# ---------------------------------------------------------------------------
# "Hours of Life Spent" Calculator
# ---------------------------------------------------------------------------

WORK_HOUR_THRESHOLD = 1000  # rupees

def calculate_hours_of_life(amount: float, hourly_wage: float) -> dict:
    """Convert an expense amount into hours of work needed to earn it.

    Returns a dict with:
        hours: float - hours of work
        minutes: int - leftover minutes after whole hours
        exceeds_threshold: bool - True if amount >= WORK_HOUR_THRESHOLD
        message: str - human-readable warning
    """
    if hourly_wage <= 0:
        return {
            "hours": 0,
            "minutes": 0,
            "exceeds_threshold": False,
            "message": "Set your hourly wage to see this metric.",
        }

    total_hours = amount / hourly_wage
    whole_hours = int(total_hours)
    leftover_minutes = int((total_hours - whole_hours) * 60)
    exceeds = amount >= WORK_HOUR_THRESHOLD

    if exceeds:
        msg = (
            f"This costs you {whole_hours}h {leftover_minutes}m of your life. "
            f"That's {total_hours:.1f} working hours to earn back!"
        )
    else:
        msg = f"This equals {whole_hours}h {leftover_minutes}m of work."

    return {
        "hours": round(total_hours, 2),
        "minutes": leftover_minutes,
        "exceeds_threshold": exceeds,
        "message": msg,
    }


# ---------------------------------------------------------------------------
# 30-Day Burn Rate Forecast (with daily projection data for line chart)
# ---------------------------------------------------------------------------

def forecast_30_day_burn(current_balance: float) -> dict:
    """Calculate average daily spend over the last 30 days and project balance
    forward to end of month.

    Returns a dict with:
        avg_daily_spend: float
        total_last_30: float
        days_with_data: int
        projection_df: pd.DataFrame with columns ['Date', 'Actual', 'Projected']
            suitable for a Streamlit line chart
        projected_eom_balance: float - estimated balance at end of month
        days_until_zero: int or None - days until balance hits zero
    """
    today = datetime.now().date()
    cutoff = today - timedelta(days=30)

    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT date, amount FROM expenses WHERE date >= ? ORDER BY date ASC",
        conn,
        params=(cutoff.strftime("%Y-%m-%d"),),
    )
    conn.close()

    if df.empty:
        # No data — return empty projection
        return {
            "avg_daily_spend": 0.0,
            "total_last_30": 0.0,
            "days_with_data": 0,
            "projection_df": pd.DataFrame(columns=["Date", "Actual", "Projected"]),
            "projected_eom_balance": current_balance,
            "days_until_zero": None,
        }

    df["date"] = pd.to_datetime(df["date"]).dt.date

    # Daily totals for actual data
    daily = df.groupby("date")["amount"].sum().reset_index()
    daily.columns = ["date", "spent"]

    # Fill missing days with 0
    all_days = pd.date_range(cutoff, today, freq="D").date
    full = pd.DataFrame({"date": all_days})
    full = full.merge(daily, on="date", how="left").fillna(0)

    avg_daily = full["spent"].mean()
    total_30 = full["spent"].sum()
    days_with_data = int((full["spent"] > 0).sum())

    # Build projection from first data date through end of month
    year, month = today.year, today.month
    if month == 12:
        eom = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        eom = datetime(year, month + 1, 1).date() - timedelta(days=1)

    # Actual cumulative spending (running balance deduction)
    actual_dates = full["date"].tolist()
    actual_cumulative = full["spent"].cumsum().tolist()
    actual_balance = [round(current_balance - c, 2) for c in actual_cumulative]

    # Projected dates (from tomorrow to end of month)
    proj_dates = []
    proj_balance = []
    last_balance = actual_balance[-1] if actual_balance else current_balance
    d = today + timedelta(days=1)
    running = last_balance
    while d <= eom:
        running -= avg_daily
        proj_dates.append(d)
        proj_balance.append(round(running, 2))
        d += timedelta(days=1)

    # Combine into a single DataFrame for charting
    rows = []
    for dt, bal in zip(actual_dates, actual_balance):
        rows.append({"Date": pd.Timestamp(dt), "Actual": bal, "Projected": None})
    # Bridge point: last actual day also starts projection
    if actual_dates:
        rows.append({"Date": pd.Timestamp(actual_dates[-1]), "Actual": None, "Projected": actual_balance[-1]})
    for dt, bal in zip(proj_dates, proj_balance):
        rows.append({"Date": pd.Timestamp(dt), "Actual": None, "Projected": bal})

    projection_df = pd.DataFrame(rows)

    # Days until zero
    days_until_zero = None
    if avg_daily > 0 and last_balance > 0:
        days_until_zero = int(last_balance / avg_daily)

    projected_eom = last_balance - avg_daily * len(proj_dates) if proj_dates else last_balance

    return {
        "avg_daily_spend": round(avg_daily, 2),
        "total_last_30": round(total_30, 2),
        "days_with_data": days_with_data,
        "projection_df": projection_df,
        "projected_eom_balance": round(projected_eom, 2),
        "days_until_zero": days_until_zero,
    }


# ---------------------------------------------------------------------------
# Receipt OCR (pytesseract)
# ---------------------------------------------------------------------------

def parse_receipt_image(image_bytes: bytes) -> dict:
    """Extract total amount and merchant name from a receipt image.

    Returns a dict with:
        merchant: str or None
        amount: float or None
        raw_text: str - the full OCR output
        error: str or None
    """
    try:
        import pytesseract
        from PIL import Image
        import io
        import re
    except ImportError as e:
        return {
            "merchant": None,
            "amount": None,
            "raw_text": "",
            "error": f"Missing dependency: {e}. Install with: pip install pytesseract Pillow",
        }

    try:
        img = Image.open(io.BytesIO(image_bytes))
        raw = pytesseract.image_to_string(img)
    except Exception as e:
        return {
            "merchant": None,
            "amount": None,
            "raw_text": "",
            "error": f"OCR failed: {e}. Make sure tesseract is installed (apt install tesseract-ocr).",
        }

    lines = [l.strip() for l in raw.splitlines() if l.strip()]

    # Merchant: usually the first non-empty line
    merchant = lines[0] if lines else None

    # Amount: look for common total patterns
    amount = None
    total_patterns = [
        r"(?i)(?:total|grand\s*total|amount\s*due|net\s*total|balance)\s*[:\-]?\s*[\$₹]?\s*([\d,]+\.?\d*)",
        r"[\$₹]\s*([\d,]+\.\d{2})",
        r"([\d,]+\.\d{2})\s*$",
    ]
    for pattern in total_patterns:
        for line in reversed(lines):  # totals are usually near the bottom
            m = re.search(pattern, line)
            if m:
                try:
                    amount = float(m.group(1).replace(",", ""))
                    break
                except ValueError:
                    continue
        if amount is not None:
            break

    return {
        "merchant": merchant,
        "amount": amount,
        "raw_text": raw,
        "error": None,
    }
