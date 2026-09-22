"""Quick, standalone MongoDB connectivity check with DNS-aware diagnostics.

Run this first, before starting the whole app or running the seed script —
it's the fastest way to confirm your MONGODB_URI actually works from your
machine/network:

    cd backend
    python -m scripts.check_db
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.db.mongodb import connect_to_mongo, close_mongo_connection, mongodb  # noqa: E402


async def main() -> None:
    print(f"MONGODB_URI host: {settings.mongodb_uri.split('@')[-1].split('/')[0]}")
    print(f"Target database:  {settings.mongodb_db_name}")
    print("Connecting...\n")

    await connect_to_mongo()

    if mongodb.db is None:
        print("FAILED — see the error log above for the specific cause and fix.")
        await close_mongo_connection()
        sys.exit(1)

    try:
        await mongodb.client.admin.command("ping")
        collections = await mongodb.db.list_collection_names()
        print("SUCCESS — connected and authenticated.")
        print(f"Existing collections in '{settings.mongodb_db_name}': {collections or '(none yet)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED on ping after initial connect: {exc}")
        sys.exit(1)
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(main())
