from __future__ import annotations
from typing import Any
from opensearchpy import OpenSearch, helpers
from .config import Settings

class OpenSearchVectorStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        client_options: dict[str, Any] = {
            "hosts": [{"host": settings.opensearch_host, "port": settings.opensearch_port}],
            "use_ssl": settings.opensearch_use_ssl,
            "verify_certs": settings.opensearch_verify_certs,
            "ssl_assert_hostname": False,
            "ssl_show_warn": False,
        }
        if settings.opensearch_user and settings.opensearch_password:
            client_options["http_auth"] = (settings.opensearch_user, settings.opensearch_password)

        self._client = OpenSearch(**client_options)

    @property
    def index_name(self) -> str:
        return self._settings.opensearch_index_name

    def index_exists(self) -> bool:
        return bool(self._client.indices.exists(index=self.index_name))

    def ensure_index(self, vector_dimension: int, recreate: bool = False) -> None:
        exists = self.index_exists()

        if exists and recreate:
            self._client.indices.delete(index=self.index_name)
            exists = False

        if exists:
            return

        body = {
            "settings": {
                "index": {
                    "knn": True,
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                }
            },
            "mappings": {
                "properties": {
                    "path": {"type": "keyword"},
                    "filename": {"type": "keyword"},
                    "chunk_id": {"type": "keyword"},
                    "text": {"type": "text"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": vector_dimension,
                    },
                }
            },
        }

        self._client.indices.create(index=self.index_name, body=body)

    def get_index_vector_dimension(self) -> int | None:
        if not self.index_exists():
            return None

        mapping = self._client.indices.get_mapping(index=self.index_name)
        index_mapping = mapping.get(self.index_name, {})
        properties = index_mapping.get("mappings", {}).get("properties", {})
        embedding = properties.get("embedding", {})
        dimension = embedding.get("dimension")
        if isinstance(dimension, int):
            return dimension
        return None

    def remove_all_documents(self) -> int:
        if not self.index_exists():
            return 0

        response = self._client.delete_by_query(
            index=self.index_name,
            body={"query": {"match_all": {}}},
            conflicts="proceed",
            refresh=True,
            wait_for_completion=True,
        )
        deleted = response.get("deleted", 0)
        return int(deleted) if isinstance(deleted, int) else 0

    def delete_index(self) -> bool:
        if not self.index_exists():
            return False
        self._client.indices.delete(index=self.index_name)
        return True

    def bulk_index(self, docs: list[dict[str, Any]]) -> None:
        actions = [
            {
                "_index": self.index_name,
                "_id": doc["id"],
                "_source": {
                    "path": doc["path"],
                    "filename": doc["filename"],
                    "chunk_id": doc["chunk_id"],
                    "text": doc["text"],
                    "embedding": doc["embedding"],
                },
            }
            for doc in docs
        ]
        helpers.bulk(self._client, actions)

    def delete_by_paths(self, paths: list[str]) -> None:
        if not paths or not self.index_exists():
            return

        self._client.delete_by_query(
            index=self.index_name,
            body={
                "query": {
                    "terms": {
                        "path": paths,
                    }
                }
            },
            conflicts="proceed",
            refresh=True,
        )

    def search(self, query_vector: list[float], top_k: int) -> list[dict[str, Any]]:
        body = {
            "size": top_k,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": query_vector,
                        "k": top_k,
                    }
                }
            },
            "_source": ["path", "filename", "text", "chunk_id"],
        }
        response = self._client.search(index=self.index_name, body=body)
        hits = response.get("hits", {}).get("hits", [])
        return hits

    def get_indexed_paths(self) -> set[str]:
        if not self.index_exists():
            return set()

        indexed_paths: set[str] = set()
        after_key: dict[str, str] | None = None

        while True:
            composite: dict[str, Any] = {
                "size": 1000,
                "sources": [{"path": {"terms": {"field": "path"}}}],
            }
            if after_key is not None:
                composite["after"] = after_key

            body = {
                "size": 0,
                "aggs": {
                    "paths": {
                        "composite": composite,
                    }
                },
            }

            response = self._client.search(index=self.index_name, body=body)
            aggregation = response.get("aggregations", {}).get("paths", {})
            buckets = aggregation.get("buckets", [])
            if not buckets:
                break

            for bucket in buckets:
                key = bucket.get("key", {}).get("path")
                if isinstance(key, str) and key:
                    indexed_paths.add(key)

            next_after_key = aggregation.get("after_key")
            if not isinstance(next_after_key, dict):
                break
            after_key = next_after_key

        return indexed_paths

    def count_documents(self) -> int:
        if not self.index_exists():
            return 0

        response = self._client.count(index=self.index_name)
        count = response.get("count", 0)
        return int(count) if isinstance(count, int) else 0
