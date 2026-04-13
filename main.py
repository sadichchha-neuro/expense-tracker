"""
Expense Tracker - Streamlit Dashboard
"""

import streamlit as st
import pandas as pd
from datetime import datetime

import logic

# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
logic.init_db()

st.set_page_config(page_title="Expense Tracker", page_icon="💰", layout="wide")
st.title("Expense Tracker")

# ---------------------------------------------------------------------------
# Sidebar – Add Expense & Budget
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Add Expense")

    exp_date = st.date_input("Date", value=datetime.now().date())
    exp_category = st.selectbox("Category", logic.CATEGORIES)
    exp_description = st.text_input("Description")
    exp_amount = st.number_input("Amount (Rupees)", min_value=0.01, step=0.01, format="%.2f")

    # Hourly wage for "Hours of Life" calculator
    hourly_wage = st.number_input(
        "Your hourly wage (Rupees)",
        min_value=0.0,
        value=0.0,
        step=1.0,
        format="%.2f",
        help="Used to show how many work hours an expense costs you.",
    )

    # "Hours of Life Spent" warning (shows for expenses >= 1000)
    if hourly_wage > 0 and exp_amount > 0:
        life_cost = logic.calculate_hours_of_life(exp_amount, hourly_wage)
        if life_cost["exceeds_threshold"]:
            st.warning(f"**{life_cost['message']}**")
        elif life_cost["hours"] > 0:
            st.caption(life_cost["message"])

    # Impulse threshold setting
    impulse_threshold = st.number_input(
        "Impulse threshold ($)",
        min_value=1.0,
        value=logic.DEFAULT_IMPULSE_THRESHOLD,
        step=5.0,
        help="Purchases at or above this amount in impulse-prone categories will trigger a warning.",
    )

    add_clicked = st.button("Add Expense", type="primary", use_container_width=True)

    st.divider()
    st.header("Monthly Budget")
    current_budget = logic.get_monthly_budget()
    new_budget = st.number_input(
        "Set budget (Rupees)",
        min_value=0.0,
        value=current_budget if current_budget else 0.0,
        step=50.0,
        format="%.2f",
    )
    if st.button("Save Budget", use_container_width=True):
        logic.set_monthly_budget(new_budget)
        st.success(f"Budget set to Rupees{new_budget:,.2f}")
        st.rerun()

    # ---- Receipt OCR Upload -----------------------------------------------
    st.divider()
    st.header("Scan Receipt")
    uploaded_file = st.file_uploader(
        "Upload a receipt image",
        type=["png", "jpg", "jpeg"],
        help="Extract amount and merchant using OCR.",
    )
    if uploaded_file is not None:
        result = logic.parse_receipt_image(uploaded_file.getvalue())
        if result["error"]:
            st.error(result["error"])
        else:
            st.success("Receipt parsed!")
            if result["merchant"]:
                st.text_input("Detected merchant", value=result["merchant"], key="ocr_merchant", disabled=True)
            if result["amount"] is not None:
                st.text_input("Detected amount", value=f"Rupees{result['amount']:.2f}", key="ocr_amount", disabled=True)
            else:
                st.warning("Could not detect a total amount.")
            with st.expander("Raw OCR text"):
                st.code(result["raw_text"])

# ---------------------------------------------------------------------------
# Handle Add Expense (with Impulse Interceptor)
# ---------------------------------------------------------------------------
if add_clicked:
    if not exp_description.strip():
        st.sidebar.error("Please enter a description.")
    else:
        risk = logic.evaluate_impulse_risk(exp_amount, exp_category, impulse_threshold)

        # "Hours of Life" warning for large expenses
        if hourly_wage > 0:
            life_cost = logic.calculate_hours_of_life(exp_amount, hourly_wage)
            if life_cost["exceeds_threshold"]:
                st.sidebar.warning(f"**{life_cost['message']}**")

        if risk["is_risky"] and not st.session_state.get("impulse_confirmed"):
            st.session_state["pending_expense"] = {
                "date": exp_date.strftime("%Y-%m-%d"),
                "category": exp_category,
                "description": exp_description,
                "amount": exp_amount,
                "risk": risk,
            }
        else:
            logic.add_expense(
                exp_date.strftime("%Y-%m-%d"),
                exp_category,
                exp_description,
                exp_amount,
                is_impulse=risk["is_risky"],
            )
            st.session_state.pop("impulse_confirmed", None)
            st.sidebar.success(f"Added Rupees{exp_amount:,.2f} for '{exp_description}'")
            st.rerun()

# ---------------------------------------------------------------------------
# Impulse Purchase Interceptor modal
# ---------------------------------------------------------------------------
pending = st.session_state.get("pending_expense")
if pending:
    risk = pending["risk"]
    st.warning("**Impulse Purchase Interceptor**")
    col_warn1, col_warn2 = st.columns([2, 1])
    with col_warn1:
        st.markdown(f"**Risk Score:** {risk['risk_score']}/100")
        for r in risk["reasons"]:
            st.markdown(f"- {r}")
        st.info(risk["suggestion"])
    with col_warn2:
        st.metric("Amount", f"${pending['amount']:,.2f}")
        st.caption(f"{pending['category']}")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Add Anyway", type="primary", use_container_width=True):
            logic.add_expense(
                pending["date"],
                pending["category"],
                pending["description"],
                pending["amount"],
                is_impulse=True,
            )
            st.session_state.pop("pending_expense", None)
            st.success("Expense added (marked as impulse).")
            st.rerun()
    with col_b:
        if st.button("Cancel", use_container_width=True):
            st.session_state.pop("pending_expense", None)
            st.rerun()

# ---------------------------------------------------------------------------
# Dashboard Tabs
# ---------------------------------------------------------------------------
tab_overview, tab_burn, tab_impulse, tab_history = st.tabs(
    ["Overview", "Burn Rate", "Impulse Stats", "History"]
)

now = datetime.now()
current_year, current_month = now.year, now.month

# ---- Overview Tab --------------------------------------------------------
with tab_overview:
    month_df = logic.get_expenses_for_month(current_year, current_month)
    budget = logic.get_monthly_budget()

    total_spent = month_df["amount"].sum() if not month_df.empty else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Spent This Month", f"${total_spent:,.2f}")
    c2.metric("Budget", f"Rupees{budget:,.2f}" if budget else "Not set")
    if budget and budget > 0:
        remaining = budget - total_spent
        c3.metric("Remaining", f"Rupees{remaining:,.2f}", delta=f"{remaining / budget * 100:.0f}%")
    else:
        c3.metric("Remaining", "N/A")

    # Category breakdown chart
    cat_df = logic.get_category_breakdown(current_year, current_month)
    if not cat_df.empty:
        st.subheader("Spending by Category")
        st.bar_chart(cat_df.set_index("category")["amount"])
    else:
        st.info("No expenses recorded this month yet. Add one using the sidebar.")

# ---- Burn Rate Tab -------------------------------------------------------
with tab_burn:
    st.subheader("Burn Rate Forecaster")
    burn = logic.calculate_burn_rate()

    c1, c2, c3 = st.columns(3)
    c1.metric("Daily Burn Rate", f"Rupees{burn['daily_rate']:,.2f}")
    c2.metric("Projected Monthly Total", f"Rupees{burn['monthly_projection']:,.2f}")
    c3.metric(
        "Days Left in Month",
        burn["days_remaining"],
    )

    if burn["budget"]:
        st.divider()
        bc1, bc2, bc3 = st.columns(3)
        bc1.metric("Budget", f"Rupees{burn['budget']:,.2f}")
        bc2.metric(
            "Budget Remaining",
            f"Rupees{burn['budget_remaining']:,.2f}" if burn["budget_remaining"] is not None else "N/A",
        )
        burnout_label = burn["burnout_date"] if burn["burnout_date"] else "Within budget"
        status_icon = "On Track" if burn["on_track"] else "Over Budget"
        bc3.metric("Status", status_icon)

        if burn["burnout_date"] and burn["burnout_date"] != "Already exceeded":
            st.info(f"At your current rate, your budget will run out on **{burn['burnout_date']}**.")
        elif burn["burnout_date"] == "Already exceeded":
            st.error("You have already exceeded your monthly budget.")
        else:
            st.success("You're on track to stay within budget this month.")
    else:
        st.info("Set a monthly budget in the sidebar to see forecasting details.")

    # Weekly trend
    weekly = burn["weekly_rates"]
    if any(v > 0 for _, v in weekly):
        st.subheader("Weekly Spending Trend")
        trend_df = pd.DataFrame(weekly, columns=["Week", "Spent"])
        st.bar_chart(trend_df.set_index("Week"))

    # ---- 30-Day Balance Forecast Line Chart --------------------------------
    st.divider()
    st.subheader("30-Day Balance Forecast")
    forecast_balance = st.number_input(
        "Current account balance (Rupees)",
        min_value=0.0,
        value=5000.0,
        step=100.0,
        format="%.2f",
        key="forecast_balance",
    )
    forecast = logic.forecast_30_day_burn(forecast_balance)

    fc1, fc2, fc3 = st.columns(3)
    fc1.metric("Avg Daily Spend (30d)", f"Rupees{forecast['avg_daily_spend']:,.2f}")
    fc2.metric("Total Spent (30d)", f"Rupees{forecast['total_last_30']:,.2f}")
    fc3.metric("Projected EOM Balance", f"Rupees{forecast['projected_eom_balance']:,.2f}")

    if forecast["days_until_zero"] is not None:
        if forecast["projected_eom_balance"] < 0:
            st.error(
                f"At this rate your balance will hit zero in ~**{forecast['days_until_zero']}** days."
            )
        else:
            st.success("Your balance should remain positive through end of month.")

    proj_df = forecast["projection_df"]
    if not proj_df.empty:
        chart_df = proj_df.set_index("Date")[["Actual", "Projected"]]
        st.line_chart(chart_df)

# ---- Impulse Stats Tab ---------------------------------------------------
with tab_impulse:
    st.subheader("Impulse Purchase Interceptor - Stats")
    stats = logic.get_impulse_stats()

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Impulse Spending", f"Rupees{stats['total_impulse']:,.2f}")
    c2.metric("Impulse Purchases", stats["impulse_count"])
    c3.metric("Impulse % of Total", f"{stats['impulse_pct']:.1f}%")

    st.divider()
    st.markdown(
        """
**How the Impulse Interceptor works:**

When you add an expense, the system evaluates it against several risk factors:

| Factor | Weight |
|---|---|
| Amount exceeds your threshold | +35 |
| Category is impulse-prone (Shopping, Entertainment, Food & Dining) | +25 |
| Recent spending in the same category (last 7 days) | +20 |
| Late-night purchase (10 PM - 6 AM) | +20 |

A combined score of **40 or higher** triggers the interceptor, giving you a moment to reconsider.
        """
    )

# ---- History Tab ---------------------------------------------------------
with tab_history:
    st.subheader("Expense History")

    all_df = logic.get_all_expenses()

    if all_df.empty:
        st.info("No expenses recorded yet.")
    else:
        # Filters
        fc1, fc2 = st.columns(2)
        with fc1:
            filter_cat = st.multiselect("Filter by category", logic.CATEGORIES)
        with fc2:
            filter_impulse = st.selectbox("Impulse filter", ["All", "Impulse only", "Non-impulse only"])

        filtered = all_df.copy()
        if filter_cat:
            filtered = filtered[filtered["category"].isin(filter_cat)]
        if filter_impulse == "Impulse only":
            filtered = filtered[filtered["is_impulse"] == 1]
        elif filter_impulse == "Non-impulse only":
            filtered = filtered[filtered["is_impulse"] == 0]

        display_df = filtered.copy()
        display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d")
        display_df["impulse"] = display_df["is_impulse"].map({1: "Yes", 0: "No"})
        st.dataframe(
            display_df[["id", "date", "category", "description", "amount", "impulse"]],
            use_container_width=True,
            hide_index=True,
        )

        # Delete expense
        st.divider()
        del_id = st.number_input("Delete expense by ID", min_value=1, step=1)
        if st.button("Delete", type="secondary"):
            logic.delete_expense(int(del_id))
            st.success(f"Expense #{int(del_id)} deleted.")
            st.rerun()
        # To Change Titles and Text
        st.set_page_config(page_title="My Expense Tracker", layout="wide") # Makes the app use the whole screen
        st.title("💰 Smart Expense Dashboard")
        st.subheader("Your Financial Co-Pilot")
        # To Create Columns (Side-by-Side Display)
        col1, col2 = st.columns(2)
        with col1:
           st.metric(label="Spent this Month", value="₹12,400", delta="-₹500")
        with col2:
           st.metric(label="Predicted Burn Date", value="Oct 28th", delta="2 Days Early", delta_color="inverse")
        #To Add the "Impulse Interceptor" (Behavioral Psychology)
        with st.sidebar:
            st.header("Log New Expense")
            amount = st.number_input("Amount (₹)", min_value=0)
            hourly_wage = 500 # You can let the user set this in settings
    
        if amount > 1000:
            hours_needed = amount / hourly_wage
            st.warning(f"⚠️ This purchase costs **{hours_needed:.1f} hours** of your life. Is it worth it?")
       
