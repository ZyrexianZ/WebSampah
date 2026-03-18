
from flask import Flask , render_template , request , redirect , session , flash , redirect , url_for
from werkzeug.utils import secure_filename
import json
import os
import bcrypt
import asyncio
from datetime import datetime
from typing import Any, Dict

try:
    from vercel.blob import BlobClient, get as blob_get
except Exception:
    BlobClient = None
    blob_get = None

app = Flask(__name__, static_folder="public", static_url_path="")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-secret-key")

# Blob storage files
USERS_FILE = "users.json"
TRASH_DATA_FILE = "trash_data.json"

BLOB_USERS_PATH = "db/users.json"
BLOB_TRASH_PATH = "db/trash_data.json"
PRIVATE_BLOB_ACCESS = "private"
USE_BLOB = bool(os.getenv("BLOB_READ_WRITE_TOKEN")) and BlobClient is not None

UPLOAD_FOLDER = "uploads"
if not USE_BLOB:
    app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)

# Initialize users storage
def _read_stream_to_bytes(stream) -> bytes:
    if hasattr(stream, "read"):
        return stream.read()

    if hasattr(stream, "__aiter__"):
        async def consume_async():
            chunks = []
            async for chunk in stream:
                chunks.append(chunk)
            return b"".join(chunks)

        return asyncio.run(consume_async())

    return b"".join(stream)

def _blob_client():
    if not USE_BLOB:
        return None
    return BlobClient()

def _blob_read_json(pathname: str, default: Dict[str, Any]):
    if not USE_BLOB or blob_get is None:
        return default
    try:
        result = blob_get(pathname, access=PRIVATE_BLOB_ACCESS)
        if result is None:
            return default
        raw = _read_stream_to_bytes(result.stream)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return default

def _blob_write_json(pathname: str, data: Dict[str, Any]):
    if not USE_BLOB:
        return
    client = _blob_client()
    if client is None:
        return
    payload = json.dumps(data).encode("utf-8")
    client.put(
        pathname,
        payload,
        access=PRIVATE_BLOB_ACCESS,
        content_type="application/json",
        overwrite=True
    )

def init_users_storage():
    if USE_BLOB:
        data = _blob_read_json(BLOB_USERS_PATH, {"next_uid": 1, "users": []})
        _blob_write_json(BLOB_USERS_PATH, data)
        return

    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'w') as f:
            json.dump({"next_uid": 1, "users": []}, f)

# Initialize trash data storage
def init_trash_storage():
    if USE_BLOB:
        data = _blob_read_json(BLOB_TRASH_PATH, {"records": []})
        _blob_write_json(BLOB_TRASH_PATH, data)
        return

    if not os.path.exists(TRASH_DATA_FILE):
        with open(TRASH_DATA_FILE, 'w') as f:
            json.dump({"records": []}, f)

def load_users_storage():
    if USE_BLOB:
        data = _blob_read_json(BLOB_USERS_PATH, {"next_uid": 1, "users": []})
    else:
        if not os.path.exists(USERS_FILE):
            return {"next_uid": 1, "users": []}
        with open(USERS_FILE, 'r') as f:
            data = json.load(f)

    # Migrate legacy dict format {"username": {"password": ..., "jumlah_poin": ...}}
    if isinstance(data, dict) and "users" not in data and "next_uid" not in data:
        users_list = []
        next_uid = 1
        for username, info in data.items():
            users_list.append({
                "uid": next_uid,
                "nama": username,
                "password": info.get("password", ""),
                "jumlah_poin": info.get("jumlah_poin", 0)
            })
            next_uid += 1
        data = {"next_uid": next_uid, "users": users_list}

    # Migrate empty/invalid structure to expected shape
    if not isinstance(data, dict) or "users" not in data or "next_uid" not in data:
        data = {"next_uid": 1, "users": []}

    return data

def save_users_storage(data):
    if USE_BLOB:
        _blob_write_json(BLOB_USERS_PATH, data)
        return

    with open(USERS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def load_trash_storage():
    if USE_BLOB:
        data = _blob_read_json(BLOB_TRASH_PATH, {"records": []})
    else:
        if not os.path.exists(TRASH_DATA_FILE):
            return {"records": []}
        with open(TRASH_DATA_FILE, 'r') as f:
            data = json.load(f)

    # Migrate legacy list format
    if isinstance(data, list):
        data = {"records": data}

    if not isinstance(data, dict) or "records" not in data:
        data = {"records": []}

    return data

def save_trash_storage(data):
    if USE_BLOB:
        _blob_write_json(BLOB_TRASH_PATH, data)
        return

    with open(TRASH_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

init_users_storage()
init_trash_storage()

# Poin jenis sampah
poin_sampah = {
    "Plastik": 10,
    "Kertas": 15,
    "Logam": 5
}

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        
        users_data = load_users_storage()
        user = next((u for u in users_data["users"] if u["nama"] == username), None)

        if user:
            if bcrypt.checkpw(password.encode(), user['password'].encode('utf-8')):
                session['nama'] = username
                session['uid'] = user['uid']
                return redirect('/dashboard')
            else:
                flash("Gagal, Username dan Password tidak cocok")
                return redirect('/')
        else:
            flash("Gagal, User tidak ditemukan")
            return redirect('/')
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"] )
def register():
    if request.method == "POST":
        newname = request.form["newname"]
        newpass = request.form["newpass"]
        
        users_data = load_users_storage()
        if any(u["nama"] == newname for u in users_data["users"]):
            flash("Username sudah terdaftar, gunakan username lain")
            return redirect('/register')

        hash_password = bcrypt.hashpw(newpass.encode(), bcrypt.gensalt()).decode('utf-8')
        new_uid = users_data["next_uid"]
        users_data["users"].append({
            "uid": new_uid,
            "nama": newname,
            "password": hash_password,
            "jumlah_poin": 0
        })
        users_data["next_uid"] = new_uid + 1
        save_users_storage(users_data)
        
        return redirect('/')
    
    return render_template("register.html")
        

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "nama" not in session:
        return redirect("/")

    if request.method == "POST":
        jenis = request.form["jenis"]
        kg = float(request.form["kg"])
        if kg <= 0:
            flash("Berat sampah harus lebih dari 0")
            return redirect("/dashboard")
        elif kg > 100:
            flash("Berat sampah tidak boleh melebihi 100 kg")
            return redirect("/dashboard")
        
        poin = kg * poin_sampah[jenis]
        
        foto = request.files["foto"]
        filename = secure_filename(foto.filename)
        if USE_BLOB:
            client = _blob_client()
            if client:
                file_bytes = foto.read()
                upload_path = f"uploads/{session.get('uid')}/{int(datetime.now().timestamp())}_{filename}"
                client.put(
                    upload_path,
                    file_bytes,
                    access=PRIVATE_BLOB_ACCESS,
                    content_type=foto.mimetype or "application/octet-stream",
                    overwrite=True
                )
                filename = upload_path
        else:
            foto.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        # Save trash data to JSON
        trash_data = load_trash_storage()

        trash_data["records"].append({
            "uid": session.get("uid"),
            "tanggal_submit": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "jenis_sampah": jenis,
            "berat": kg,
            "file": filename
        })

        save_trash_storage(trash_data)

        # Update user points
        users_data = load_users_storage()
        user = next((u for u in users_data["users"] if u["uid"] == session.get("uid")), None)
        if user:
            user["jumlah_poin"] += poin
            save_users_storage(users_data)

    # Get user points
    users_data = load_users_storage()
    user = next((u for u in users_data["users"] if u["uid"] == session.get("uid")), None)
    total_poin = user["jumlah_poin"] if user else 0

    return render_template("dashboard.html", 
                           user=session["nama"],
                           total_poin=total_poin,
                           jenis_list=poin_sampah.keys())
@app.route("/logout")
def logout():
    session.pop("nama", None)
    return redirect("/")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
