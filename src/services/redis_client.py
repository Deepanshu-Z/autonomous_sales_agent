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
    Returns Upstash REST client (from upstash_redis import Redis) wrapped to fix hset mapping compatibility.
    """
    from upstash_redis import Redis as UpstashRedis

    if UPSTASH_REST_URL and UPSTASH_REST_TOKEN:
        client = UpstashRedis(url=UPSTASH_REST_URL, token=UPSTASH_REST_TOKEN)
        return UpstashRedisWrapper(client)
    
    from redis import Redis as StandardRedis
    url = REDIS_URL or "redis://localhost:6379"
    return StandardRedis.from_url(url, decode_responses=True)


def get_redis_connection():
    """
    Returns TCP Redis connection for RQ task queues, locks, and workers.
    Automatically connects to Upstash via TLS rediss:// if REST credentials are configured.
    """
    from redis import Redis as StandardRedis

    if UPSTASH_REST_URL and UPSTASH_REST_TOKEN:
        host = UPSTASH_REST_URL.replace("https://", "").replace("http://", "").strip("/")
        tcp_url = f"rediss://default:{UPSTASH_REST_TOKEN}@{host}:6379"
        return StandardRedis.from_url(tcp_url, decode_responses=True, ssl_cert_reqs=None)

    url = REDIS_URL or "redis://localhost:6379"
    return StandardRedis.from_url(url, decode_responses=True)
