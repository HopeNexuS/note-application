from flask import Blueprint, request, jsonify
from supabase import create_client
import os, base64, uuid, re
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

note_bp = Blueprint("note", __name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
_NOTES_HAS_USER_ID = None


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
# Process Images (Replace base64 with public URL)
# --------------------------------------------------
def process_images(content, note_id):
    content = content or ""
    image_matches = re.findall(r'<img[^>]+src="([^"]+)"', content)

    for img_src in image_matches:
        if img_src.startswith("data:image"):
            public_url, img_type = upload_image_to_storage(img_src)

            supabase.table("images").insert({
                "note_id": note_id,
                "image_url": public_url,
                "image_type": img_type
            }).execute()

            content = content.replace(img_src, public_url)

    return content


# --------------------------------------------------
# Helper: Embed / Extract user ownership marker
# --------------------------------------------------

def _embed_user_marker(content, user_id):
    # Store user_id in a hidden HTML comment at the beginning of the note content.
    marker = f"<!--uid:{user_id}-->"
    content = (content or "").lstrip()
    # Remove any existing marker
    content = re.sub(r"^<!--uid:[^>]+-->", "", content, flags=re.IGNORECASE)
    return marker + content


def _extract_user_id(content):
    if not content:
        return None
    m = re.match(r"^<!--uid:([0-9a-fA-F\-]+)-->", content.strip())
    return m.group(1) if m else None


def _strip_user_marker(content):
    return re.sub(r"^<!--uid:[^>]+-->", "", (content or ""), flags=re.IGNORECASE)


def _note_belongs_to_user(note, user_id):
    """Return True if this note should be visible/editable by the given user."""
    if not note or not user_id:
        return False

    # If the notes table has a real user_id column, use it first.
    if note.get("user_id"):
        return str(note.get("user_id")) == str(user_id)

    # Otherwise, fall back to the embedded marker.
    marker_owner = _extract_user_id(note.get("content"))
    return bool(marker_owner and marker_owner == str(user_id))


def notes_has_user_id():
    """Detect whether the notes table contains a user_id column."""
    global _NOTES_HAS_USER_ID
    if _NOTES_HAS_USER_ID is not None:
        return _NOTES_HAS_USER_ID

    try:
        supabase.table("notes").select("id,user_id").limit(1).execute()
        _NOTES_HAS_USER_ID = True
    except Exception as e:
        msg = str(e).lower()
        if "user_id" in msg and ("column" in msg or "does not exist" in msg):
            _NOTES_HAS_USER_ID = False
        else:
            # Unknown error: keep secure default behavior.
            _NOTES_HAS_USER_ID = True
    return _NOTES_HAS_USER_ID


# --------------------------------------------------
# CREATE NOTE
# --------------------------------------------------
@note_bp.route("/notes", methods=["POST"])
def create_note():
    data = request.json or {}

    notebook_id = data.get("notebook_id")
    title = (data.get("title") or "").strip() or "Untitled"
    content = data.get("content") or ""
    note_color = data.get("note_color") or data.get("color")
    user_id = data.get("user_id")

    has_user_id = notes_has_user_id()
    if has_user_id and not user_id:
        return jsonify({"status": "error", "message": "user_id is required"}), 400

    # Embed a user ownership marker in the note content so we can scope quick notes
    # even if the database schema doesn't have a user_id column.
    if user_id:
        content = _embed_user_marker(content, user_id)

    payload = {
        "notebook_id": notebook_id,
        "title": title,
        "content": content,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }
    if note_color:
        payload["note_color"] = note_color
    if has_user_id and user_id:
        payload["user_id"] = user_id

    try:
        note = supabase.table("notes").insert(payload).execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to create note: {e}"}), 500

    if not note.data:
        return jsonify({"status": "error", "message": "Note insert returned no data"}), 500

    note_id = note.data[0]["id"]

    try:
        updated_content = process_images(content, note_id)
        builder = supabase.table("notes") \
            .update({"content": updated_content}) \
            .eq("id", note_id)
        if has_user_id and user_id:
            builder = builder.eq("user_id", user_id)
        builder.execute()
    except Exception:
        # Keep note even if image processing fails.
        pass

    return jsonify({"status": "success", "note_id": note_id})


# --------------------------------------------------
# UPDATE NOTE
# --------------------------------------------------
@note_bp.route("/notes/<int:note_id>", methods=["PUT"])
def update_note(note_id):
    data = request.json or {}

    title = (data.get("title") or "").strip() or "Untitled"
    content = data.get("content") or ""
    note_color = data.get("note_color") or data.get("color")
    user_id = data.get("user_id")

    has_user_id = notes_has_user_id()
    if has_user_id and not user_id:
        return jsonify({"status": "error", "message": "user_id is required"}), 400

    # If the notes table does not have user_id, use the embedded marker to enforce ownership.
    if not has_user_id and user_id:
        existing = supabase.table("notes").select("content").eq("id", note_id).single().execute()
        if existing.data:
            owner = _extract_user_id(existing.data.get("content", ""))
            if owner and owner != user_id:
                return jsonify({"status": "error", "message": "Note not found for this user"}), 404

    if user_id:
        content = _embed_user_marker(content, user_id)

    try:
        updated_content = process_images(content, note_id)
    except Exception:
        updated_content = content

    update_payload = {
        "title": title,
        "content": updated_content,
        "updated_at": datetime.utcnow().isoformat()
    }
    if note_color:
        update_payload["note_color"] = note_color

    try:
        builder = supabase.table("notes") \
            .update(update_payload) \
            .eq("id", note_id)
        if has_user_id and user_id:
            builder = builder.eq("user_id", user_id)
        result = builder.execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to update note: {e}"}), 500

    if not result.data:
        return jsonify({"status": "error", "message": "Note not found for this user"}), 404

    return jsonify({"status": "updated", "note_id": note_id})


@note_bp.route("/quick_notes/<user_id>", methods=["GET"])
def get_quick_notes(user_id):
    if not user_id:
        return jsonify({"status": "error", "message": "user_id is required"}), 400

    has_user_id = notes_has_user_id()
    try:
        builder = supabase.table("notes") \
            .select("*") \
            .order("created_at", desc=True)
        if has_user_id:
            builder = builder.eq("user_id", user_id)
        resp = builder.execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to load quick notes: {e}"}), 500

    notes = resp.data or []

    # Ensure we only return notes that belong to the requesting user.
    filtered = []
    for note in notes:
        if _note_belongs_to_user(note, user_id):
            note["content"] = _strip_user_marker(note.get("content"))
            filtered.append(note)

    return jsonify(filtered)


# --------------------------------------------------
# DELETE NOTE
# --------------------------------------------------
@note_bp.route("/notes/<int:note_id>", methods=["DELETE"])
def delete_note(note_id):
    user_id = request.args.get("user_id")
    has_user_id = notes_has_user_id()
    if has_user_id and not user_id:
        return jsonify({"status": "error", "message": "user_id is required"}), 400

    # Normalize user_id for matching (Supabase may compare types strictly).
    user_id_filter = user_id
    if user_id:
        try:
            user_id_filter = int(user_id)
        except ValueError:
            user_id_filter = user_id

    # When we don't have a real user_id column, enforce ownership via embedded marker.
    if not has_user_id and user_id:
        existing = supabase.table("notes").select("content").eq("id", note_id).single().execute()
        if existing.data:
            owner = _extract_user_id(existing.data.get("content", ""))
            if owner and owner != user_id:
                return jsonify({"status": "error", "message": "Note not found for this user"}), 404

    try:
        builder = supabase.table("notes") \
            .delete() \
            .eq("id", note_id)
        if has_user_id and user_id:
            builder = builder.eq("user_id", user_id_filter)
        result = builder.execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to delete note: {e}"}), 500

    if getattr(result, "error", None):
        return jsonify({"status": "error", "message": f"Failed to delete note: {result.error}"}), 500

    # Verify the note is actually gone (catch odd supabase behavior / type mismatch).
    try:
        check = supabase.table("notes").select("id").eq("id", note_id)
        if has_user_id and user_id:
            check = check.eq("user_id", user_id_filter)
        check_res = check.single().execute()
        if check_res.data:
            # Still exists; deletion failed (likely due to type mismatch)
            return jsonify({"status": "error", "message": "Failed to delete note (still exists)"}), 500
    except Exception:
        # ignore check errors, assume delete worked
        pass

    return jsonify({"status": "deleted", "note_id": note_id})


# --------------------------------------------------
# GET SINGLE NOTE
# --------------------------------------------------
@note_bp.route("/notes/<int:note_id>", methods=["GET"])
def get_single_note(note_id):
    user_id = request.args.get("user_id")
    has_user_id = notes_has_user_id()
    if has_user_id and not user_id:
        return jsonify({"status": "error", "message": "user_id is required"}), 400

    try:
        builder = supabase.table("notes") \
            .select("*") \
            .eq("id", note_id)
        if has_user_id and user_id:
            builder = builder.eq("user_id", user_id)
        response = builder.single().execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to load note: {e}"}), 500

    note = response.data
    if not note or not _note_belongs_to_user(note, user_id):
        return jsonify({"status": "error", "message": "Note not found for this user"}), 404

    note["content"] = _strip_user_marker(note.get("content"))
    return jsonify(note)


# --------------------------------------------------
# GET NOTES INSIDE A NOTEBOOK
# --------------------------------------------------
@note_bp.route("/notebooks/<int:notebook_id>/notes", methods=["GET"])
def get_notes_by_notebook(notebook_id):
    user_id = request.args.get("user_id")
    has_user_id = notes_has_user_id()
    if has_user_id and not user_id:
        return jsonify({"status": "error", "message": "user_id is required"}), 400

    try:
        builder = supabase.table("notes") \
            .select("*") \
            .eq("notebook_id", notebook_id) \
            .order("created_at", desc=True)
        if has_user_id and user_id:
            builder = builder.eq("user_id", user_id)
        response = builder.execute()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to load notes: {e}"}), 500

    notes = response.data or []

    # Ensure we only return notes belonging to the requesting user when user_id is used.
    if not has_user_id:
        notes = [n for n in notes if _note_belongs_to_user(n, user_id)]

    # Strip any embedded markers before returning
    for n in notes:
        n["content"] = _strip_user_marker(n.get("content"))

    return jsonify(notes)
