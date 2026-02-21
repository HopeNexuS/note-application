import os
import random
import datetime
import requests
from notebook_s import notebook_bp
from flask import Flask, render_template, request, jsonify
from email_service import send_otp_email, send_welcome_email
from dotenv import load_dotenv

load_dotenv()

# ==============================
# CONFIGURATION
# ==============================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

app = Flask(__name__, template_folder="template")
app.register_blueprint(notebook_bp)

# ==============================
# ROUTES
# ==============================

@app.route("/")
def home():
    return render_template("auth.html")

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/editor")
def editor():
    return render_template("editor.html")

# ==============================
# REGISTER
# ==============================

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")
    email = data.get("email")

    if not username or not password or not email:
        return jsonify({"success": False, "message": "All fields required"}), 400

    try:
        # Check existing user
        check_url = f"{SUPABASE_URL}/rest/v1/users?or=(username.eq.{username},email.eq.{email})&select=id"
        res = requests.get(check_url, headers=SUPABASE_HEADERS)

        if res.json():
            return jsonify({"success": False, "message": "User already exists"}), 409

        payload = {
            "username": username,
            "password": password,   # Plain password
            "email": email,
            "isotpused": False
        }

        insert_url = f"{SUPABASE_URL}/rest/v1/users"
        insert_res = requests.post(insert_url, headers=SUPABASE_HEADERS, json=payload)

        if insert_res.status_code == 201:
            send_welcome_email(email, username)
            return jsonify({"success": True})

        return jsonify({"success": False, "message": insert_res.text}), 400

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ==============================
# LOGIN
# ==============================

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    url = f"{SUPABASE_URL}/rest/v1/users?username=eq.{username}&select=id,username,password,email"
    res = requests.get(url, headers=SUPABASE_HEADERS)
    users = res.json()

    if not users:
        return jsonify({"success": False, "message": "User not found"}), 404

    user = users[0]

    if user["password"] != password:
        return jsonify({"success": False, "message": "Invalid password"}), 401

    return jsonify({
        "success": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"]
        }
    })


# ==============================
# SEND OTP
# ==============================

@app.route("/send-otp", methods=["POST"])
def send_otp():
    data = request.get_json()
    email = data.get("email")

    if not email:
        return jsonify({"success": False, "message": "Email required"}), 400

    try:
        search_url = f"{SUPABASE_URL}/rest/v1/users?email=eq.{email}&select=id"
        search_res = requests.get(search_url, headers=SUPABASE_HEADERS)
        users = search_res.json()

        if not users:
            return jsonify({"success": False, "message": "Email not registered"}), 404

        otp_code = str(random.randint(100000, 999999))
        expiry_time = (datetime.datetime.utcnow() + datetime.timedelta(minutes=10)).isoformat()

        update_url = f"{SUPABASE_URL}/rest/v1/users?email=eq.{email}"
        payload = {
            "otp": otp_code,
            "otpexp": expiry_time,
            "isotpused": False
        }

        requests.patch(update_url, headers=SUPABASE_HEADERS, json=payload)

        send_otp_email(email, otp_code)

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ==============================
# VERIFY OTP
# ==============================

@app.route("/verify-otp", methods=["POST"])
def verify_otp():
    data = request.get_json()
    email = data.get("email", "").strip()
    otp = data.get("otp", "").strip()

    if not email or not otp:
        return jsonify({"success": False, "message": "Email and OTP required"}), 400

    try:
        search_url = f"{SUPABASE_URL}/rest/v1/users?email=eq.{email}&select=otp,otpexp,isotpused"
        res = requests.get(search_url, headers=SUPABASE_HEADERS)
        users = res.json()

        if not users:
            return jsonify({"success": False, "message": "User not found"}), 404

        user = users[0]

        stored_otp = str(user.get("otp")).strip()

        print("Entered OTP:", otp)
        print("Stored OTP:", stored_otp)

        if stored_otp != otp:
            return jsonify({"success": False, "message": "Invalid OTP"}), 400

        if user.get("isotpused"):
            return jsonify({"success": False, "message": "OTP already used"}), 400

        expiry_str = user.get("otpexp")
        if not expiry_str:
            return jsonify({"success": False, "message": "OTP expired"}), 400

        expiry = datetime.datetime.fromisoformat(expiry_str.replace("Z", ""))

        if datetime.datetime.utcnow() > expiry:
            return jsonify({"success": False, "message": "OTP expired"}), 400

        # Mark as used
        update_url = f"{SUPABASE_URL}/rest/v1/users?email=eq.{email}"
        requests.patch(update_url, headers=SUPABASE_HEADERS, json={"isotpused": True})

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ==============================
# RESET PASSWORD
# ==============================

@app.route("/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json()
    email = data.get("email")
    new_password = data.get("newPassword")

    if not email or not new_password:
        return jsonify({"success": False, "message": "Missing data"}), 400

    search_url = f"{SUPABASE_URL}/rest/v1/users?email=eq.{email}&select=id,isotpused"
    res = requests.get(search_url, headers=SUPABASE_HEADERS)
    users = res.json()

    if not users:
        return jsonify({"success": False, "message": "User not found"}), 404

    if not users[0].get("isotpused"):
        return jsonify({"success": False, "message": "OTP verification required"}), 403

    update_url = f"{SUPABASE_URL}/rest/v1/users?email=eq.{email}"
    payload = {
        "password": new_password,
        "otp": None,
        "otpexp": None,
        "isotpused": False
    }

    requests.patch(update_url, headers=SUPABASE_HEADERS, json=payload)

    return jsonify({"success": True})


# ==============================

if __name__ == "__main__":
    app.run(debug=True, port=5000)
