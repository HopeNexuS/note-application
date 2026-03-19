from flask import Blueprint, request, jsonify
from supabase import create_client
from werkzeug.security import generate_password_hash, check_password_hash
import os, base64, uuid, re
from datetime import datetime
from dotenv import load_dotenv


load_dotenv()

notebook_bp = Blueprint("notebook", __name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# --------------------------------------------------
# Helper: Upload Image to Supabase Storage
# --------------------------------------------------
def upload_image_to_storage(base64_data):
    header, encoded = base64_data.split(",", 1)
    file_ext = header.split("/")[1].split(";")[0]

    file_name = f"{uuid.uuid4()}.{file_ext}"
    image_bytes = base64.b64decode(encoded)

    supabase.storage.from_("notebook-images").upload(
        file_name,
        image_bytes,
        {"content-type": f"image/{file_ext}"}
    )

    public_url = supabase.storage.from_("notebook-images").get_public_url(file_name)
    return public_url, file_ext


# --------------------------------------------------
# Save Notebook
# --------------------------------------------------
@notebook_bp.route("/save_notebook", methods=["POST"])
def save_notebook():
    data = request.json or {}

    user_id = data.get("user_id")
    title = (data.get("title") or "").strip() or "Untitled"
    content = data.get("content") or ""
    is_protected = bool(data.get("is_protected", False))
    password = (data.get("password") or "").strip()

    if not user_id:
        return jsonify({"status": "error", "message": "user_id is required"}), 400

    if is_protected and not password:
        return jsonify({"status": "error", "message": "Password is required for protected notebooks"}), 400

    password_hash = generate_password_hash(password) if is_protected else None

    try:
        notebook = supabase.table("notebooks").insert({
            "user_id": user_id,
            "title": title,
            "description": content,
            "icon": "fa-solid fa-book",
            "is_protected": is_protected,
            "password_hash": password_hash,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }).execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to save notebook: {e}"}), 500

    if not notebook.data:
        return jsonify({"status": "error", "message": "Notebook insert returned no data"}), 500

    notebook_id = notebook.data[0].get("id")
    if notebook_id is None:
        return jsonify({"status": "error", "message": "Notebook id not returned from database"}), 500

    image_matches = re.findall(r'<img[^>]+src="([^"]+)"', content)
    for img_src in image_matches:
        if img_src.startswith("data:image"):
            try:
                public_url, img_type = upload_image_to_storage(img_src)
                supabase.table("images").insert({
                    "notebook_id": notebook_id,
                    "image_url": public_url,
                    "image_type": img_type
                }).execute()
            except Exception:
                continue

    return jsonify({"status": "success", "notebook_id": notebook_id})

# --------------------------------------------------
# Update Notebook
# --------------------------------------------------
@notebook_bp.route("/update_notebook/<notebook_id>", methods=["PUT"])
def update_notebook(notebook_id):
    data = request.json or {}
    user_id = data.get("user_id")

    if not user_id:
        return jsonify({"status": "error", "message": "user_id is required"}), 400

    try:
        existing = supabase.table("notebooks") \
            .select("id, is_protected, password_hash") \
            .eq("id", notebook_id) \
            .eq("user_id", user_id) \
            .single() \
            .execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to load notebook: {e}"}), 500

    if not existing.data:
        return jsonify({"status": "error", "message": "Notebook not found for this user"}), 404

    update_data = {
        "updated_at": datetime.utcnow().isoformat()
    }

    if "title" in data:
        update_data["title"] = data["title"]

    if "content" in data:
        update_data["description"] = data["content"]

    if "icon" in data:
        update_data["icon"] = data["icon"]

    if "color" in data:
        update_data["color"] = data["color"]

    if "is_protected" in data:
        wants_protection = bool(data.get("is_protected"))
        new_password = (data.get("password") or "").strip()

        if wants_protection:
            update_data["is_protected"] = True
            if new_password:
                update_data["password_hash"] = generate_password_hash(new_password)
            elif not existing.data.get("password_hash"):
                return jsonify({"status": "error", "message": "Password is required to protect this notebook"}), 400
        else:
            update_data["is_protected"] = False
            update_data["password_hash"] = None

    try:
        result = supabase.table("notebooks") \
            .update(update_data) \
            .eq("id", notebook_id) \
            .eq("user_id", user_id) \
            .execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to update notebook: {e}"}), 500

    if not result.data:
        return jsonify({"status": "error", "message": "Notebook not found for this user"}), 404

    return jsonify({"status": "updated", "notebook_id": notebook_id})


# --------------------------------------------------
# Delete Notebook
# --------------------------------------------------
@notebook_bp.route("/delete_notebook/<notebook_id>", methods=["DELETE"])
def delete_notebook(notebook_id):
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"status": "error", "message": "user_id is required"}), 400

    try:
        user_id_cast = int(user_id)
    except ValueError:
        user_id_cast = user_id

    # Delete any notes belonging to this notebook (best-effort).
    try:
        supabase.table("notes") \
            .delete() \
            .eq("notebook_id", notebook_id) \
            .eq("user_id", user_id_cast) \
            .execute()
    except Exception:
        pass

    try:
        result = supabase.table("notebooks") \
            .delete() \
            .eq("id", notebook_id) \
            .eq("user_id", user_id_cast) \
            .execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to delete notebook: {e}"}), 500

    if getattr(result, "error", None):
        return jsonify({"status": "error", "message": f"Failed to delete notebook: {result.error}"}), 500

    return jsonify({"status": "deleted", "notebook_id": notebook_id})


# --------------------------------------------------
# Get Single Notebook
# --------------------------------------------------
@notebook_bp.route("/get_single_notebook/<notebook_id>")
def get_single_notebook(notebook_id):
    user_id = request.args.get("user_id")

    builder = supabase.table("notebooks") \
        .select("id, user_id, title, description, icon, color, is_protected, created_at, updated_at") \
        .eq("id", notebook_id)

    if user_id:
        builder = builder.eq("user_id", user_id)

    try:
        response = builder.single().execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to load notebook: {e}"}), 500

    if not response.data:
        return jsonify({"status": "error", "message": "Notebook not found"}), 404

    return jsonify(response.data)



# --------------------------------------------------
# Get All Notebooks of User
# --------------------------------------------------
@notebook_bp.route("/get_notebooks/<user_id>", methods=["GET"])
def get_notebooks(user_id):
    if not user_id:
        return jsonify({"status": "error", "message": "user_id is required"}), 400

    try:
        response = supabase.table("notebooks") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to load notebooks: {e}"}), 500

    return jsonify(response.data or [])

# --------------------------------------------------
# Verify Notebook Password
# --------------------------------------------------
@notebook_bp.route("/verify_notebook_password/<notebook_id>", methods=["POST"])
def verify_notebook_password(notebook_id):
    data = request.json or {}
    user_id = data.get("user_id")
    password = (data.get("password") or "").strip()

    if not user_id or not password:
        return jsonify({"status": "error", "message": "user_id and password are required"}), 400

    try:
        response = supabase.table("notebooks") \
            .select("password_hash, is_protected") \
            .eq("id", notebook_id) \
            .eq("user_id", user_id) \
            .single() \
            .execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Verification failed: {e}"}), 500

    notebook = response.data
    if not notebook:
        return jsonify({"status": "error", "message": "Notebook not found"}), 404

    if not notebook.get("is_protected"):
        return jsonify({"status": "success", "valid": True})

    valid = check_password_hash(notebook.get("password_hash") or "", password)
    return jsonify({"status": "success", "valid": valid})
