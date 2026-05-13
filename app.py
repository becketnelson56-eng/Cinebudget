from flask import Flask, render_template, request, redirect, url_for, session, flash
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)

CREDENTIALS = {
    "username": "admin",
    "password": "breakdown2024",
}


@app.route("/", methods=["GET"])
def index():
    if "user" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == CREDENTIALS["username"] and password == CREDENTIALS["password"]:
            session["user"] = username
            return redirect(url_for("dashboard"))
        error = "Invalid credentials."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect(url_for("login"))
    tab = request.args.get("tab", "budget")
    valid_tabs = {"budget", "breakdown", "scheduling", "deals", "export"}
    if tab not in valid_tabs:
        tab = "budget"
    return render_template("dashboard.html", active_tab=tab)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
