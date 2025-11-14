"""Cosmos DB client for storing conversation transcripts."""

import logging
import os
from datetime import datetime
from typing import Dict, List, Any, Optional
from azure.cosmos import CosmosClient, exceptions

logger = logging.getLogger(__name__)


class ConversationCosmosClient:
    """Client for storing and retrieving conversation transcripts in Cosmos DB."""

    def __init__(self, config: Dict[str, str]):
        """Initialize the Cosmos DB client with configuration."""
        self.endpoint = config.get("COSMOS_DB_ENDPOINT", "")
        self.key = config.get("COSMOS_DB_KEY", "")
        self.database_name = config.get("COSMOS_DB_DATABASE_NAME", "conversationdb")
        self.container_name = config.get("COSMOS_DB_CONTAINER_NAME", "transcripts")
        
        if not self.endpoint or not self.key:
            logger.warning("Cosmos DB configuration not found. Transcript storage will be disabled.")
            self.client = None
            self.container = None
            return
            
        try:
            self.client = CosmosClient(self.endpoint, self.key)
            self.database = self.client.get_database_client(self.database_name)
            self.container = self.database.get_container_client(self.container_name)
            logger.info("Cosmos DB client initialized successfully")
        except Exception as e:
            logger.exception("Failed to initialize Cosmos DB client: %s", e)
            self.client = None
            self.container = None

    async def store_conversation(self, session_id: str, transcripts: List[Dict[str, Any]]) -> bool:
        """Store a complete conversation transcript in Cosmos DB."""
        if not self.container:
            logger.warning("Cosmos DB client not available. Skipping transcript storage.")
            return False

        try:
            # Create a document for the conversation
            conversation_doc = {
                "id": session_id,
                "sessionId": session_id,  # Partition key
                "timestamp": datetime.utcnow().isoformat(),
                "transcripts": transcripts,
                "conversationStart": min(t.get("timestamp", "") for t in transcripts) if transcripts else datetime.utcnow().isoformat(),
                "conversationEnd": max(t.get("timestamp", "") for t in transcripts) if transcripts else datetime.utcnow().isoformat(),
                "messageCount": len(transcripts),
                "userMessages": len([t for t in transcripts if t.get("role") == "user"]),
                "assistantMessages": len([t for t in transcripts if t.get("role") == "assistant"])
            }

            # Upsert the document (insert or update if exists)
            self.container.upsert_item(conversation_doc)
            logger.info("Successfully stored conversation %s with %d messages", session_id, len(transcripts))
            return True

        except exceptions.CosmosHttpResponseError as e:
            logger.error("Cosmos DB error storing conversation %s: %s", session_id, e)
            return False
        except Exception as e:
            logger.exception("Unexpected error storing conversation %s: %s", session_id, e)
            return False

    async def get_conversation(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a conversation by session ID."""
        if not self.container:
            logger.warning("Cosmos DB client not available.")
            return None

        try:
            item = self.container.read_item(
                item=session_id,
                partition_key=session_id
            )
            logger.info("Retrieved conversation %s", session_id)
            return item
        except exceptions.CosmosResourceNotFoundError:
            logger.info("Conversation %s not found", session_id)
            return None
        except Exception as e:
            logger.exception("Error retrieving conversation %s: %s", session_id, e)
            return None

    async def list_conversations(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List recent conversations."""
        if not self.container:
            logger.warning("Cosmos DB client not available.")
            return []

        try:
            query = "SELECT * FROM c ORDER BY c.timestamp DESC"
            items = list(self.container.query_items(
                query=query,
                max_item_count=limit,
                enable_cross_partition_query=True
            ))
            logger.info("Retrieved %d conversations", len(items))
            return items
        except Exception as e:
            logger.exception("Error listing conversations: %s", e)
            return []

    async def delete_conversation(self, session_id: str) -> bool:
        """Delete a conversation by session ID."""
        if not self.container:
            logger.warning("Cosmos DB client not available.")
            return False

        try:
            self.container.delete_item(
                item=session_id,
                partition_key=session_id
            )
            logger.info("Deleted conversation %s", session_id)
            return True
        except exceptions.CosmosResourceNotFoundError:
            logger.info("Conversation %s not found for deletion", session_id)
            return False
        except Exception as e:
            logger.exception("Error deleting conversation %s: %s", session_id, e)
            return False

    def is_available(self) -> bool:
        """Check if Cosmos DB client is available."""
        return self.container is not None
