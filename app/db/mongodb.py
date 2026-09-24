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
        serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
        connectTimeoutMS=settings.mongodb_connect_timeout_ms,
        maxPoolSize=settings.mongodb_max_pool_size,
        minPoolSize=settings.mongodb_min_pool_size,
        maxIdleTimeMS=120000,
        retryWrites=True,
    )
    mongodb.db = mongodb.client[settings.mongodb_db_name]

    try:
        await mongodb.client.admin.command("ping")
        await ensure_indexes(mongodb.db)
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


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create the indexes used by the highest-traffic API queries.

    MongoDB makes this operation idempotent, so it is safe to run once during
    startup. These indexes prevent list, auth, notification, and matching
    endpoints from scanning entire collections as the platform grows.
    """
    indexes = {
        "users": [([('email', 1)], {"unique": True, "name": "email_unique"})],
        "jobs": [
            ([('status', 1), ('posted_at', -1)], {"name": "status_posted_at"}),
            ([('employer_id', 1), ('posted_at', -1)], {"name": "employer_posted_at"}),
            ([('category', 1), ('status', 1), ('posted_at', -1)], {"name": "category_status_posted_at"}),
            ([('job_type', 1), ('status', 1), ('posted_at', -1)], {"name": "job_type_status_posted_at"}),
        ],
        "applications": [
            ([('talent_id', 1), ('applied_at', -1)], {"name": "talent_applied_at"}),
            ([('job_id', 1), ('applied_at', -1)], {"name": "job_applied_at"}),
        ],
        "talent_skills": [([('talent_id', 1)], {"name": "talent_id"})],
        "work_preferences": [([('talent_id', 1)], {"name": "talent_id"})],
        "talent_profiles": [([('user_id', 1)], {"name": "user_id"})],
        "employer_profiles": [([('user_id', 1)], {"name": "user_id"})],
        "job_matches": [
            ([('talent_id', 1), ('job_id', 1)], {"name": "talent_job"}),
            ([('job_id', 1), ('match_score', -1)], {"name": "job_score"}),
        ],
        "notifications": [([('user_id', 1), ('created_at', -1)], {"name": "user_created_at"})],
        "messages": [([('conversation_id', 1), ('sent_at', 1)], {"name": "conversation_sent_at"})],
        "conversations": [
            ([('talent_id', 1), ('employer_id', 1)], {"name": "talent_employer"}),
            ([('talent_id', 1), ('updated_at', -1)], {"name": "talent_updated_at"}),
            ([('employer_id', 1), ('updated_at', -1)], {"name": "employer_updated_at"}),
        ],
        "saved_jobs": [([('talent_id', 1), ('saved_at', -1)], {"name": "talent_saved_at"})],
        "saved_candidates": [([('employer_id', 1), ('saved_at', -1)], {"name": "employer_saved_at"})],
        "testimonials": [([('featured', 1), ('created_at', -1)], {"name": "featured_created_at"})],
        "articles": [([('published_at', -1)], {"name": "published_at"})],
    }

    for collection_name, collection_indexes in indexes.items():
        collection = db[collection_name]
        for keys, options in collection_indexes:
            try:
                await collection.create_index(keys, **options)
            except Exception as exc:  # noqa: BLE001 - one stale index must not block boot
                logger.warning("Could not ensure index %s.%s: %s", collection_name, options["name"], exc)


def get_database() -> AsyncIOMotorDatabase:
    assert mongodb.db is not None, "Database not initialized — call connect_to_mongo() first"
    return mongodb.db
