import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ConfigurationError, ServerSelectionTimeoutError

from app.core.config import settings

logger = logging.getLogger("wazifny.db")


class MongoDB:
    client: AsyncIOMotorClient | None = None
    db: AsyncIOMotorDatabase | None = None


mongodb = MongoDB()


async def connect_to_mongo() -> None:
    """Create the Motor client and verify connectivity with a `ping`.

    `mongodb+srv://` URIs (the Atlas default) need a DNS SRV + TXT lookup
    before the driver even knows which hosts to talk to. That step is where
    most first-time connection failures happen — not the actual database
    auth — so we ping eagerly on startup and print a specific, actionable
    message for each common failure mode instead of a raw stack trace.
    """
    mongodb.client = AsyncIOMotorClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=8000,
        retryWrites=True,
    )
    mongodb.db = mongodb.client[settings.mongodb_db_name]

    try:
        await mongodb.client.admin.command("ping")
        logger.info("Connected to MongoDB database '%s'.", settings.mongodb_db_name)
    except ConfigurationError as exc:
        # Raised when the `mongodb+srv://` hostname's DNS SRV/TXT records
        # can't be resolved at all — this is a DNS problem, not a MongoDB
        # problem, and it will NOT go away by retrying the same query.
        logger.error(
            "\n"
            "MongoDB DNS resolution failed (%s).\n"
            "This means the mongodb+srv:// hostname's SRV/TXT records could not be looked up.\n"
            "How to fix it, in order of likelihood:\n"
            "  1. Make sure the `dnspython` package is installed "
            "(it's required for mongodb+srv:// URIs): pip install dnspython\n"
            "  2. Check your machine/network's DNS resolver. Corporate, school, or some "
            "VPN/firewalled networks block the DNS SRV record lookups Atlas relies on. "
            "Try switching to a public DNS (e.g. 8.8.8.8 / 1.1.1.1) or a different network/hotspot.\n"
            "  3. As a workaround that skips SRV lookup entirely, use the non-SRV connection "
            "string instead: in Atlas > Database > Connect > Drivers, choose the legacy "
            "'mongodb://' format (lists each shard host:port explicitly) and put that in "
            "MONGODB_URI.\n"
            "  4. Double-check the cluster hostname in MONGODB_URI has no typos "
            "(e.g. cluster0.whtyswz.mongodb.net).\n",
            exc,
        )
        # Don't crash the whole app — keep it up so /health and non-DB
        # routes still work, and so retrying after a fix doesn't require a
        # restart (Motor lazily reconnects on the next operation).
    except ServerSelectionTimeoutError as exc:
        logger.error(
            "\n"
            "MongoDB connection timed out (%s).\n"
            "DNS resolved fine, but no server responded in time. Usual causes:\n"
            "  1. Your current IP isn't in the Atlas cluster's Network Access allow-list. "
            "In Atlas > Network Access, add your IP (or 0.0.0.0/0 for quick local testing).\n"
            "  2. A firewall/VPN is blocking outbound traffic on port 27017.\n"
            "  3. The cluster is paused (free-tier M0 clusters auto-pause when idle) — "
            "resume it from the Atlas dashboard.\n",
            exc,
        )
    except Exception as exc:  # noqa: BLE001 — surface anything else too, e.g. bad auth
        logger.error("Unexpected error connecting to MongoDB: %s", exc)


async def close_mongo_connection() -> None:
    if mongodb.client:
        mongodb.client.close()


def get_database() -> AsyncIOMotorDatabase:
    assert mongodb.db is not None, "Database not initialized — call connect_to_mongo() first"
    return mongodb.db
