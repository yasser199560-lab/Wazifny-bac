"""Mongo document shape for the `users` collection (Core Identity).

Not enforced at the DB layer (Mongo is schemaless) — this is documentation
for what `auth.py` / `deps.py` read and write.

{
    "_id": ObjectId,
    "full_name": str,
    "email": str,                 # unique, lowercased
    "password_hash": str,
    "role": "talent" | "employer" | "admin",
    "phone": str | None,
    "is_verified": bool,
    "is_blocked": bool,           # set by the admin panel
    "created_at": datetime,
}
"""
