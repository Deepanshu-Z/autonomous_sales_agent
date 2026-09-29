"""
redis_client.py — Centralized Upstash Redis connection service.
Fixes compatibility issues between upstash_redis and redis-py.
"""

import os
from dotenv import load_dotenv

load_dotenv()

UPSTASH_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")


class UpstashRedisWrapper:
    """
    Wrapper around upstash_redis.Redis client.
    Translates standard redis-py calls (like hset with mapping={...}) so existing code runs without errors.
    """
    def __init__(self, client):
        self._client = client

    def hset(self, name: str, key: str = None, value: str = None, mapping: dict = None, values: dict = None):
        if mapping is not None:
            return self._client.hset(name, values=mapping)
        if values is not None:
            return self._client.hset(name, values=values)
        if key is not None and value is not None:
            return self._client.hset(name, key, value)
        return self._client.hset(name, key=key, value=value)

    def __getattr__(self, name):
        return getattr(self._client, name)


def get_upstash_redis():
    """
    Returns Redis client.
    If REDIS_URL is configured, connects directly to self-hosted native Redis.
    Otherwise uses Upstash REST client if credentials configured.
    """
    if REDIS_URL:
        from redis import Redis as StandardRedis
        return StandardRedis.from_url(REDIS_URL, decode_responses=True)

    if UPSTASH_REST_URL and UPSTASH_REST_TOKEN:
        from upstash_redis import Redis as UpstashRedis
        client = UpstashRedis(url=UPSTASH_REST_URL, token=UPSTASH_REST_TOKEN)
        return UpstashRedisWrapper(client)
    
    from redis import Redis as StandardRedis
    return StandardRedis.from_url("redis://localhost:6379", decode_responses=True)


def get_redis_connection():
    """
    Returns TCP Redis connection for task queues, locks, and round-robin.
    If REDIS_URL is set, connects to self-hosted Redis natively.
    """
    from redis import Redis as StandardRedis

    if REDIS_URL:
        return StandardRedis.from_url(REDIS_URL, decode_responses=True)

    if UPSTASH_REST_URL and UPSTASH_REST_TOKEN:
        host = UPSTASH_REST_URL.replace("https://", "").replace("http://", "").strip("/")
        tcp_url = f"rediss://default:{UPSTASH_REST_TOKEN}@{host}:6379"
        return StandardRedis.from_url(tcp_url, decode_responses=True, ssl_cert_reqs=None)

    return StandardRedis.from_url("redis://localhost:6379", decode_responses=True)


_async_redis_clients = {}


def get_async_redis_connection():
    """
    Returns non-blocking Async Redis connection (redis.asyncio) for high-concurrency webhook ingestion.
    Reuses connection pool safely per event loop to prevent 'Event loop is closed' errors.
    """
    import asyncio
    from redis.asyncio import Redis as AsyncRedis

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop in _async_redis_clients:
        if not loop.is_closed():
            return _async_redis_clients[loop]

    if REDIS_URL:
        client = AsyncRedis.from_url(REDIS_URL, decode_responses=True)
    elif UPSTASH_REST_URL and UPSTASH_REST_TOKEN:
        host = UPSTASH_REST_URL.replace("https://", "").replace("http://", "").strip("/")
        tcp_url = f"rediss://default:{UPSTASH_REST_TOKEN}@{host}:6379"
        client = AsyncRedis.from_url(tcp_url, decode_responses=True, ssl_cert_reqs=None)
    else:
        client = AsyncRedis.from_url("redis://localhost:6379", decode_responses=True)

    if loop:
        _async_redis_clients[loop] = client

    return client

