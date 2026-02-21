from flask import Blueprint, request, jsonify
from supabase import create_client
import os, base64, uuid
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
@notebook_bp.route("/save_notebook", methods=["GET", "POST"])
def save_notebook():

    data = request.json

    user_id = data.get("user_id")        # must come from login session
    title = data.get("title")
    content = data.get("content")        # full HTML content

    # Insert notebook
    notebook = supabase.table("notebooks").insert({
        "user_id": user_id,
        "title": title,
        "description": content,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }).execute()

    notebook_id = notebook.data[0]["id"]

    # Extract images from content (base64 only)
    import re
    image_matches = re.findall(r'<img[^>]+src="([^"]+)"', content)

    for img_src in image_matches:
        if img_src.startswith("data:image"):

            public_url, img_type = upload_image_to_storage(img_src)

            # Save image record
            supabase.table("images").insert({
                "notebook_id": notebook_id,
                "image_url": public_url,
                "image_type": img_type
            }).execute()

    return jsonify({"status": "success", "notebook_id": notebook_id})


# --------------------------------------------------
# Update Notebook
# --------------------------------------------------
@notebook_bp.route("/update_notebook/<int:notebook_id>", methods=["PUT"])
def update_notebook(notebook_id):

    data = request.json

    title = data.get("title")
    content = data.get("content")

    supabase.table("notebooks").update({
        "title": title,
        "description": content,
        "updated_at": datetime.utcnow()
    }).eq("id", notebook_id).execute()

    return jsonify({"status": "updated"})

