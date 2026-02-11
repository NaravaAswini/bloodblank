from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
from datetime import datetime, date
import pandas as pd
from sklearn.linear_model import LinearRegression

app = Flask(__name__)
app.secret_key = "change_this_secret_key"
DB = "bloodbank.db"

BLOOD_GROUPS = ["A+","A-","B+","B-","O+","O-","AB+","AB-"]

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()

    # Users (admin/hospital)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
      user_id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('admin','hospital'))
    );
    """)

    # Donors
    cur.execute("""
    CREATE TABLE IF NOT EXISTS donors (
      donor_id INTEGER PRIMARY KEY AUTOINCREMENT,
      full_name TEXT NOT NULL,
      blood_group TEXT NOT NULL,
      gender TEXT,
      age INTEGER,
      phone TEXT NOT NULL,
      email TEXT,
      city TEXT,
      last_donation_date TEXT,
      created_at TEXT NOT NULL
    );
    """)

    # Blood stock
    cur.execute("""
    CREATE TABLE IF NOT EXISTS blood_stock (
      stock_id INTEGER PRIMARY KEY AUTOINCREMENT,
      blood_group TEXT NOT NULL,
      units INTEGER NOT NULL,
      collected_date TEXT NOT NULL,
      expiry_date TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    """)

    # Hospital requests
    cur.execute("""
    CREATE TABLE IF NOT EXISTS requests (
      request_id INTEGER PRIMARY KEY AUTOINCREMENT,
      hospital_name TEXT NOT NULL,
      blood_group TEXT NOT NULL,
      units INTEGER NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED')),
      created_at TEXT NOT NULL
    );
    """)

    # Usage history for prediction (monthly usage)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS usage_history (
      usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
      month TEXT NOT NULL,           -- YYYY-MM
      blood_group TEXT NOT NULL,
      units_used INTEGER NOT NULL
    );
    """)

    # Create default users if not exist
    cur.execute("SELECT COUNT(*) AS c FROM users;")
    c = cur.fetchone()["c"]
    if c == 0:
        cur.execute("INSERT INTO users (username,password,role) VALUES (?,?,?)", ("admin","admin123","admin"))
        cur.execute("INSERT INTO users (username,password,role) VALUES (?,?,?)", ("hospital","hospital123","hospital"))

    # Insert sample usage history if empty (for prediction demo)
    cur.execute("SELECT COUNT(*) AS c FROM usage_history;")
    if cur.fetchone()["c"] == 0:
        sample = [
            ("2025-09","O+",18), ("2025-10","O+",20), ("2025-11","O+",22), ("2025-12","O+",26), ("2026-01","O+",28),
            ("2025-09","A+",12), ("2025-10","A+",11), ("2025-11","A+",13), ("2025-12","A+",14), ("2026-01","A+",15),
            ("2025-09","B+",9),  ("2025-10","B+",10), ("2025-11","B+",10), ("2025-12","B+",11), ("2026-01","B+",12),
        ]
        cur.executemany("INSERT INTO usage_history (month,blood_group,units_used) VALUES (?,?,?)", sample)

    conn.commit()
    conn.close()

def require_login(role=None):
    if "user" not in session:
        return False
    if role and session.get("role") != role:
        return False
    return True

@app.route("/", methods=["GET"])
def home():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","").strip()

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
        user = cur.fetchone()
        conn.close()

        if not user:
            flash("Invalid username or password", "danger")
            return redirect(url_for("login"))

        session["user"] = user["username"]
        session["role"] = user["role"]
        flash(f"Welcome, {user['username']}!", "success")
        return redirect(url_for("admin_dashboard" if user["role"]=="admin" else "hospital_requests"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully", "info")
    return redirect(url_for("login"))

# ---------------- ADMIN ----------------
@app.route("/admin")
def admin_dashboard():
    if not require_login("admin"):
        return redirect(url_for("login"))

    conn = db()
    cur = conn.cursor()

    # Stats
    cur.execute("SELECT COUNT(*) AS c FROM donors")
    donors_count = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM requests WHERE status='PENDING'")
    pending_requests = cur.fetchone()["c"]

    # Stock totals per group
    cur.execute("""
    SELECT blood_group, COALESCE(SUM(units),0) AS total_units
    FROM blood_stock
    GROUP BY blood_group
    ORDER BY blood_group;
    """)
    stock = cur.fetchall()
    conn.close()

    # Low stock if < 3 units
    low = [s for s in stock if s["total_units"] < 3]

    return render_template(
        "admin_dashboard.html",
        donors_count=donors_count,
        pending_requests=pending_requests,
        stock=stock,
        low=low
    )

@app.route("/admin/donors", methods=["GET","POST"])
def donors():
    if not require_login("admin"):
        return redirect(url_for("login"))

    conn = db()
    cur = conn.cursor()

    if request.method == "POST":
        full_name = request.form.get("full_name","").strip()
        blood_group = request.form.get("blood_group","").strip()
        gender = request.form.get("gender","").strip()
        age = request.form.get("age","").strip()
        phone = request.form.get("phone","").strip()
        email = request.form.get("email","").strip()
        city = request.form.get("city","").strip()
        last_donation_date = request.form.get("last_donation_date","").strip()

        if not full_name or blood_group not in BLOOD_GROUPS or not phone:
            flash("Please fill required fields correctly (Name, Blood group, Phone).", "danger")
        else:
            cur.execute("""
            INSERT INTO donors(full_name,blood_group,gender,age,phone,email,city,last_donation_date,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            """, (full_name,blood_group,gender,int(age) if age else None,phone,email,city,last_donation_date,datetime.now().isoformat(timespec="seconds")))
            conn.commit()
            flash("Donor added successfully!", "success")
            return redirect(url_for("donors"))

    # search
    q_bg = request.args.get("bg","").strip()
    if q_bg in BLOOD_GROUPS:
        cur.execute("SELECT * FROM donors WHERE blood_group=? ORDER BY donor_id DESC", (q_bg,))
    else:
        cur.execute("SELECT * FROM donors ORDER BY donor_id DESC")

    donors_list = cur.fetchall()
    conn.close()

    return render_template("donors.html", donors=donors_list, blood_groups=BLOOD_GROUPS, selected_bg=q_bg)

@app.route("/admin/stock", methods=["GET","POST"])
def stock():
    if not require_login("admin"):
        return redirect(url_for("login"))

    conn = db()
    cur = conn.cursor()

    if request.method == "POST":
        blood_group = request.form.get("blood_group","").strip()
        units = request.form.get("units","").strip()
        collected_date = request.form.get("collected_date","").strip()
        expiry_date = request.form.get("expiry_date","").strip()

        if blood_group not in BLOOD_GROUPS or not units or int(units) <= 0 or not collected_date or not expiry_date:
            flash("Please fill all stock fields correctly.", "danger")
        else:
            cur.execute("""
            INSERT INTO blood_stock(blood_group,units,collected_date,expiry_date,created_at)
            VALUES(?,?,?,?,?)
            """, (blood_group,int(units),collected_date,expiry_date,datetime.now().isoformat(timespec="seconds")))
            conn.commit()
            flash("Stock added successfully!", "success")
            return redirect(url_for("stock"))

    # Inventory summary
    cur.execute("""
    SELECT blood_group, COALESCE(SUM(units),0) AS total_units
    FROM blood_stock
    GROUP BY blood_group
    ORDER BY blood_group
    """)
    summary = cur.fetchall()

    # Expiry alerts (within 7 days)
    cur.execute("SELECT * FROM blood_stock ORDER BY expiry_date ASC")
    rows = cur.fetchall()
    expiring = []
    today = date.today()
    for r in rows:
        try:
            exp = date.fromisoformat(r["expiry_date"])
            if (exp - today).days <= 7:
                expiring.append(r)
        except:
            pass

    conn.close()
    return render_template("stock.html", summary=summary, blood_groups=BLOOD_GROUPS, expiring=expiring)

@app.route("/admin/requests", methods=["GET","POST"])
def admin_requests():
    if not require_login("admin"):
        return redirect(url_for("login"))

    conn = db()
    cur = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action")
        request_id = request.form.get("request_id")

        # fetch request
        cur.execute("SELECT * FROM requests WHERE request_id=?", (request_id,))
        req = cur.fetchone()
        if not req:
            flash("Request not found.", "danger")
            conn.close()
            return redirect(url_for("admin_requests"))

        if action == "approve":
            # Check stock availability
            cur.execute("SELECT COALESCE(SUM(units),0) AS total_units FROM blood_stock WHERE blood_group=?", (req["blood_group"],))
            available = cur.fetchone()["total_units"]

            if available < req["units"]:
                flash(f"Not enough stock for {req['blood_group']}. Available: {available}, Needed: {req['units']}", "danger")
            else:
                # Reduce stock (simple: delete rows until units deducted)
                units_to_deduct = req["units"]
                cur.execute("SELECT * FROM blood_stock WHERE blood_group=? ORDER BY expiry_date ASC", (req["blood_group"],))
                batches = cur.fetchall()

                for b in batches:
                    if units_to_deduct <= 0:
                        break
                    if b["units"] <= units_to_deduct:
                        units_to_deduct -= b["units"]
                        cur.execute("DELETE FROM blood_stock WHERE stock_id=?", (b["stock_id"],))
                    else:
                        new_units = b["units"] - units_to_deduct
                        units_to_deduct = 0
                        cur.execute("UPDATE blood_stock SET units=? WHERE stock_id=?", (new_units, b["stock_id"]))

                cur.execute("UPDATE requests SET status='APPROVED' WHERE request_id=?", (request_id,))
                conn.commit()
                flash("Request approved and stock updated!", "success")

        elif action == "reject":
            cur.execute("UPDATE requests SET status='REJECTED' WHERE request_id=?", (request_id,))
            conn.commit()
            flash("Request rejected.", "info")

    cur.execute("SELECT * FROM requests ORDER BY request_id DESC")
    requests_list = cur.fetchall()
    conn.close()

    return render_template("requests.html", requests=requests_list, is_admin=True)

# ---------------- HOSPITAL ----------------
@app.route("/hospital/requests", methods=["GET","POST"])
def hospital_requests():
    if not require_login("hospital"):
        return redirect(url_for("login"))

    conn = db()
    cur = conn.cursor()

    if request.method == "POST":
        hospital_name = request.form.get("hospital_name","").strip()
        blood_group = request.form.get("blood_group","").strip()
        units = request.form.get("units","").strip()

        if not hospital_name or blood_group not in BLOOD_GROUPS or not units or int(units) <= 0:
            flash("Please fill request correctly.", "danger")
        else:
            cur.execute("""
            INSERT INTO requests(hospital_name,blood_group,units,status,created_at)
            VALUES(?,?,?,?,?)
            """, (hospital_name,blood_group,int(units),"PENDING",datetime.now().isoformat(timespec="seconds")))
            conn.commit()
            flash("Request submitted successfully!", "success")
            return redirect(url_for("hospital_requests"))

    cur.execute("SELECT * FROM requests ORDER BY request_id DESC")
    requests_list = cur.fetchall()
    conn.close()

    return render_template("requests.html", requests=requests_list, blood_groups=BLOOD_GROUPS, is_admin=False)

# ---------------- PREDICTION ----------------
@app.route("/admin/prediction", methods=["GET"])
def prediction():
    if not require_login("admin"):
        return redirect(url_for("login"))

    conn = db()
    df = pd.read_sql_query("SELECT * FROM usage_history ORDER BY month ASC", conn)
    conn.close()

    results = []
    if df.empty:
        return render_template("prediction.html", results=[], note="No usage history available.")

    # Convert month to numeric index
    df["month_dt"] = pd.to_datetime(df["month"] + "-01")
    df = df.sort_values("month_dt")

    # next month label
    last_month = df["month_dt"].max()
    next_month = (last_month + pd.offsets.MonthBegin(1)).strftime("%Y-%m")

    for bg in sorted(df["blood_group"].unique()):
        d = df[df["blood_group"] == bg].copy()
        d = d.sort_values("month_dt")

        # if less data, fallback average
        if len(d) < 3:
            pred_units = int(round(d["units_used"].mean()))
            method = "Average"
        else:
            # feature = month index
            d["m_index"] = range(1, len(d) + 1)
            X = d[["m_index"]]
            y = d["units_used"]
            model = LinearRegression()
            model.fit(X, y)

            pred_units = model.predict([[len(d) + 1]])[0]
            pred_units = int(max(0, round(pred_units)))
            method = "Linear Regression"

        # demand label
        if pred_units >= 20:
            demand = "HIGH"
        elif pred_units >= 10:
            demand = "MEDIUM"
        else:
            demand = "LOW"

        results.append({
            "blood_group": bg,
            "predicted_units": pred_units,
            "demand": demand,
            "method": method
        })

    # Sort by predicted units desc
    results = sorted(results, key=lambda x: x["predicted_units"], reverse=True)

    return render_template("prediction.html", results=results, next_month=next_month)

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
